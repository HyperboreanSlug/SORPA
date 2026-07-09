"""
Build a local offender database by searching NSOPW for common ethnic surnames,
saving report links + archived HTML, and enriching demographics from report pages.

NSOPW name search accepts partial first *and* last names. Combined first+last
must be at least 3 characters (e.g. first="M", last="AH" matches Mohamed Ahmed).
Default mode uses A–Z first initials + shortest valid last-name prefixes, then
collapses surnames that share a prefix so one query covers many list names.
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

# NSOPW API: len(firstName) + len(lastName) >= 3 (verified live).
MIN_COMBINED_NAME_LEN = 3

# Default rate limits (seconds / caps)
# Search hits Cloudflare on nsopw-api — keep higher.
# Report pages are per-jurisdiction and can be faster (HTML save is the same request).
DEFAULT_SEARCH_DELAY = 3.0
DEFAULT_REPORT_DELAY = 0.75
DEFAULT_MIN_SEARCH_INTERVAL = 2.0
DEFAULT_MIN_REPORT_INTERVAL = 0.25


def last_name_search_prefix(
    surname: str,
    first: str,
    min_combined: int = MIN_COMBINED_NAME_LEN,
) -> str:
    """
    Shortest last-name token valid with this first name under NSOPW's min length.

    With first="M" (1 char), last needs 2 chars → "AH" for Ahmed/Ahmad.
    With first="MO" (2), last needs 1 char → "A".
    """
    first_s = (first or "").strip()
    last_s = (surname or "").strip()
    if not last_s:
        return last_s
    need = max(1, int(min_combined) - len(first_s))
    if len(last_s) <= need:
        return last_s
    return last_s[:need]


def last_matches_target_surnames(last_name: str, targets: Sequence[str]) -> bool:
    """
    True if hit last name is covered by any full list surname (prefix match).

    Keeps broad short-prefix searches from inserting unrelated last names
    (e.g. search last='AH' for Ahmed/Ahmad should not keep Ahern).
    """
    last = (last_name or "").strip().lower()
    if not last:
        return False
    for t in targets:
        tl = (t or "").strip().lower()
        if not tl:
            continue
        if last == tl or last.startswith(tl):
            return True
    return False


def compact_search_plan(
    surname_pairs: Sequence[Tuple[str, str]],
    firsts: Sequence[str],
    min_combined: int = MIN_COMBINED_NAME_LEN,
) -> List[Tuple[str, str, str, List[str]]]:
    """
    Collapse (first, full_surname) into unique (first, last_prefix) queries.

    Returns list of (first, last_prefix, eth_label, covered_full_surnames).
    When several list surnames share a short last prefix (Ahmed/Ahmad → AH),
    one NSOPW query covers them all.
    """
    # key: (first_norm, last_prefix_norm) -> first, last_prefix, eth, surnames
    first_disp: Dict[Tuple[str, str], str] = {}
    prefix_disp: Dict[Tuple[str, str], str] = {}
    eth_disp: Dict[Tuple[str, str], str] = {}
    covered: Dict[Tuple[str, str], Set[str]] = {}

    for surname, eth_label in surname_pairs:
        sn = (surname or "").strip()
        if not sn:
            continue
        eth = eth_label or ""
        for first in firsts:
            fn = (first or "").strip()
            if not fn:
                continue
            prefix = last_name_search_prefix(sn, fn, min_combined=min_combined)
            if len(fn) + len(prefix) < min_combined:
                prefix = sn
                if len(fn) + len(prefix) < min_combined:
                    continue
            key = (fn.upper(), prefix.upper())
            first_disp[key] = fn
            prefix_disp[key] = prefix
            if eth and not eth_disp.get(key):
                eth_disp[key] = eth
            elif key not in eth_disp:
                eth_disp[key] = eth
            covered.setdefault(key, set()).add(sn)

    plan: List[Tuple[str, str, str, List[str]]] = []
    for key in covered:
        plan.append(
            (
                first_disp[key],
                prefix_disp[key],
                eth_disp.get(key, ""),
                sorted(covered[key], key=str.lower),
            )
        )
    plan.sort(key=lambda t: (t[1].upper(), t[0].upper()))
    return plan


def estimate_compact_query_count(
    surname_pairs: Sequence[Tuple[str, str]],
    firsts: Sequence[str] | None = None,
    min_combined: int = MIN_COMBINED_NAME_LEN,
) -> int:
    """Estimate unique NSOPW queries after short-prefix collapse."""
    firsts = list(firsts) if firsts is not None else list(FIRST_INITIALS)
    return len(compact_search_plan(surname_pairs, firsts, min_combined=min_combined))


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
    search_hits_matched: int = 0
    search_hits_other: int = 0
    unique_offenders: int = 0
    inserted: int = 0
    inserted_matched: int = 0
    inserted_other: int = 0
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
        subcategory: Optional[str] = None,
    ) -> List[Tuple[str, str]]:
        """
        Return list of (surname, ethnicity_label) from the ethnic name DB.

        subcategory: when set (and not 'all'), only that nested group is used
        for asian / indian / european / african lists.
        """
        eth = (ethnicity or "all").lower().strip()
        sub = (subcategory or "all").lower().strip()
        if sub in ("", "all", "(all)", "none", "*"):
            sub = ""
        pairs: List[Tuple[str, str]] = []
        # all_surnames / limit<=0 → no per-group cap
        unlimited = all_surnames or limit_per_group is None or int(limit_per_group) <= 0
        cap = 10**9 if unlimited else max(1, int(limit_per_group))

        def take(names: Iterable[str], label: str, n: int) -> None:
            for name in sorted(names, key=lambda x: x.lower())[:n]:
                if name and name.strip():
                    pairs.append((name.strip(), label))

        def group_cap() -> int:
            return cap if unlimited else max(3, cap // 3)

        if eth in ("all", "hispanic"):
            if not sub:  # flat list — no subcategory filter
                take(self.ethnic_db.hispanic_surnames, "Hispanic", cap)
        # East / Southeast Asian only (not Indian / South Asian)
        if eth in ("all", "asian"):
            for group, names in sorted(self.ethnic_db.asian_surnames.items()):
                if sub and group.lower() != sub:
                    continue
                take(names, f"Asian ({group})", group_cap())
        # Indian subcontinent / South Asian (separate list; optional regional groups)
        if eth in ("all", "indian"):
            by_group = getattr(self.ethnic_db, "indian_surnames_by_group", None) or {}
            if by_group:
                for group, names in sorted(by_group.items()):
                    if sub and group.lower() != sub:
                        continue
                    take(names, f"Indian ({group})", group_cap())
            elif not sub:
                take(self.ethnic_db.indian_surnames, "Indian", cap)
        if eth in ("all", "african_american") and not sub:
            take(self.ethnic_db.african_american_surnames, "African American", cap)
        if eth in ("all", "arabic") and not sub:
            take(self.ethnic_db.arabic_surnames, "Arabic", cap)
        if eth in ("all", "jewish") and not sub:
            take(self.ethnic_db.jewish_surnames, "Jewish", cap)
        if eth in ("all", "portuguese") and not sub:
            take(self.ethnic_db.portuguese_surnames, "Portuguese", cap)
        if eth in ("all", "native_american") and not sub:
            take(self.ethnic_db.native_american_surnames, "Native American", cap)
        if eth in ("all", "european"):
            for country, names in sorted(self.ethnic_db.european_surnames.items()):
                if sub and country.lower() != sub:
                    continue
                n = cap if unlimited else max(2, cap // 4)
                take(names, f"European ({country})", n)
        if eth in ("all", "african"):
            for region, names in sorted(self.ethnic_db.african_surnames.items()):
                if sub and region.lower() != sub:
                    continue
                take(names, f"African ({region})", group_cap())

        # When eth is a grouped family but subcategory was set under eth="all",
        # only the matching nested branch above contributes names.

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
        subcategory: Optional[str] = None,
        first_names: Optional[Sequence[str]] = None,
        first_mode: str = "initials",
        jurisdictions: Optional[Sequence[str]] = None,
        max_searches: Optional[int] = 50,
        max_report_fetches: Optional[int] = 100,
        max_names: Optional[int] = None,
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

        Short last-name prefixes (min combined first+last length 3) collapse many
        list surnames into fewer queries (e.g. M+AH covers Ahmed and Ahmad).

        max_searches:
          Cap on new NSOPW API queries. None or <= 0 means unlimited.
        max_names / max_report_fetches:
          Cap on unique offender names processed (GUI "Max reports" = max names).
          max_report_fetches is an alias kept for CLI compatibility.
          None or <= 0 means unlimited.

        skip_completed_searches:
          Resume mode — skip (first, last_prefix) pairs already in nsopw_query_log.
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

        def _cap(value: Optional[int]) -> Optional[int]:
            """Normalize limit: None / <=0 → unlimited (None)."""
            if value is None:
                return None
            try:
                n = int(value)
            except (TypeError, ValueError):
                return None
            return None if n <= 0 else n

        search_cap = _cap(max_searches)
        # "Max reports" in the GUI means max unique names, not HTTP report fetches.
        # Prefer explicit max_names; fall back to max_report_fetches for CLI.
        names_cap = _cap(max_names if max_names is not None else max_report_fetches)

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
            subcategory=subcategory,
        )
        eth_key = (ethnicity or "").lower()
        sub_disp = (subcategory or "all").strip() or "all"

        # Collapse to short last prefixes (first+last ≥ 3 chars) for fewer API calls
        search_plan = compact_search_plan(surname_pairs, firsts)
        naive_queries = len(surname_pairs) * len(firsts)
        compact_queries = len(search_plan)
        # Full selected ethnicity surname list — used to bucket hits matched vs other
        eth_surnames_list = [s for s, _ in surname_pairs]

        _log(f"Ethnicity filter: {ethnicity}")
        _log(f"Subcategory: {sub_disp}")
        _log(
            f"Surnames in list: {len(surname_pairs)}"
            + (" (ALL in list)" if all_surnames or surnames_limit <= 0 else f" (cap {surnames_limit}/group)")
        )
        _log(
            "Result bucketing: ethnicity-list surnames → primary tab; "
            "other surnames from the same queries are still saved → other tab."
        )
        _log(f"First-name mode: {mode} ({len(firsts)} prefixes/names)")
        _log(f"  Prefixes: {', '.join(firsts[:12])}{'…' if len(firsts) > 12 else ''}")
        _log(
            f"Compact queries: {compact_queries:,} "
            f"(vs {naive_queries:,} full surname×first; "
            f"short last prefixes, min combined {MIN_COMBINED_NAME_LEN} letters)"
        )
        _log(f"Jurisdictions: {len(jurs)}")
        _log(
            f"Max new searches: {'unlimited' if search_cap is None else search_cap}, "
            f"max names: {'unlimited' if names_cap is None else names_cap}"
        )
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
        _log(
            "Partial names: e.g. first='M' last='AH' matches Mohamed Ahmed "
            f"(API min combined length {MIN_COMBINED_NAME_LEN})."
        )
        _log("")

        seen_urls: Set[str] = set()
        search_count = 0
        report_count = 0
        names_processed = 0  # unique names after dedupe (counts toward max names)

        def _search_limit_reached() -> bool:
            return search_cap is not None and search_count >= search_cap

        def _names_limit_reached() -> bool:
            return names_cap is not None and names_processed >= names_cap

        for first, last_token, eth_label, covered_surnames in search_plan:
            if self.cancel_check():
                _log("Cancelled by user.")
                break
            if _search_limit_reached() or _names_limit_reached():
                break

            # Resume: skip API queries already completed successfully
            if skip_completed_searches and self._query_done(first, last_token, eth_key):
                self.stats.searches_skipped += 1
                cov = ",".join(covered_surnames[:4])
                if len(covered_surnames) > 4:
                    cov += f"…(+{len(covered_surnames) - 4})"
                _log(f"  Skip completed search: '{first}' {last_token} [{cov}]")
                continue

            search_count += 1
            self.stats.searches = search_count
            self.search_limiter.wait()
            cap_label = "∞" if search_cap is None else str(search_cap)
            cov = ",".join(covered_surnames[:4])
            if len(covered_surnames) > 4:
                cov += f"…(+{len(covered_surnames) - 4})"
            _log(
                f"[{search_count}/{cap_label}] NSOPW: '{first}' {last_token} "
                f"({eth_label}; covers {len(covered_surnames)}: {cov})"
            )

            try:
                hits = self.client.search_by_name(first, last_token, jurisdictions=jurs)
            except Exception as e:
                msg = f"  Search error: {e}"
                self.stats.errors.append(msg)
                _log(msg)
                # Do not mark complete — resume will retry
                continue

            # Split hits: ethnicity-list surnames (primary) vs other surnames from
            # the same short-prefix search. Both are saved; GUI shows them in tabs.
            eth_matched: List[Any] = []
            other_hits: List[Any] = []
            for h in hits:
                if last_matches_target_surnames(h.last_name, eth_surnames_list):
                    eth_matched.append(h)
                else:
                    other_hits.append(h)

            self._mark_query_done(
                first, last_token, eth_key, hit_count=len(eth_matched) + len(other_hits)
            )
            self.stats.search_hits += len(hits)
            self.stats.search_hits_matched += len(eth_matched)
            self.stats.search_hits_other += len(other_hits)
            sample_firsts = sorted({(h.first_name or "?") for h in eth_matched})[:8]
            _log(
                f"  Hits: {len(hits)}  "
                f"(ethnicity list: {len(eth_matched)}, other surnames: {len(other_hits)})"
                + (
                    f"  matched first-names: {', '.join(sample_firsts)}"
                    if sample_firsts
                    else ""
                )
            )

            # Process ethnicity matches first (count toward max names), then others.
            # Others are always saved/archived but do not consume the names cap.
            ordered_hits: List[Tuple[Any, bool]] = [
                (h, True) for h in eth_matched
            ] + [(h, False) for h in other_hits]

            for hit, is_eth_match in ordered_hits:
                if self.cancel_check():
                    break
                # Max names applies only to ethnicity-list matches; still save "other".
                if is_eth_match and _names_limit_reached():
                    continue

                st = (hit.jurisdiction_id or hit.state or "UNK").upper()
                self._state_stats(st).hits += 1

                url = (hit.offender_uri or "").strip()
                dedupe_key = url or f"{hit.jurisdiction_id}:{hit.full_name}:{hit.date_of_birth}"
                if dedupe_key in seen_urls:
                    continue
                seen_urls.add(dedupe_key)
                self.stats.unique_offenders += 1
                if is_eth_match:
                    names_processed += 1
                ncap_label = "∞" if names_cap is None else str(names_cap)

                record = hit.to_record()
                record["likely_ethnicity"] = eth_label
                conf_eth, conf = self.ethnic_db.get_likely_ethnicity(
                    hit.last_name or last_token
                )
                record["name_confidence"] = conf
                if conf_eth and conf_eth != "Unknown":
                    record["likely_ethnicity"] = conf_eth

                # GUI routing: matched ethnicity list vs other surnames
                record["nsopw_ethnicity_match"] = bool(is_eth_match)
                record["nsopw_result_bucket"] = "matched" if is_eth_match else "other"

                flags = [
                    "nsopw",
                    f"search_last:{last_token}",
                    f"search_first:{first}",
                    f"first_mode:{mode}",
                    f"covers:{len(covered_surnames)}",
                    "ethnicity_match" if is_eth_match else "other_surname",
                    f"filter_ethnicity:{eth_key}",
                ]
                record["flags"] = json.dumps(flags)

                if skip_existing_urls and url and self._url_exists(url):
                    self.stats.skipped_existing += 1
                    continue

                if enrich_reports and url:
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
                            f"  Name ({names_processed}/{ncap_label}) "
                            f"report [{st}]: {url[:90]}"
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
                    if is_eth_match:
                        self.stats.inserted_matched += 1
                    else:
                        self.stats.inserted_other += 1
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
        _log(
            f"  · ethnicity list:    {self.stats.search_hits_matched}  · other surnames: "
            f"{self.stats.search_hits_other}"
        )
        _log(f"Unique offenders:      {self.stats.unique_offenders}")
        _log(
            f"Inserted:              {self.stats.inserted} "
            f"(matched {self.stats.inserted_matched}, other {self.stats.inserted_other})"
        )
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
