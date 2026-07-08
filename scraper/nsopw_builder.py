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
# Search hits Cloudflare on nsopw-api — keep higher.
# Report pages are per-jurisdiction and can be faster (HTML save is the same request).
DEFAULT_SEARCH_DELAY = 3.0
DEFAULT_REPORT_DELAY = 0.75
DEFAULT_MIN_SEARCH_INTERVAL = 2.0
DEFAULT_MIN_REPORT_INTERVAL = 0.25


@dataclass
class StateReportStats:
    hits: int = 0
    reports_attempted: int = 0
    reports_ok: int = 0
    with_race: int = 0
    html_saved: int = 0
    blocks: Dict[str, int] = field(default_factory=dict)
    errors: int = 0


@dataclass
class BuildStats:
    searches: int = 0
    searches_skipped: int = 0
    search_hits: int = 0
    unique_offenders: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_existing: int = 0
    reports_fetched: int = 0
    reports_skipped_existing_file: int = 0
    reports_with_demographics: int = 0
    reports_with_race: int = 0
    html_saved: int = 0
    errors: List[str] = field(default_factory=list)
    by_state: Dict[str, StateReportStats] = field(default_factory=dict)


class RateLimiter:
    """Minimum interval between *starts* of operations (caller waits then works)."""

    def __init__(self, min_interval: float):
        self.min_interval = max(0.0, float(min_interval))
        self._last = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            self._last = time.monotonic()
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
        # Clients sleep themselves only if >0; builder RateLimiters are primary.
        client_owned_delay: bool = False,
    ):
        self.db = Database(db_path)
        self.ethnic_db = get_ethnic_database()
        search_delay = max(DEFAULT_MIN_SEARCH_INTERVAL, float(delay))
        report_delay = max(DEFAULT_MIN_REPORT_INTERVAL, float(report_delay))
        self.search_delay = search_delay
        self.report_delay = report_delay
        # Avoid double-delay: either builder limiter OR client sleep, not both.
        client_search_sleep = search_delay if client_owned_delay else 0.0
        client_report_sleep = report_delay if client_owned_delay else 0.0
        self.client = NSOPWClient(delay=client_search_sleep)
        self.reports = ReportFetcher(delay=client_report_sleep)
        self.search_limiter = RateLimiter(0.0 if client_owned_delay else search_delay)
        self.report_limiter = RateLimiter(0.0 if client_owned_delay else report_delay)
        self.html_dir = Path(html_dir)
        self.html_dir.mkdir(parents=True, exist_ok=True)
        self.cancel_check = cancel_check or (lambda: False)
        self.stats = BuildStats()
        self._ensure_query_log()

    def close(self) -> None:
        self.client.close()
        self.reports.close()
        self.db.close()

    def _ensure_query_log(self) -> None:
        """Track completed NSOPW (first, surname) queries for resume support."""
        self.db._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nsopw_query_log (
                first_prefix TEXT NOT NULL,
                surname TEXT NOT NULL,
                ethnicity TEXT NOT NULL DEFAULT '',
                completed_at TEXT NOT NULL,
                hit_count INTEGER DEFAULT 0,
                PRIMARY KEY (first_prefix, surname, ethnicity)
            )
            """
        )
        self.db._conn.commit()

    def _state_stats(self, state: str) -> StateReportStats:
        key = (state or "UNK").upper()[:12] or "UNK"
        if key not in self.stats.by_state:
            self.stats.by_state[key] = StateReportStats()
        return self.stats.by_state[key]

    def _query_done(self, first: str, surname: str, ethnicity: str) -> bool:
        row = self.db._conn.execute(
            """
            SELECT 1 FROM nsopw_query_log
            WHERE first_prefix = ? AND surname = ? AND ethnicity = ?
            LIMIT 1
            """,
            (first.strip().upper(), surname.strip().lower(), (ethnicity or "").lower()),
        ).fetchone()
        return row is not None

    def _mark_query_done(
        self, first: str, surname: str, ethnicity: str, hit_count: int = 0
    ) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.db._conn.execute(
            """
            INSERT INTO nsopw_query_log (first_prefix, surname, ethnicity, completed_at, hit_count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(first_prefix, surname, ethnicity) DO UPDATE SET
                completed_at = excluded.completed_at,
                hit_count = excluded.hit_count
            """,
            (
                first.strip().upper(),
                surname.strip().lower(),
                (ethnicity or "").lower(),
                now,
                int(hit_count),
            ),
        )
        self.db._conn.commit()

    @staticmethod
    def _html_path_for(url: str, html_dir: Path, jurisdiction: str) -> Path:
        jur = re.sub(r"[^A-Za-z0-9_-]", "", (jurisdiction or "UNK").upper())[:12] or "UNK"
        digest = sha1(url.encode("utf-8", errors="replace")).hexdigest()[:16]
        return Path(html_dir) / jur / f"{digest}.html"

    def _existing_html_path(self, url: str, jurisdiction: str) -> Optional[str]:
        """Return local HTML path if already archived for this URL."""
        if not url:
            return None
        # Prefer DB path if present and file exists
        row = self.db._conn.execute(
            """
            SELECT report_html_path FROM offenders
            WHERE source_url = ? AND report_html_path IS NOT NULL AND report_html_path != ''
            LIMIT 1
            """,
            (url,),
        ).fetchone()
        if row and row["report_html_path"]:
            p = Path(row["report_html_path"])
            if p.is_file() and p.stat().st_size > 100:
                return str(p)
        # Digest path used by ReportFetcher (original URL)
        candidate = self._html_path_for(url, self.html_dir, jurisdiction)
        if candidate.is_file() and candidate.stat().st_size > 100:
            try:
                return str(candidate.relative_to(Path.cwd()))
            except ValueError:
                return str(candidate)
        return None

    def surnames_for_ethnicity(
        self,
        ethnicity: str = "all",
        limit_per_group: int = 15,
        all_surnames: bool = False,
    ) -> List[Tuple[str, str]]:
        """Return list of (surname, ethnicity_label) from the ethnic name DB."""
        eth = (ethnicity or "all").lower().strip()
        pairs: List[Tuple[str, str]] = []
        # all_surnames / limit<=0 → no per-group cap
        unlimited = all_surnames or limit_per_group is None or int(limit_per_group) <= 0
        cap = 10**9 if unlimited else max(1, int(limit_per_group))

        def take(names: Iterable[str], label: str, n: int) -> None:
            for name in sorted(names, key=lambda x: x.lower())[:n]:
                if name and name.strip():
                    pairs.append((name.strip(), label))

        if eth in ("all", "hispanic"):
            take(self.ethnic_db.hispanic_surnames, "Hispanic", cap)
        # East / Southeast Asian only (not Indian / South Asian)
        if eth in ("all", "asian"):
            for group, names in sorted(self.ethnic_db.asian_surnames.items()):
                n = cap if unlimited else max(3, cap // 3)
                take(names, f"Asian ({group})", n)
        # Indian subcontinent / South Asian (separate list; optional regional groups)
        if eth in ("all", "indian"):
            by_group = getattr(self.ethnic_db, "indian_surnames_by_group", None) or {}
            if by_group:
                for group, names in sorted(by_group.items()):
                    n = cap if unlimited else max(3, cap // 3)
                    take(names, f"Indian ({group})", n)
            else:
                take(self.ethnic_db.indian_surnames, "Indian", cap)
        if eth in ("all", "african_american"):
            take(self.ethnic_db.african_american_surnames, "African American", cap)
        if eth in ("all", "arabic"):
            take(self.ethnic_db.arabic_surnames, "Arabic", cap)
        if eth in ("all", "jewish"):
            take(self.ethnic_db.jewish_surnames, "Jewish", cap)
        if eth in ("all", "portuguese"):
            take(self.ethnic_db.portuguese_surnames, "Portuguese", cap)
        if eth in ("all", "native_american"):
            take(self.ethnic_db.native_american_surnames, "Native American", cap)
        if eth in ("all", "european"):
            for country, names in sorted(self.ethnic_db.european_surnames.items()):
                n = cap if unlimited else max(2, cap // 4)
                take(names, f"European ({country})", n)

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
        all_surnames: bool = False,
        first_names: Optional[Sequence[str]] = None,
        first_mode: str = "initials",
        jurisdictions: Optional[Sequence[str]] = None,
        max_searches: int = 50,
        max_report_fetches: int = 100,
        skip_existing_urls: bool = True,
        skip_completed_searches: bool = True,
        new_files_only: bool = True,
        enrich_reports: bool = True,
        save_html: bool = True,
        log: Optional[Callable[[str], None]] = None,
        on_insert: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> BuildStats:
        """
        Run the ethnic-name NSOPW search pipeline.

        first_mode:
          - "initials" (default): A–Z single-letter prefixes (partial first-name match)
          - "full": use DEFAULT_FIRST_NAMES or provided first_names list
          - "custom": only the provided first_names list

        skip_completed_searches:
          Resume mode — skip (first, surname) pairs already in nsopw_query_log.
        new_files_only:
          Skip report HTTP download when local HTML already exists for that URL.
        all_surnames:
          Ignore surnames_limit and use every name in the selected list(s).

        on_insert: optional callback with the stored record after each successful insert
        (used by the GUI for live Recent inserts).
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
            firsts = list(FIRST_INITIALS)

        if not firsts:
            firsts = list(FIRST_INITIALS)

        jurs = list(jurisdictions) if jurisdictions else list(DEFAULT_JURISDICTIONS)
        surname_pairs = self.surnames_for_ethnicity(
            ethnicity,
            limit_per_group=surnames_limit,
            all_surnames=all_surnames,
        )
        eth_key = (ethnicity or "").lower()

        _log(f"Ethnicity filter: {ethnicity}")
        _log(
            f"Surnames to search: {len(surname_pairs)}"
            + (" (ALL in list)" if all_surnames or surnames_limit <= 0 else f" (cap {surnames_limit}/group)")
        )
        _log(f"First-name mode: {mode} ({len(firsts)} prefixes/names)")
        _log(f"  Prefixes: {', '.join(firsts[:12])}{'…' if len(firsts) > 12 else ''}")
        _log(f"Jurisdictions: {len(jurs)}")
        _log(f"Max new searches: {max_searches}, max report fetches: {max_report_fetches}")
        _log(
            f"Rate limits — search: {self.search_delay:.2f}s  |  "
            f"report/HTML: {self.report_delay:.2f}s  "
            f"(search is slower: Cloudflare on nsopw-api)"
        )
        _log(f"Resume/skip completed searches: {skip_completed_searches}")
        _log(f"Skip known URLs in DB: {skip_existing_urls}")
        _log(f"New report files only (no re-download): {new_files_only}")
        _log(f"Save report HTML: {save_html} → {self.html_dir}")
        _log(f"Enrich demographics: {enrich_reports}")
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

                # Resume: skip API queries already completed successfully
                if skip_completed_searches and self._query_done(first, surname, eth_key):
                    self.stats.searches_skipped += 1
                    _log(f"  Skip completed search: '{first}' {surname}")
                    continue

                search_count += 1
                self.stats.searches = search_count
                self.search_limiter.wait()
                _log(
                    f"[{search_count}/{max_searches}] NSOPW: '{first}' {surname} ({eth_label})"
                )
                try:
                    hits = self.client.search_by_name(first, surname, jurisdictions=jurs)
                except Exception as e:
                    msg = f"  Search error: {e}"
                    self.stats.errors.append(msg)
                    _log(msg)
                    # Do not mark complete — resume will retry
                    continue

                self._mark_query_done(first, surname, eth_key, hit_count=len(hits))
                self.stats.search_hits += len(hits)
                sample_firsts = sorted({(h.first_name or "?") for h in hits})[:8]
                _log(
                    f"  Hits: {len(hits)}"
                    + (f"  first-names: {', '.join(sample_firsts)}" if sample_firsts else "")
                )

                for hit in hits:
                    if self.cancel_check():
                        break
                    st = (hit.jurisdiction_id or hit.state or "UNK").upper()
                    self._state_stats(st).hits += 1

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
                        existing_html = (
                            self._existing_html_path(url, st) if new_files_only else None
                        )
                        if existing_html:
                            self.stats.reports_skipped_existing_file += 1
                            record["report_html_path"] = existing_html
                            flags_list = json.loads(record["flags"])
                            flags_list.append("html_cached")
                            record["flags"] = json.dumps(flags_list)
                            _log(f"  Report skip (local HTML): {existing_html}")
                        else:
                            report_count += 1
                            self.stats.reports_fetched = report_count
                            sst = self._state_stats(st)
                            sst.reports_attempted += 1
                            self.report_limiter.wait()
                            _log(
                                f"  Report ({report_count}/{max_report_fetches}) "
                                f"[{st}]: {url[:90]}"
                            )
                            demo = self.reports.fetch_demographics(
                                url,
                                save_html=save_html,
                                html_dir=self.html_dir,
                                jurisdiction=st,
                            )
                            self._merge_demographics(record, demo)
                            if demo.get("report_fetch_ok"):
                                sst.reports_ok += 1
                            if demo.get("race"):
                                self.stats.reports_with_race += 1
                                sst.with_race += 1
                            if demo.get("race") or demo.get("ethnicity"):
                                self.stats.reports_with_demographics += 1
                            if demo.get("report_html_path"):
                                record["report_html_path"] = demo["report_html_path"]
                                self.stats.html_saved += 1
                                sst.html_saved += 1
                            block = demo.get("report_block_reason") or ""
                            status = str(demo.get("report_fetch_status") or "")
                            if block or status.startswith("blocked:") or status.startswith("error:"):
                                reason = block or status
                                sst.blocks[reason] = sst.blocks.get(reason, 0) + 1
                                if status.startswith("error:"):
                                    sst.errors += 1
                            if not demo.get("report_fetch_ok"):
                                _log(
                                    f"    ↳ no demographics "
                                    f"(status={demo.get('report_fetch_status')}"
                                    f"{', ' + block if block else ''})"
                                )
                            else:
                                _log(
                                    f"    ↳ race={demo.get('race') or '—'} "
                                    f"eth={demo.get('ethnicity') or '—'} "
                                    f"gender={demo.get('gender') or '—'}"
                                )
                            record["source_url"] = demo.get("report_final_url") or url
                    elif save_html and url and not enrich_reports:
                        pass

                    if url:
                        record["source_url"] = record.get("source_url") or url

                    try:
                        self.db.insert_offender(record)
                        self.stats.inserted += 1
                        if on_insert:
                            try:
                                on_insert(dict(record))
                            except Exception:
                                pass
                    except Exception as e:
                        msg = f"  Insert error: {e}"
                        self.stats.errors.append(msg)
                        _log(msg)

        _log("")
        _log("=== Build complete ===")
        _log(f"Searches (new):        {self.stats.searches}")
        _log(f"Searches skipped:      {self.stats.searches_skipped} (already completed)")
        _log(f"Raw hits:              {self.stats.search_hits}")
        _log(f"Unique offenders:      {self.stats.unique_offenders}")
        _log(f"Inserted:              {self.stats.inserted}")
        _log(f"Skipped existing URLs: {self.stats.skipped_existing}")
        _log(f"Reports fetched:       {self.stats.reports_fetched}")
        _log(f"Reports skipped HTML:  {self.stats.reports_skipped_existing_file}")
        _log(f"Reports with race:     {self.stats.reports_with_race}")
        _log(f"Reports with race/eth: {self.stats.reports_with_demographics}")
        _log(f"HTML pages saved:      {self.stats.html_saved}")
        _log(f"Errors:                {len(self.stats.errors)}")
        if self.stats.by_state:
            _log("")
            _log("Per-state report coverage (attempted → ok / race / html):")
            for st in sorted(self.stats.by_state.keys()):
                s = self.stats.by_state[st]
                if s.reports_attempted == 0 and s.hits == 0:
                    continue
                blocks = ""
                if s.blocks:
                    top = sorted(s.blocks.items(), key=lambda x: -x[1])[:2]
                    blocks = "  blocks=" + ",".join(f"{k}:{v}" for k, v in top)
                _log(
                    f"  {st:6} hits={s.hits:4}  reports={s.reports_attempted:3} "
                    f"ok={s.reports_ok:3}  race={s.with_race:3}  html={s.html_saved:3}"
                    f"{blocks}"
                )
            _log(
                "Note: iCrimeWatch/OffenderWatch disclaimers are auto-accepted when possible. "
                "NY reCAPTCHA and some WAF walls still cannot yield full sheets."
            )
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
                "report_url", "report_final_url", "report_resolved_url",
                "report_fetch_status", "report_fetch_ok", "report_html_path",
                "report_block_reason", "race", "ethnicity", "gender",
                "height", "weight", "hair_color", "eye_color",
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
        if demo.get("report_fetch_ok"):
            flags.append("report_enriched")
        else:
            flags.append("report_link_saved")
            if demo.get("report_block_reason"):
                flags.append(f"blocked:{demo['report_block_reason']}")
        record["flags"] = json.dumps(flags)
