"""
Build a local offender database by searching NSOPW for common ethnic surnames,
saving report links + archived HTML, and enriching demographics from report pages.

NSOPW name search accepts partial first names (e.g. first="M", last="Singh"
returns many given names beginning with M). Default mode uses A–Z initials to
minimize query count while maximizing coverage.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from hashlib import sha1
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .database import Database
from .ethnic_names import get_ethnic_database
from .nsopw_client import DEFAULT_JURISDICTIONS, NSOPWClient
from .report_fetcher import ReportFetcher

# Full first names (optional mode) — NSOPW requires first + last.
DEFAULT_FIRST_NAMES = [
    "John", "James", "Robert", "Michael", "David", "William", "Joseph", "Thomas",
    "Carlos", "Juan", "Jose", "Luis", "Miguel", "Maria", "Ana", "Rosa",
    "Wei", "Li", "Min", "Yong", "Jin",
    "Ahmed", "Mohamed", "Ali", "Omar",
]

# Single-letter prefixes: one search per letter covers partial first-name matches
FIRST_INITIALS = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

# Default rate limits (seconds / caps)
DEFAULT_SEARCH_DELAY = 2.0
DEFAULT_REPORT_DELAY = 2.0
DEFAULT_MIN_SEARCH_INTERVAL = 1.5  # hard floor even if UI sets lower
DEFAULT_MIN_REPORT_INTERVAL = 1.5


@dataclass
class BuildStats:
    searches: int = 0
    search_hits: int = 0
    unique_offenders: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_existing: int = 0
    reports_fetched: int = 0
    reports_with_demographics: int = 0
    html_saved: int = 0
    errors: List[str] = field(default_factory=list)


class RateLimiter:
    """Simple minimum-interval rate limiter."""

    def __init__(self, min_interval: float):
        self.min_interval = max(0.0, float(min_interval))
        self._last = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()


class NSOPWEthnicDatabaseBuilder:
    """
    Search NSOPW for surnames from the ethnic name lists, store hits + report
    URLs, archive report HTML locally, and pull demographics when possible.
    """

    def __init__(
        self,
        db_path: str = "data/offenders.db",
        delay: float = DEFAULT_SEARCH_DELAY,
        report_delay: float = DEFAULT_REPORT_DELAY,
        html_dir: str = "data/report_pages",
        cancel_check: Optional[Callable[[], bool]] = None,
    ):
        self.db = Database(db_path)
        self.ethnic_db = get_ethnic_database()
        # Enforce floor on delays to protect NSOPW
        search_delay = max(DEFAULT_MIN_SEARCH_INTERVAL, float(delay))
        report_delay = max(DEFAULT_MIN_REPORT_INTERVAL, float(report_delay))
        self.client = NSOPWClient(delay=search_delay)
        self.reports = ReportFetcher(delay=report_delay)
        self.search_limiter = RateLimiter(search_delay)
        self.report_limiter = RateLimiter(report_delay)
        self.html_dir = Path(html_dir)
        self.html_dir.mkdir(parents=True, exist_ok=True)
        self.cancel_check = cancel_check or (lambda: False)
        self.stats = BuildStats()

    def close(self) -> None:
        self.client.close()
        self.reports.close()
        self.db.close()

    def surnames_for_ethnicity(
        self,
        ethnicity: str = "all",
        limit_per_group: int = 15,
    ) -> List[Tuple[str, str]]:
        """Return list of (surname, ethnicity_label) from the ethnic name DB."""
        eth = (ethnicity or "all").lower().strip()
        pairs: List[Tuple[str, str]] = []

        def take(names: Iterable[str], label: str, n: int) -> None:
            for name in sorted(names, key=lambda x: x.lower())[:n]:
                if name and name.strip():
                    pairs.append((name.strip(), label))

        if eth in ("all", "hispanic"):
            take(self.ethnic_db.hispanic_surnames, "Hispanic", limit_per_group)
        if eth in ("all", "asian"):
            for group, names in sorted(self.ethnic_db.asian_surnames.items()):
                take(names, f"Asian ({group})", max(3, limit_per_group // 3))
        if eth in ("all", "african_american"):
            take(self.ethnic_db.african_american_surnames, "African American", limit_per_group)
        if eth in ("all", "arabic"):
            take(self.ethnic_db.arabic_surnames, "Arabic", limit_per_group)
        if eth in ("all", "jewish"):
            take(self.ethnic_db.jewish_surnames, "Jewish", limit_per_group)
        if eth in ("all", "portuguese"):
            take(self.ethnic_db.portuguese_surnames, "Portuguese", limit_per_group)
        if eth in ("all", "native_american"):
            take(self.ethnic_db.native_american_surnames, "Native American", limit_per_group)
        if eth in ("all", "european"):
            for country, names in sorted(self.ethnic_db.european_surnames.items()):
                take(names, f"European ({country})", max(2, limit_per_group // 4))

        seen: Set[str] = set()
        unique: List[Tuple[str, str]] = []
        for surname, label in pairs:
            key = surname.lower()
            if key not in seen:
                seen.add(key)
                unique.append((surname, label))
        return unique

    def build(
        self,
        ethnicity: str = "hispanic",
        surnames_limit: int = 10,
        first_names: Optional[Sequence[str]] = None,
        first_mode: str = "initials",
        jurisdictions: Optional[Sequence[str]] = None,
        max_searches: int = 50,
        max_report_fetches: int = 100,
        skip_existing_urls: bool = True,
        enrich_reports: bool = True,
        save_html: bool = True,
        log: Optional[Callable[[str], None]] = None,
    ) -> BuildStats:
        """
        Run the ethnic-name NSOPW search pipeline.

        first_mode:
          - "initials" (default): A–Z single-letter prefixes (partial first-name match)
          - "full": use DEFAULT_FIRST_NAMES or provided first_names list
          - "custom": only the provided first_names list
        """
        def _log(msg: str) -> None:
            if log:
                log(msg)
            else:
                print(msg)

        mode = (first_mode or "initials").lower().strip()
        if first_names is not None:
            firsts = [f.strip() for f in first_names if f and str(f).strip()]
        elif mode == "full":
            firsts = list(DEFAULT_FIRST_NAMES)
        else:
            # Default: A–Z initials — leverages NSOPW partial first-name matching
            firsts = list(FIRST_INITIALS)

        if not firsts:
            firsts = list(FIRST_INITIALS)

        jurs = list(jurisdictions) if jurisdictions else list(DEFAULT_JURISDICTIONS)
        surname_pairs = self.surnames_for_ethnicity(ethnicity, limit_per_group=surnames_limit)

        _log(f"Ethnicity filter: {ethnicity}")
        _log(f"Surnames to search: {len(surname_pairs)}")
        _log(f"First-name mode: {mode} ({len(firsts)} prefixes/names)")
        _log(f"  Prefixes: {', '.join(firsts[:12])}{'…' if len(firsts) > 12 else ''}")
        _log(f"Jurisdictions: {len(jurs)}")
        _log(f"Max searches: {max_searches}, max report fetches: {max_report_fetches}")
        _log(f"Search delay ≥ {self.search_limiter.min_interval}s | "
             f"Report delay ≥ {self.report_limiter.min_interval}s")
        _log(f"Save report HTML: {save_html} → {self.html_dir}")
        _log("NSOPW Conditions of Use apply: https://www.nsopw.gov/")
        _log("Partial first names (e.g. 'M' + surname) expand to matching given names.")
        _log("")

        seen_urls: Set[str] = set()
        search_count = 0
        report_count = 0

        for surname, eth_label in surname_pairs:
            if self.cancel_check():
                _log("Cancelled by user.")
                break
            if search_count >= max_searches:
                break
            for first in firsts:
                if self.cancel_check():
                    _log("Cancelled by user.")
                    break
                if search_count >= max_searches:
                    break

                search_count += 1
                self.stats.searches = search_count
                self.search_limiter.wait()
                _log(f"[{search_count}/{max_searches}] NSOPW: '{first}' {surname} ({eth_label})")
                try:
                    hits = self.client.search_by_name(first, surname, jurisdictions=jurs)
                except Exception as e:
                    msg = f"  Search error: {e}"
                    self.stats.errors.append(msg)
                    _log(msg)
                    continue

                self.stats.search_hits += len(hits)
                # Show how partial expand worked
                sample_firsts = sorted({(h.first_name or "?") for h in hits})[:8]
                _log(f"  Hits: {len(hits)}"
                     + (f"  first-names: {', '.join(sample_firsts)}" if sample_firsts else ""))

                for hit in hits:
                    if self.cancel_check():
                        break
                    url = (hit.offender_uri or "").strip()
                    dedupe_key = url or f"{hit.jurisdiction_id}:{hit.full_name}:{hit.date_of_birth}"
                    if dedupe_key in seen_urls:
                        continue
                    seen_urls.add(dedupe_key)
                    self.stats.unique_offenders += 1

                    record = hit.to_record()
                    record["likely_ethnicity"] = eth_label
                    conf_eth, conf = self.ethnic_db.get_likely_ethnicity(hit.last_name or surname)
                    record["name_confidence"] = conf
                    if conf_eth and conf_eth != "Unknown":
                        record["likely_ethnicity"] = conf_eth

                    flags = [
                        "nsopw",
                        f"search_surname:{surname}",
                        f"search_first:{first}",
                        f"first_mode:{mode}",
                    ]
                    record["flags"] = json.dumps(flags)

                    if skip_existing_urls and url and self._url_exists(url):
                        self.stats.skipped_existing += 1
                        continue

                    if enrich_reports and url and report_count < max_report_fetches:
                        report_count += 1
                        self.stats.reports_fetched = report_count
                        self.report_limiter.wait()
                        _log(f"  Report ({report_count}/{max_report_fetches}): {url[:90]}")
                        demo = self.reports.fetch_demographics(
                            url,
                            save_html=save_html,
                            html_dir=self.html_dir,
                            jurisdiction=hit.jurisdiction_id or hit.state or "UNK",
                        )
                        self._merge_demographics(record, demo)
                        if demo.get("race") or demo.get("ethnicity"):
                            self.stats.reports_with_demographics += 1
                        if demo.get("report_html_path"):
                            record["report_html_path"] = demo["report_html_path"]
                            self.stats.html_saved += 1
                        record["source_url"] = demo.get("report_final_url") or url
                    elif save_html and url and not enrich_reports:
                        # Still allow link-only mode without fetch
                        pass

                    if url:
                        record["source_url"] = record.get("source_url") or url

                    try:
                        self.db.insert_offender(record)
                        self.stats.inserted += 1
                    except Exception as e:
                        msg = f"  Insert error: {e}"
                        self.stats.errors.append(msg)
                        _log(msg)

        _log("")
        _log("=== Build complete ===")
        _log(f"Searches:              {self.stats.searches}")
        _log(f"Raw hits:              {self.stats.search_hits}")
        _log(f"Unique offenders:      {self.stats.unique_offenders}")
        _log(f"Inserted:              {self.stats.inserted}")
        _log(f"Skipped existing:      {self.stats.skipped_existing}")
        _log(f"Reports fetched:       {self.stats.reports_fetched}")
        _log(f"Reports with race/eth: {self.stats.reports_with_demographics}")
        _log(f"HTML pages saved:      {self.stats.html_saved}")
        _log(f"Errors:                {len(self.stats.errors)}")
        return self.stats

    def _url_exists(self, url: str) -> bool:
        row = self.db._conn.execute(
            "SELECT 1 FROM offenders WHERE source_url = ? LIMIT 1",
            (url,),
        ).fetchone()
        return row is not None

    def _merge_demographics(self, record: Dict[str, Any], demo: Dict[str, Any]) -> None:
        for key in (
            "race", "ethnicity", "gender", "height", "weight",
            "eye_color", "hair_color", "skin_tone", "build", "age",
            "date_of_birth", "county", "city", "address", "risk_level",
            "offense_type", "offense_description",
        ):
            val = demo.get(key)
            if val is None or val == "":
                continue
            if key in ("race", "ethnicity"):
                record[key] = val
            elif not record.get(key):
                record[key] = val

        try:
            raw = json.loads(record.get("raw_data_json") or "{}")
        except json.JSONDecodeError:
            raw = {}
        raw["report_enrichment"] = {
            k: demo.get(k)
            for k in (
                "report_url", "report_final_url", "report_fetch_status",
                "report_fetch_ok", "report_html_path", "race", "ethnicity",
                "gender", "height", "weight", "hair_color", "eye_color",
            )
            if k in demo
        }
        record["raw_data_json"] = json.dumps(raw, ensure_ascii=False)[:50000]

        try:
            flags = json.loads(record.get("flags") or "[]")
            if not isinstance(flags, list):
                flags = [str(flags)]
        except json.JSONDecodeError:
            flags = []
        if demo.get("report_html_path"):
            flags.append("html_archived")
        flags.append("report_enriched" if demo.get("report_fetch_ok") else "report_link_saved")
        record["flags"] = json.dumps(flags)
