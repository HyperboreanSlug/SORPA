"""NSOPW ethnic database builder, requeue, and enrich."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from hashlib import sha1
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from scraper.database import Database
from scraper.ethnic_names import get_ethnic_database
from scraper.report_fetcher import ReportFetcher
from scraper.nsopw.client import (
    DEFAULT_JURISDICTIONS,
    NSOPWClient,
    NSOPWOffender,
    normalize_jurisdiction_code,
)
from scraper.nsopw.search_plan import *  # noqa: F403


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
    photos_saved: int = 0
    errors: List[str] = field(default_factory=list)
    by_state: Dict[str, StateReportStats] = field(default_factory=dict)


class RateLimiter:
    """Minimum interval between *starts* of operations (caller waits then works)."""

    # Poll cancel this often while sleeping (keeps Cancel responsive under 3s+ delays)
    CANCEL_POLL_S = 0.05

    def __init__(self, min_interval: float):
        self.min_interval = max(0.0, float(min_interval))
        self._last = 0.0

    def set_interval(self, min_interval: float) -> None:
        """Update pacing (e.g. GUI changed search/report delay mid-run)."""
        self.min_interval = max(0.0, float(min_interval))

    def wait(self, cancel_check: Optional[Callable[[], bool]] = None) -> bool:
        """
        Wait for min_interval since last operation.

        Returns True if *cancel_check* fired mid-wait (caller should abort).
        Sleeps in short slices so Cancel is felt in ~50ms, not after a full delay.
        """
        if cancel_check and cancel_check():
            return True
        if self.min_interval <= 0:
            self._last = time.monotonic()
            return bool(cancel_check and cancel_check())
        now = time.monotonic()
        remaining = self.min_interval - (now - self._last)
        if remaining <= 0:
            self._last = time.monotonic()
            return bool(cancel_check and cancel_check())
        end = now + remaining
        poll = max(0.02, float(self.CANCEL_POLL_S))
        while True:
            if cancel_check and cancel_check():
                return True
            now = time.monotonic()
            left = end - now
            if left <= 0:
                break
            time.sleep(left if left < poll else poll)
        self._last = time.monotonic()
        return bool(cancel_check and cancel_check())


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
        # Shared cookie jar + captcha queue (manual browser solve → import cookies)
        from scraper.cookie_jar import CaptchaQueue, CookieJarStore

        self.cookie_store = CookieJarStore()
        self.captcha_queue = CaptchaQueue()
        self.reports = ReportFetcher(
            delay=client_report_sleep,
            cookie_store=self.cookie_store,
            captcha_queue=self.captcha_queue,
            use_saved_cookies=True,
        )
        self.search_limiter = RateLimiter(0.0 if client_owned_delay else search_delay)
        self.report_limiter = RateLimiter(0.0 if client_owned_delay else report_delay)
        self.html_dir = Path(html_dir)
        self.html_dir.mkdir(parents=True, exist_ok=True)
        self.cancel_check = cancel_check or (lambda: False)
        self.stats = BuildStats()
        self._known_urls: Set[str] = set()
        self._ensure_query_log()

    def close(self) -> None:
        self.client.close()
        self.reports.close()
        self.db.close()

    def _load_known_urls(self) -> None:
        """Cache existing source_url values for O(1) skip-existing checks."""
        try:
            self._known_urls = self.db.existing_source_urls()
        except Exception:
            self._known_urls = set()

    def _ensure_query_log(self) -> None:
        """Track completed NSOPW API queries (first + last token) for resume support.

        Ethnicity is stored for audit only. Skip decisions key on (first, last)
        because the NSOPW name API is not ethnicity-filtered — re-running the same
        first/last under another ethnicity would be a duplicate network search.
        """
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
        # Fast lookup by API identity (first + last), any ethnicity
        self.db._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_nsopw_query_log_api
            ON nsopw_query_log (first_prefix, surname)
            """
        )
        self.db._conn.commit()
        # In-memory set of completed (first_upper, last_lower); filled by _load_completed_queries
        self._completed_queries: Set[Tuple[str, str]] = set()

    @staticmethod
    def _query_key(first: str, surname: str) -> Tuple[str, str]:
        """Canonical API search identity: (FIRST, last)."""
        return ((first or "").strip().upper(), (surname or "").strip().lower())

    def _load_completed_queries(self) -> Set[Tuple[str, str]]:
        """Load all completed (first, last) pairs from the DB (any ethnicity)."""
        try:
            rows = self.db._conn.execute(
                "SELECT first_prefix, surname FROM nsopw_query_log"
            ).fetchall()
        except Exception:
            return set()
        out: Set[Tuple[str, str]] = set()
        for row in rows:
            out.add(self._query_key(row[0], row[1]))
        self._completed_queries = out
        return out

    def _state_stats(self, state: str) -> StateReportStats:
        key = (state or "UNK").upper()[:12] or "UNK"
        if key not in self.stats.by_state:
            self.stats.by_state[key] = StateReportStats()
        return self.stats.by_state[key]

    def _query_done(self, first: str, surname: str, ethnicity: str = "") -> bool:
        """True if this first+last API query was completed (ethnicity ignored)."""
        key = self._query_key(first, surname)
        if key in getattr(self, "_completed_queries", ()):
            return True
        # DB fallback (and when set not preloaded)
        row = self.db._conn.execute(
            """
            SELECT 1 FROM nsopw_query_log
            WHERE first_prefix = ? AND surname = ?
            LIMIT 1
            """,
            key,
        ).fetchone()
        if row is not None:
            self._completed_queries.add(key)
            return True
        return False

    def _mark_query_done(
        self, first: str, surname: str, ethnicity: str = "", hit_count: int = 0
    ) -> None:
        from datetime import datetime, timezone

        fp, sn = self._query_key(first, surname)
        if not fp or not sn:
            return
        eth = (ethnicity or "").strip().lower()
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.db._conn.execute(
            """
            INSERT INTO nsopw_query_log (first_prefix, surname, ethnicity, completed_at, hit_count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(first_prefix, surname, ethnicity) DO UPDATE SET
                completed_at = excluded.completed_at,
                hit_count = excluded.hit_count
            """,
            (fp, sn, eth, now, int(hit_count)),
        )
        self.db._conn.commit()
        self._completed_queries.add((fp, sn))

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
        # Curated high-confidence Indians (own ethnicity OR indian → high_confidence sub)
        hc_names = getattr(self.ethnic_db, "indian_high_confidence_surnames", None) or set()
        if eth in (
            "indian_high_confidence",
            "high_confidence_indian",
            "high-confidence indian",
            "indian_hc",
        ) or (eth == "indian" and sub == "high_confidence"):
            take(hc_names, "Indian (high_confidence)", cap if eth != "all" else group_cap())
        # Indian subcontinent / South Asian (separate list; optional regional groups)
        # Note: high_confidence is a curated subset — only when subcategory selects it
        # (handled above); never merge into eth=indian "all" (avoids dupes + noise).
        if eth in ("all", "indian") and sub != "high_confidence":
            by_group = getattr(self.ethnic_db, "indian_surnames_by_group", None) or {}
            if by_group:
                for group, names in sorted(by_group.items()):
                    if group.lower() == "high_confidence":
                        continue
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
        use_compact_prefixes: bool = True,
        min_combined_len: int = MIN_COMBINED_NAME_LEN,
        log: Optional[Callable[[str], None]] = None,
        on_insert: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        live_options: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> BuildStats:
        """
        Run the ethnic-name NSOPW search pipeline.

        first_mode:
          - "initials" (default): full A–Z firsts + all list surname digraphs
          - "indian" / "common": abbreviated BOTH first letters
            (ASRPMKVNBD) AND top ~30 Indian surname digraphs (RA/CH/KA/…)
          - "indian_wide" / "common_wide": wider firsts (+GJHT) and ~50 digraphs
          - "full": use DEFAULT_FIRST_NAMES or provided first_names list
          - "custom": only the provided first_names list

        Short last-name prefixes (min combined first+last length 3) collapse many
        list surnames into fewer queries (e.g. M+AH covers Ahmed and Ahmad).
        Prefixes are always derived from the selected surname list (never
        brute-force AA–ZZ). Abbreviated mode further cuts surname digraphs to
        the most common Indian-likely letter combos only.

        use_compact_prefixes:
          When True (default), collapse surnames to short last prefixes that
          satisfy NSOPW's min combined first+last length (usually 3 letters).
          When False, search each full surname × first token (many more queries).
        min_combined_len:
          NSOPW API minimum for len(first)+len(last); default 3.

        max_searches:
          Cap on new NSOPW API queries. None or <= 0 means unlimited.
        max_names / max_report_fetches:
          Cap on unique offender names processed (GUI "Max reports" = max names).
          max_report_fetches is an alias kept for CLI compatibility.
          None or <= 0 means unlimited.

        skip_completed_searches:
          When True (default), never re-run a (first, last) API query already in
          nsopw_query_log — ethnicity is ignored for this check. Set False only
          to explicitly repeat old searches.
        new_files_only:
          Skip report HTTP download when local HTML already exists for that URL.
        all_surnames:
          Ignore surnames_limit and use every name in the selected list(s).

        on_insert: optional callback with the stored record after each successful insert
        (used by the GUI for live Recent inserts).
        on_progress: optional callback with a progress dict after each plan step
        (plan_i, plan_total, searches, inserted, hits, current query, etc.).
        live_options: optional callable returning a dict of runtime knobs re-read
          during the run (delays, caps, skip/enrich/save flags). Ethnicity and
          surname plan are fixed at start; only operational knobs are live.
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

        # Fresh counters each run (do not accumulate across build() calls)
        self.stats = BuildStats()

        search_cap = _cap(max_searches)
        # "Max reports" in the GUI means max unique names, not HTTP report fetches.
        # Prefer explicit max_names; fall back to max_report_fetches for CLI.
        names_cap = _cap(max_names if max_names is not None else max_report_fetches)

        # Live-tunable operational flags (mutated by _apply_live_options each step)
        skip_existing_urls = bool(skip_existing_urls)
        skip_completed_searches = bool(skip_completed_searches)
        new_files_only = bool(new_files_only)
        enrich_reports = bool(enrich_reports)
        save_html = bool(save_html)
        _live_last_sig: Optional[str] = None

        def _apply_live_options(*, announce: bool = True) -> None:
            """Refresh delays/caps/flags from live_options callback (GUI mid-run)."""
            nonlocal search_cap, names_cap
            nonlocal skip_existing_urls, skip_completed_searches, new_files_only
            nonlocal enrich_reports, save_html, _live_last_sig
            if not live_options:
                return
            try:
                opts = live_options() or {}
            except Exception:
                return
            if not isinstance(opts, dict):
                return

            if "max_searches" in opts:
                search_cap = _cap(opts.get("max_searches"))
            if "max_names" in opts:
                names_cap = _cap(opts.get("max_names"))
            elif "max_report_fetches" in opts:
                names_cap = _cap(opts.get("max_report_fetches"))

            if "skip_existing_urls" in opts:
                skip_existing_urls = bool(opts.get("skip_existing_urls"))
            if "skip_completed_searches" in opts:
                skip_completed_searches = bool(opts.get("skip_completed_searches"))
            if "new_files_only" in opts:
                new_files_only = bool(opts.get("new_files_only"))
            if "enrich_reports" in opts:
                enrich_reports = bool(opts.get("enrich_reports"))
            if "save_html" in opts:
                save_html = bool(opts.get("save_html"))

            if "search_delay" in opts and opts.get("search_delay") is not None:
                try:
                    sd = max(DEFAULT_MIN_SEARCH_INTERVAL, float(opts["search_delay"]))
                    self.search_delay = sd
                    self.search_limiter.set_interval(sd)
                except (TypeError, ValueError):
                    pass
            if "report_delay" in opts and opts.get("report_delay") is not None:
                try:
                    rd = max(DEFAULT_MIN_REPORT_INTERVAL, float(opts["report_delay"]))
                    self.report_delay = rd
                    self.report_limiter.set_interval(rd)
                except (TypeError, ValueError):
                    pass

            # Skip-existing turned on mid-run → load URL cache once
            if skip_existing_urls and not self._known_urls:
                self._load_known_urls()

            sig = (
                f"sc={search_cap}|nc={names_cap}|sd={self.search_delay:.2f}|"
                f"rd={self.report_delay:.2f}|se={int(skip_existing_urls)}|"
                f"sk={int(skip_completed_searches)}|nf={int(new_files_only)}|"
                f"en={int(enrich_reports)}|sh={int(save_html)}"
            )
            if announce and sig != _live_last_sig and _live_last_sig is not None:
                _log(
                    "Live options updated: "
                    f"max_searches={'∞' if search_cap is None else search_cap}, "
                    f"max_names={'∞' if names_cap is None else names_cap}, "
                    f"search_delay={self.search_delay:.2f}s, "
                    f"report_delay={self.report_delay:.2f}s, "
                    f"skip_urls={skip_existing_urls}, "
                    f"skip_done={skip_completed_searches}, "
                    f"new_html_only={new_files_only}, "
                    f"enrich={enrich_reports}, save_html={save_html}"
                )
            _live_last_sig = sig

        mode = (first_mode or "initials").lower().strip()
        if first_names is not None:
            firsts = [f.strip() for f in first_names if f and str(f).strip()]
        else:
            firsts = first_initials_for_mode(mode)

        if not firsts:
            firsts = list(FIRST_INITIALS)  # default full A–Z, not abbreviated

        jurs = list(jurisdictions) if jurisdictions else list(DEFAULT_JURISDICTIONS)
        surname_pairs = self.surnames_for_ethnicity(
            ethnicity,
            limit_per_group=surnames_limit,
            all_surnames=all_surnames,
            subcategory=subcategory,
        )
        eth_key = (ethnicity or "").lower()
        sub_disp = (subcategory or "all").strip() or "all"

        # Collapse to short last prefixes (first+last ≥ min_combined) for fewer API calls.
        # Abbreviated mode shortens BOTH first letters (Indian set) AND surname
        # digraphs (top Indian-likely combos only).
        try:
            mcl = max(3, int(min_combined_len))
        except (TypeError, ValueError):
            mcl = MIN_COMBINED_NAME_LEN
        abbrev = is_abbreviated_first_mode(mode)
        last_allow = last_prefix_whitelist_for(
            eth_key, surname_pairs, abbreviated=abbrev, mode=mode
        )
        naive_queries = len(surname_pairs) * len(firsts)
        if use_compact_prefixes:
            search_plan = compact_search_plan(
                surname_pairs,
                firsts,
                min_combined=mcl,
                allowed_last_prefixes=last_allow,
            )
        else:
            # One query per full surname × first (no prefix collapse), de-duped.
            # Abbreviated mode still drops surnames whose digraph is not allowed.
            plan_map: Dict[Tuple[str, str], Tuple[str, str, str, Set[str]]] = {}
            for sn, eth_lab in surname_pairs:
                s = (sn or "").strip()
                if not s:
                    continue
                if last_allow is not None:
                    al = _surname_alnum(s)
                    dig = al[:2].upper() if len(al) >= 2 else al.upper()
                    if dig and dig not in last_allow:
                        continue
                for fn in firsts:
                    f = (fn or "").strip()
                    if not f:
                        continue
                    if len(f) + len(s) < mcl:
                        continue
                    key = self._query_key(f, s)
                    if key not in plan_map:
                        plan_map[key] = (f, s, eth_lab or "", {s})
                    else:
                        prev_f, prev_s, prev_eth, cov = plan_map[key]
                        cov.add(s)
                        if eth_lab and not prev_eth:
                            plan_map[key] = (prev_f, prev_s, eth_lab or "", cov)
            search_plan = [
                (f, s, eth, sorted(cov, key=str.lower))
                for f, s, eth, cov in plan_map.values()
            ]
            search_plan.sort(key=lambda t: (t[1].upper(), t[0].upper()))
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
        _log(f"First-name mode: {describe_first_mode(mode)}")
        _log(f"  Tokens ({len(firsts)}): {', '.join(firsts[:14])}{'…' if len(firsts) > 14 else ''}")
        if use_compact_prefixes:
            dig_note = ""
            if last_allow is not None:
                dig_note = (
                    f"; last prefixes restricted to {len(last_allow)} "
                    f"Indian-likely digraphs"
                )
            else:
                dig_note = "; last prefixes from selected surnames only (not AA–ZZ)"
            _log(
                f"Compact queries: {compact_queries:,} "
                f"(vs {naive_queries:,} full surname×first; "
                f"short last prefixes, min combined {mcl} letters{dig_note})"
            )
        else:
            _log(
                f"Full-surname queries: {compact_queries:,} "
                f"(compact prefixes OFF; min combined {mcl} letters)"
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
        _log(
            f"Skip completed searches: {skip_completed_searches} "
            f"({'default — will not re-hit finished first+last pairs' if skip_completed_searches else 'OFF — will re-run old searches'})"
        )
        _log(f"Skip known URLs in DB: {skip_existing_urls}")
        _log(f"New report files only (no re-download): {new_files_only}")
        _log(f"Save report HTML: {save_html} → {self.html_dir}")
        _log(f"Enrich demographics: {enrich_reports}")
        if skip_existing_urls:
            self._load_known_urls()
            _log(f"Known URLs cached for skip: {len(self._known_urls):,}")
        else:
            self._known_urls = set()

        # Preload completed API queries so we never re-hit NSOPW for the same
        # (first, last) unless skip_completed_searches is False (explicit repeat).
        already_done_n = 0
        planned_done_n = 0
        if skip_completed_searches:
            completed = self._load_completed_queries()
            already_done_n = len(completed)
            planned_done_n = sum(
                1
                for f, last_tok, *_rest in search_plan
                if self._query_key(f, last_tok) in completed
            )
            _log(
                f"Completed search log: {already_done_n:,} unique first+last pairs in DB; "
                f"{planned_done_n:,} of {len(search_plan):,} planned queries already done (will skip)"
            )
        else:
            self._completed_queries = set()
            _log("Repeat mode: completed-search log ignored for this run")

        _log("NSOPW Conditions of Use apply: https://www.nsopw.gov/")
        if use_compact_prefixes:
            _log(
                "Yield mode: short partials (e.g. M+AH) + keep all API hits "
                f"(aliases/fuzzy included; min combined {mcl}). "
                "Primary tab = ethnicity-list surnames only; other tab = rest."
            )
        else:
            _log(
                f"Full surname mode: each list name is searched as-is "
                f"(API min combined length {mcl})."
            )
        _log("")

        seen_urls: Set[str] = set()
        search_count = 0
        report_count = 0
        names_processed = 0  # unique names after dedupe (counts toward max names)
        plan_total = len(search_plan)
        # New work only: planned queries not already completed (when resume/skip on)
        remaining = plan_total - planned_done_n if skip_completed_searches else plan_total
        work_total = remaining
        if search_cap is not None:
            work_total = min(remaining, search_cap) if remaining else 0
        work_total = max(int(work_total or 0), 1)

        def _search_limit_reached() -> bool:
            return search_cap is not None and search_count >= search_cap

        def _names_limit_reached() -> bool:
            return names_cap is not None and names_processed >= names_cap

        def _progress(**extra: Any) -> None:
            if not on_progress:
                return
            try:
                pi = int(extra.get("plan_i", 0) or 0)
                pt = int(extra.get("plan_total", plan_total) or 0)
                # Refresh work_total from current caps for the progress bar
                if search_cap is not None:
                    total = max(int(search_cap), 1)
                else:
                    total = max(int(work_total or pt or 1), 1)
                on_progress({
                    "plan_i": pi,
                    "plan_total": pt,
                    "done": pi,
                    "total": total,
                    "searches": int(self.stats.searches),
                    "searches_skipped": int(self.stats.searches_skipped),
                    "search_hits": int(self.stats.search_hits),
                    "search_hits_matched": int(self.stats.search_hits_matched),
                    "search_hits_other": int(self.stats.search_hits_other),
                    "inserted": int(self.stats.inserted),
                    "inserted_matched": int(self.stats.inserted_matched),
                    "inserted_other": int(self.stats.inserted_other),
                    "skipped_existing": int(self.stats.skipped_existing),
                    "reports_fetched": int(self.stats.reports_fetched),
                    "reports_with_race": int(self.stats.reports_with_race),
                    "html_saved": int(self.stats.html_saved),
                    "photos_saved": int(getattr(self.stats, "photos_saved", 0) or 0),
                    "errors": len(self.stats.errors),
                    "current": str(extra.get("current") or ""),
                    "phase": str(extra.get("phase") or "running"),
                    # Explicit search terms for the GUI progress line
                    "search_first": str(extra.get("search_first") or ""),
                    "search_last": str(extra.get("search_last") or ""),
                    "search_covers": str(extra.get("search_covers") or ""),
                    "search_label": str(extra.get("search_label") or ""),
                    "search_cap": search_cap,
                    "names_cap": names_cap,
                    "search_delay": self.search_delay,
                    "report_delay": self.report_delay,
                })
            except Exception:
                pass

        if live_options:
            _log(
                "Live options enabled: max searches/names, delays, and checkboxes "
                "re-apply every step. Ethnicity / surname plan stay fixed for this run."
            )
            _apply_live_options(announce=False)

        _progress(plan_i=0, plan_total=plan_total, current="starting…", phase="start")

        last_plan_i = 0
        for plan_i, (first, last_token, eth_label, covered_surnames) in enumerate(
            search_plan, start=1
        ):
            last_plan_i = plan_i
            # GUI may have changed delays/caps/checkboxes since last step
            _apply_live_options(announce=True)
            if self.cancel_check():
                _log("Cancelled by user.")
                _progress(
                    plan_i=plan_i - 1,
                    plan_total=plan_total,
                    current="cancelled",
                    phase="cancelled",
                )
                break
            if _search_limit_reached() or _names_limit_reached():
                _log(
                    "Limit reached: "
                    + (
                        f"searches {search_count}/{search_cap}"
                        if search_cap is not None and search_count >= search_cap
                        else f"names {names_processed}/{names_cap}"
                    )
                )
                break

            # Default: never re-hit an API query already logged (any ethnicity).
            # Only re-runs when skip_completed_searches is False (explicit repeat).
            if skip_completed_searches and self._query_done(first, last_token, eth_key):
                self.stats.searches_skipped += 1
                # Quiet skip log: only every 25th + first few (avoids spam looking like re-runs)
                if self.stats.searches_skipped <= 3 or self.stats.searches_skipped % 25 == 0:
                    cov = ",".join(covered_surnames[:4])
                    if len(covered_surnames) > 4:
                        cov += f"…(+{len(covered_surnames) - 4})"
                    _log(
                        f"  Skip completed search #{self.stats.searches_skipped}: "
                        f"'{first}' {last_token} [{cov}]"
                    )
                cov = ",".join(covered_surnames[:4])
                if len(covered_surnames) > 4:
                    cov += f"…(+{len(covered_surnames) - 4})"
                _progress(
                    plan_i=plan_i,
                    plan_total=plan_total,
                    current=f"skip first='{first}' last='{last_token}'",
                    phase="resume_skip",
                    search_first=first,
                    search_last=last_token,
                    search_covers=cov,
                    search_label=eth_label or "",
                )
                continue

            if self.cancel_check():
                _log("Cancelled by user.")
                _progress(
                    plan_i=plan_i - 1,
                    plan_total=plan_total,
                    current="cancelled",
                    phase="cancelled",
                )
                break
            search_count += 1
            self.stats.searches = search_count
            if self.search_limiter.wait(self.cancel_check):
                _log("Cancelled by user (during search delay).")
                _progress(
                    plan_i=plan_i - 1,
                    plan_total=plan_total,
                    current="cancelled",
                    phase="cancelled",
                )
                # Don't count a search we never issued
                search_count = max(0, search_count - 1)
                self.stats.searches = search_count
                break
            if self.cancel_check():
                _log("Cancelled by user.")
                _progress(
                    plan_i=plan_i - 1,
                    plan_total=plan_total,
                    current="cancelled",
                    phase="cancelled",
                )
                search_count = max(0, search_count - 1)
                self.stats.searches = search_count
                break
            cap_label = "∞" if search_cap is None else str(search_cap)
            cov = ",".join(covered_surnames[:4])
            if len(covered_surnames) > 4:
                cov += f"…(+{len(covered_surnames) - 4})"
            # Always search with normalized tokens (same as query log keys)
            first_q, last_q = self._query_key(first, last_token)
            # Preserve display casing of first if single letter already upper
            first_api = first_q
            last_api = last_token.strip()  # NSOPW accepts any case; log uses last_q
            _log(
                f"[{search_count}/{cap_label}] NSOPW: first='{first_api}' last='{last_api}' "
                f"({eth_label}; covers {len(covered_surnames)}: {cov})"
            )
            _progress(
                plan_i=plan_i,
                plan_total=plan_total,
                current=f"first='{first_api}' last='{last_api}'",
                phase="search",
                search_first=first_api,
                search_last=last_api,
                search_covers=cov,
                search_label=eth_label or "",
            )

            try:
                hits = self.client.search_by_name(first_api, last_api, jurisdictions=jurs)
            except Exception as e:
                msg = f"  Search error: {e}"
                self.stats.errors.append(msg)
                _log(msg)
                # Do not mark complete — next run will retry
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

            # Log as done immediately after a successful API response (0 hits is still done)
            self._mark_query_done(
                first_api, last_q, eth_key, hit_count=len(eth_matched) + len(other_hits)
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

            cancelled = False
            for hit, is_eth_match in ordered_hits:
                _apply_live_options(announce=False)
                if self.cancel_check():
                    cancelled = True
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
                    hit.last_name or last_token,
                    first_name=getattr(hit, "first_name", None) or None,
                    middle_name=getattr(hit, "middle_name", None) or None,
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

                # Tag NSOPW search hit as a source (race usually empty until report HTML)
                try:
                    from scraper.database.sources import (
                        attach_source_to_record,
                        extract_tracked_fields,
                        make_source,
                    )

                    nsopw_src = make_source(
                        source_type="nsopw",
                        jurisdiction=st,
                        origin="nsopw_search",
                        label=f"NSOPW ({st})",
                        external_id=str(record.get("external_id") or ""),
                        source_url=str(url or record.get("source_url") or ""),
                        fields=extract_tracked_fields(record),
                        html_verified=False,
                        html_status="pending" if url else "no_url",
                    )
                    attach_source_to_record(record, nsopw_src, prefer_new_fields=False)
                except Exception:
                    pass

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
                        if self.cancel_check():
                            cancelled = True
                            break
                        report_count += 1
                        self.stats.reports_fetched = report_count
                        sst = self._state_stats(st)
                        sst.reports_attempted += 1
                        if self.report_limiter.wait(self.cancel_check):
                            cancelled = True
                            break
                        if self.cancel_check():
                            cancelled = True
                            break
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
                        if demo.get("photo_path"):
                            record["photo_path"] = demo["photo_path"]
                        if demo.get("photo_url") and not record.get("photo_url"):
                            record["photo_url"] = demo["photo_url"]
                        block = demo.get("report_block_reason") or ""
                        status = str(demo.get("report_fetch_status") or "")
                        if block or status.startswith("blocked:") or status.startswith("error:"):
                            reason = block or status
                            sst.blocks[reason] = sst.blocks.get(reason, 0) + 1
                            if status.startswith("error:"):
                                sst.errors += 1
                        if demo.get("needs_manual_captcha"):
                            _log(
                                "    ↳ CAPTCHA/WAF wall — queued for manual browser solve "
                                "(Settings → Access assistance: open URL, complete challenge, "
                                "import cookies, re-run / requeue)"
                            )
                        if not demo.get("report_fetch_ok"):
                            _log(
                                f"    ↳ no demographics "
                                f"(status={demo.get('report_fetch_status')}"
                                f"{', ' + block if block else ''})"
                            )
                        else:
                            crime_snip = (record.get("crime") or demo.get("crime") or "")[:40]
                            _log(
                                f"    ↳ race={demo.get('race') or '—'} "
                                f"eth={demo.get('ethnicity') or '—'} "
                                f"gender={demo.get('gender') or '—'}"
                                f"{' · crime=' + crime_snip if crime_snip else ''}"
                                f"{' · photo' if record.get('photo_path') else ''}"
                            )
                        record["source_url"] = demo.get("report_final_url") or url
                elif save_html and url and not enrich_reports:
                    pass

                if url:
                    record["source_url"] = record.get("source_url") or url

                # Save offender photo (NSOPW imageUri and/or report-page assets)
                self._ensure_photo(record, hit, st)

                if self.cancel_check():
                    cancelled = True
                    break

                try:
                    self.db.insert_offender(record)
                    self.stats.inserted += 1
                    if is_eth_match:
                        self.stats.inserted_matched += 1
                    else:
                        self.stats.inserted_other += 1
                    # Keep skip-cache in sync so same-run duplicates are skipped
                    self._remember_url(record.get("source_url") or url)
                    if on_insert:
                        try:
                            on_insert(dict(record))
                        except Exception:
                            pass
                except Exception as e:
                    msg = f"  Insert error: {e}"
                    self.stats.errors.append(msg)
                    _log(msg)

            if cancelled:
                _log("Cancelled by user.")
                _progress(
                    plan_i=plan_i,
                    plan_total=plan_total,
                    current="cancelled",
                    phase="cancelled",
                )
                break

        was_cancelled = bool(self.cancel_check())
        _progress(
            plan_i=last_plan_i if was_cancelled else plan_total,
            plan_total=plan_total,
            current="cancelled" if was_cancelled else "complete",
            phase="cancelled" if was_cancelled else "done",
        )
        _log("")
        _log("=== Build cancelled ===" if was_cancelled else "=== Build complete ===")
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
        _log(f"Photos saved:          {self.stats.photos_saved}")
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
                "Interactive CAPTCHA/WAF pages are queued (data/captcha_queue.json) — "
                "solve in a browser, import cookies under Settings → Access assistance, then requeue."
            )
            try:
                n_q = len(self.captcha_queue.list_items())
                if n_q:
                    _log(f"CAPTCHA queue size: {n_q} (see Settings)")
            except Exception:
                pass
        return self.stats

    def requeue_incomplete(
        self,
        *,
        need_race: bool = True,
        need_crime: bool = True,
        need_photo: bool = True,
        need_html: bool = False,
        limit: int = 100,
        state: Optional[str] = None,
        save_html: bool = True,
        log: Optional[Callable[[str], None]] = None,
        on_update: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Any]:
        """
        Re-fetch jurisdiction reports for DB rows missing race/crime/photo/HTML.

        Updates existing offender rows in place (does not insert duplicates).
        on_progress(done, total) is called after each attempt when provided.
        """
        def _log(msg: str) -> None:
            if log:
                log(msg)
            else:
                print(msg)

        rows = self.db.find_incomplete_reports(
            need_race=need_race,
            need_crime=need_crime,
            need_photo=need_photo,
            need_html=need_html,
            require_url=True,
            limit=limit,
            state=state,
        )
        summary = {
            "queued": len(rows),
            "attempted": 0,
            "updated": 0,
            "with_race": 0,
            "with_crime": 0,
            "with_photo": 0,
            "with_html": 0,
            "errors": 0,
        }
        total_q = len(rows)
        _log(
            f"Requeue incomplete reports: {len(rows)} candidates "
            f"(need race={need_race} crime={need_crime} photo={need_photo} html={need_html})"
        )
        if on_progress:
            try:
                on_progress(0, total_q or 1)
            except Exception:
                pass
        for rec in rows:
            if self.cancel_check():
                _log("Requeue cancelled.")
                break
            url = (rec.get("source_url") or "").strip()
            if not url:
                continue
            rid = rec.get("id")
            st = (rec.get("state") or rec.get("source_state") or "UNK").upper()
            summary["attempted"] += 1
            if self.report_limiter.wait(self.cancel_check):
                _log("Requeue cancelled (during delay).")
                break
            if self.cancel_check():
                _log("Requeue cancelled.")
                break
            name = (
                (rec.get("full_name") or "").strip()
                or f"{rec.get('first_name') or ''} {rec.get('last_name') or ''}".strip()
                or f"id={rid}"
            )
            _log(f"  [{summary['attempted']}/{len(rows)}] Re-fetch [{st}] {name[:50]}")
            try:
                demo = self.reports.fetch_demographics(
                    url,
                    save_html=save_html,
                    html_dir=self.html_dir,
                    jurisdiction=st,
                )
            except Exception as e:
                summary["errors"] += 1
                _log(f"    ↳ error: {e}")
                continue

            # Build patch from demo + existing
            patch: Dict[str, Any] = {}
            record = dict(rec)
            self._merge_demographics(record, demo)
            if demo.get("report_html_path"):
                record["report_html_path"] = demo["report_html_path"]
            if demo.get("photo_path"):
                record["photo_path"] = demo["photo_path"]
            # Photo from NSOPW-style url on record
            class _Hit:
                image_uri = rec.get("photo_url") or ""

            self._ensure_photo(record, _Hit(), st)

            for key in (
                "race", "ethnicity", "gender", "height", "weight",
                "eye_color", "hair_color", "crime", "offense_type",
                "offense_description", "report_html_path", "photo_path", "photo_url",
                "county", "city", "address", "risk_level",
                "sources_json", "flags",
            ):
                new_v = record.get(key)
                old_v = rec.get(key)
                if new_v is None or new_v == "":
                    continue
                if key in ("sources_json", "flags", "race"):
                    # Always persist multi-source rewrites
                    if new_v != old_v:
                        patch[key] = new_v
                    continue
                if new_v and (not old_v or (key in ("crime",) and new_v != old_v)):
                    # Fill empty; crime may update; race handled above via sources
                    if not old_v or key in (
                        "ethnicity", "crime", "photo_path", "report_html_path"
                    ):
                        if new_v != old_v:
                            patch[key] = new_v

            if patch and rid is not None:
                ok = self.db.update_offender(int(rid), patch)
                if ok:
                    summary["updated"] += 1
                    merged = dict(rec)
                    merged.update(patch)
                    if merged.get("race"):
                        summary["with_race"] += 1
                    if merged.get("crime") or merged.get("offense_description") or merged.get("offense_type"):
                        summary["with_crime"] += 1
                    if merged.get("photo_path"):
                        summary["with_photo"] += 1
                    if merged.get("report_html_path"):
                        summary["with_html"] += 1
                    _log(
                        f"    ↳ updated id={rid} "
                        f"race={patch.get('race') or '—'} "
                        f"crime={(patch.get('crime') or '—')[:40]} "
                        f"{'photo ' if patch.get('photo_path') else ''}"
                        f"{'html' if patch.get('report_html_path') else ''}"
                    )
                    if on_update:
                        try:
                            on_update(merged)
                        except Exception:
                            pass
                else:
                    _log(f"    ↳ no DB change for id={rid}")
            else:
                _log(
                    f"    ↳ no new fields "
                    f"(status={demo.get('report_fetch_status')} "
                    f"{demo.get('report_block_reason') or ''})"
                )

            if on_progress:
                try:
                    on_progress(summary["attempted"], total_q or 1)
                except Exception:
                    pass

        _log(
            f"Requeue done: attempted={summary['attempted']} updated={summary['updated']} "
            f"errors={summary['errors']}"
        )
        return summary

    def verify_all_sources(
        self,
        *,
        limit: int = 100,
        state: Optional[str] = None,
        only_unverified: bool = True,
        save_html: bool = True,
        log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Any]:
        """
        For each offender, attempt HTML verification of every source that has a URL.

        Updates sources_json html_verified / html_status / fields from the live
        (or archived) report page so bulk CSV values stay tagged separately from
        jurisdiction HTML values.
        """
        from scraper.database.sources import (
            dumps_sources,
            jurisdiction_from_url,
            make_source,
            parse_sources,
            apply_sources_to_record,
        )

        def _log(msg: str) -> None:
            if log:
                log(msg)
            else:
                print(msg)

        def _split_urls(raw: str) -> List[str]:
            try:
                from scraper.public_links import split_source_urls as _split

                parts = _split(raw or "")
                if parts:
                    return list(parts)
            except Exception:
                pass
            return [u.strip() for u in str(raw or "").split(" | ") if u.strip()]

        sql = "SELECT * FROM offenders WHERE 1=1"
        params: List[Any] = []
        if state:
            sql = self.db._append_state_filter(sql, params, state)  # type: ignore[attr-defined]
        sql += " ORDER BY id ASC"
        if limit and int(limit) > 0:
            sql += " LIMIT ?"
            params.append(int(limit))

        rows = [dict(r) for r in self.db._conn.execute(sql, params).fetchall()]
        summary = {
            "rows": len(rows),
            "sources_attempted": 0,
            "sources_verified": 0,
            "sources_failed": 0,
            "rows_updated": 0,
            "errors": 0,
        }
        total = len(rows)
        _log(
            f"Verify sources HTML: {total} rows "
            f"(only_unverified={only_unverified})"
        )
        if on_progress:
            try:
                on_progress(0, total or 1)
            except Exception:
                pass

        for i, rec in enumerate(rows):
            if self.cancel_check():
                _log("Verify sources cancelled.")
                break

            sources = parse_sources(rec.get("sources_json"))
            urls = _split_urls(str(rec.get("source_url") or ""))

            existing_urls = {
                str(s.get("source_url") or "").strip().lower()
                for s in sources
                if s.get("source_url")
            }
            for u in urls:
                if u.strip().lower() not in existing_urls:
                    j = jurisdiction_from_url(u) or str(rec.get("state") or "")
                    sources.append(
                        make_source(
                            source_type="report_html",
                            jurisdiction=j,
                            origin="source_url",
                            source_url=u,
                            fields={},
                            html_verified=False,
                            html_status="pending",
                        )
                    )

            if not sources:
                if on_progress:
                    try:
                        on_progress(i + 1, total or 1)
                    except Exception:
                        pass
                continue

            record = dict(rec)
            record["sources_json"] = dumps_sources(sources)
            changed = False

            for src in list(sources):
                if self.cancel_check():
                    break
                surl = str(src.get("source_url") or "").strip()
                if not surl:
                    if src.get("html_status") != "no_url":
                        src["html_status"] = "no_url"
                        changed = True
                    continue
                if only_unverified and src.get("html_verified"):
                    continue

                st = (
                    str(src.get("jurisdiction") or rec.get("state") or "UNK")
                    .split(" | ")[0]
                    .strip()
                    .upper()
                )
                summary["sources_attempted"] += 1
                if self.report_limiter.wait(self.cancel_check):
                    break
                _log(
                    f"  [{i+1}/{total}] verify [{st}] "
                    f"{(rec.get('first_name') or '')} {(rec.get('last_name') or '')} "
                    f"← {surl[:80]}"
                )
                try:
                    demo = self.reports.fetch_demographics(
                        surl,
                        save_html=save_html,
                        html_dir=self.html_dir,
                        jurisdiction=st,
                    )
                except Exception as e:
                    summary["errors"] += 1
                    summary["sources_failed"] += 1
                    src["html_status"] = f"error:{e}"
                    src["html_verified"] = False
                    changed = True
                    _log(f"    ↳ error: {e}")
                    continue

                self._merge_demographics(record, demo)
                # _merge_demographics already attached report source; refresh flags
                changed = True
                if demo.get("report_fetch_ok"):
                    summary["sources_verified"] += 1
                    _log(
                        f"    ↳ ok race={demo.get('race') or '—'} "
                        f"html={demo.get('report_html_path') or '—'}"
                    )
                else:
                    summary["sources_failed"] += 1
                    _log(
                        f"    ↳ fail status={demo.get('report_fetch_status')} "
                        f"{demo.get('report_block_reason') or ''}"
                    )

            if changed:
                apply_sources_to_record(record)
                patch = {
                    k: record.get(k)
                    for k in (
                        "sources_json", "race", "flags", "report_html_path",
                        "photo_path", "photo_url", "crime", "ethnicity",
                        "gender", "height", "weight", "eye_color", "hair_color",
                        "county", "city", "address",
                    )
                    if record.get(k) is not None and record.get(k) != rec.get(k)
                }
                if patch and rec.get("id") is not None:
                    if self.db.update_offender(int(rec["id"]), patch):
                        summary["rows_updated"] += 1

            if on_progress:
                try:
                    on_progress(i + 1, total or 1)
                except Exception:
                    pass

        _log(
            f"Verify sources done: attempted={summary['sources_attempted']} "
            f"verified={summary['sources_verified']} failed={summary['sources_failed']} "
            f"rows_updated={summary['rows_updated']}"
        )
        return summary

    @staticmethod
    def record_needs_enrichment(rec: Dict[str, Any]) -> bool:
        """True if race, crime, photo, URL, or archived HTML is still missing."""
        if not rec:
            return False
        photo = (rec.get("photo_path") or "").strip()
        has_photo = bool(photo) and Path(photo).is_file()
        has_race = bool((rec.get("race") or "").strip())
        has_crime = bool(
            (rec.get("crime") or "").strip()
            or (rec.get("offense_description") or "").strip()
            or (rec.get("offense_type") or "").strip()
        )
        has_url = bool((rec.get("source_url") or "").strip())
        has_html = bool((rec.get("report_html_path") or "").strip()) and Path(
            (rec.get("report_html_path") or "").strip()
        ).exists()
        # Need enrich if any core field is missing
        return not (has_photo and has_race and has_crime and has_url)

    def enrich_misclassified(
        self,
        records: List[Dict[str, Any]],
        *,
        limit: int = 50,
        prefer_missing_photo: bool = True,
        only_missing_data: bool = True,
        enrich_reports: bool = True,
        save_html: bool = True,
        log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
        on_update: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        NSOPW / report refresh for misclassification candidates.

        By default only rows **missing** photo, race, crime, or source URL are
        processed (complete rows are skipped).

        For each person still needing data:
          1. If they already have a source_url → re-fetch report (photo/race/crime).
          2. Else search NSOPW by first+last, pick best matching hit, attach URL
             and optional report/photo enrichment, update the existing DB row.

        Does not insert new rows (avoids duplicates).
        """
        def _log(msg: str) -> None:
            if log:
                log(msg)
            else:
                print(msg)

        # Deduplicate by id
        by_id: Dict[int, Dict[str, Any]] = {}
        for rec in records or []:
            try:
                rid = int(rec.get("id"))
            except (TypeError, ValueError):
                continue
            if rid in by_id:
                continue
            by_id[rid] = dict(rec)

        queue: List[Dict[str, Any]] = list(by_id.values())
        skipped_complete = 0
        if only_missing_data:
            incomplete: List[Dict[str, Any]] = []
            for rec in queue:
                if self.record_needs_enrichment(rec):
                    incomplete.append(rec)
                else:
                    skipped_complete += 1
            queue = incomplete

        if prefer_missing_photo:
            queue.sort(
                key=lambda r: (
                    0 if not (r.get("photo_path") or "").strip() else 1,
                    0 if not (r.get("source_url") or "").strip() else 1,
                    0 if not (r.get("race") or "").strip() else 1,
                    str(r.get("last_name") or ""),
                )
            )
        if limit and int(limit) > 0:
            queue = queue[: int(limit)]

        summary: Dict[str, Any] = {
            "queued": len(queue),
            "skipped_complete": skipped_complete,
            "attempted": 0,
            "updated": 0,
            "nsopw_searched": 0,
            "nsopw_matched": 0,
            "reports_fetched": 0,
            "with_photo": 0,
            "with_race": 0,
            "errors": 0,
            "skipped_no_name": 0,
        }
        total_q = len(queue)
        _log(
            f"NSOPW enrich misclassified: {total_q} incomplete "
            f"(skipped complete={skipped_complete}, "
            f"only_missing={only_missing_data}, reports={enrich_reports})"
        )
        if on_progress:
            try:
                on_progress(0, total_q or 1)
            except Exception:
                pass

        for rec in queue:
            if self.cancel_check():
                _log("Enrich cancelled.")
                break
            rid = rec.get("id")
            first = (rec.get("first_name") or "").strip().split()[0] if rec.get("first_name") else ""
            last = (rec.get("last_name") or "").strip()
            if not last:
                full = (rec.get("full_name") or "").strip()
                parts = full.replace(",", " ").split()
                if len(parts) >= 2:
                    first = first or parts[0]
                    last = parts[-1]
            name = (
                f"{first} {last}".strip()
                or (rec.get("full_name") or "").strip()
                or f"id={rid}"
            )
            st = (rec.get("state") or rec.get("source_state") or "UNK").upper()
            url = (rec.get("source_url") or "").strip()
            summary["attempted"] += 1
            _log(f"  [{summary['attempted']}/{total_q}] Enrich [{st}] {name[:55]}")

            patch: Dict[str, Any] = {}
            working = dict(rec)

            # --- Path A: already have URL → report re-fetch ---
            if url and enrich_reports:
                if self.report_limiter.wait(self.cancel_check):
                    _log("Enrich cancelled (during delay).")
                    break
                if self.cancel_check():
                    break
                try:
                    demo = self.reports.fetch_demographics(
                        url,
                        save_html=save_html,
                        html_dir=self.html_dir,
                        jurisdiction=st if st != "UNK" else None,
                    )
                    summary["reports_fetched"] += 1
                    self._merge_demographics(working, demo)
                    class _Hit:
                        image_uri = working.get("photo_url") or rec.get("photo_url") or ""

                    self._ensure_photo(working, _Hit(), st)
                except Exception as e:
                    summary["errors"] += 1
                    _log(f"    ↳ report error: {e}")

            # --- Path B: no URL → NSOPW name search ---
            elif first and last:
                if self.search_limiter.wait(self.cancel_check):
                    _log("Enrich cancelled (during delay).")
                    break
                if self.cancel_check():
                    break
                summary["nsopw_searched"] += 1
                try:
                    hits = self.client.search_by_name(first, last)
                except Exception as e:
                    summary["errors"] += 1
                    _log(f"    ↳ NSOPW search error: {e}")
                    hits = []

                best = self._pick_nsopw_hit_for_person(rec, hits)
                if best is None:
                    _log(f"    ↳ no NSOPW match among {len(hits)} hit(s)")
                else:
                    summary["nsopw_matched"] += 1
                    hit_rec = best.to_record()
                    for key in (
                        "source_url", "external_id", "photo_url", "state",
                        "source_state", "city", "address", "zip_code",
                        "latitude", "longitude", "gender", "date_of_birth", "age",
                    ):
                        val = hit_rec.get(key)
                        if val and not working.get(key):
                            working[key] = val
                            patch[key] = val
                    # Prefer NSOPW URL even if we had a weak one
                    if hit_rec.get("source_url"):
                        working["source_url"] = hit_rec["source_url"]
                        patch["source_url"] = hit_rec["source_url"]
                    if hit_rec.get("photo_url"):
                        working["photo_url"] = hit_rec["photo_url"]
                        patch["photo_url"] = hit_rec["photo_url"]
                    url = (working.get("source_url") or "").strip()
                    hit_st = (
                        (hit_rec.get("state") or hit_rec.get("source_state") or st) or "UNK"
                    ).upper()
                    _log(f"    ↳ matched NSOPW url={url[:80]}")

                    if enrich_reports and url:
                        if self.report_limiter.wait(self.cancel_check):
                            _log("Enrich cancelled (during delay).")
                            break
                        if self.cancel_check():
                            break
                        try:
                            demo = self.reports.fetch_demographics(
                                url,
                                save_html=save_html,
                                html_dir=self.html_dir,
                                jurisdiction=hit_st if hit_st != "UNK" else None,
                            )
                            summary["reports_fetched"] += 1
                            self._merge_demographics(working, demo)
                            self._ensure_photo(working, best, hit_st)
                        except Exception as e:
                            summary["errors"] += 1
                            _log(f"    ↳ report error: {e}")
                    else:
                        try:
                            self._ensure_photo(working, best, hit_st)
                        except Exception:
                            pass
            else:
                summary["skipped_no_name"] += 1
                _log("    ↳ skip (need first+last or existing URL)")

            # Build DB patch from filled fields
            for key in (
                "source_url", "external_id", "photo_url", "photo_path",
                "report_html_path", "race", "ethnicity", "gender", "height",
                "weight", "eye_color", "hair_color", "crime", "offense_type",
                "offense_description", "county", "city", "address", "risk_level",
                "state", "source_state", "date_of_birth", "age", "zip_code",
                "latitude", "longitude", "flags", "raw_data_json",
            ):
                new_v = working.get(key)
                old_v = rec.get(key)
                if not new_v:
                    continue
                if not old_v or (
                    key in (
                        "race", "ethnicity", "crime", "photo_path",
                        "report_html_path", "source_url", "photo_url",
                    )
                    and str(new_v) != str(old_v or "")
                ):
                    patch[key] = new_v

            if patch and rid is not None:
                try:
                    ok = self.db.update_offender(int(rid), patch)
                except Exception as e:
                    summary["errors"] += 1
                    _log(f"    ↳ DB update error: {e}")
                    ok = False
                if ok:
                    summary["updated"] += 1
                    merged = dict(rec)
                    merged.update(patch)
                    if merged.get("photo_path"):
                        summary["with_photo"] += 1
                    if merged.get("race"):
                        summary["with_race"] += 1
                    _log(
                        f"    ↳ updated id={rid} "
                        f"race={patch.get('race') or '—'} "
                        f"{'photo ' if patch.get('photo_path') else ''}"
                        f"{'url ' if patch.get('source_url') else ''}"
                        f"{'html' if patch.get('report_html_path') else ''}"
                    )
                    if on_update:
                        try:
                            on_update(merged)
                        except Exception:
                            pass
                else:
                    _log(f"    ↳ no DB change for id={rid}")
            else:
                _log("    ↳ nothing new to write")

            if on_progress:
                try:
                    on_progress(summary["attempted"], total_q or 1)
                except Exception:
                    pass

        _log(
            f"Enrich done: attempted={summary['attempted']} updated={summary['updated']} "
            f"nsopw_matched={summary['nsopw_matched']} reports={summary['reports_fetched']} "
            f"errors={summary['errors']}"
        )
        return summary

    @staticmethod
    def _pick_nsopw_hit_for_person(
        rec: Dict[str, Any],
        hits: List[Any],
    ) -> Optional[Any]:
        """Choose the best NSOPW hit for an existing DB person."""
        if not hits:
            return None
        want_first = (rec.get("first_name") or "").strip().lower().split()[0:1]
        want_first_s = want_first[0] if want_first else ""
        want_last = (rec.get("last_name") or "").strip().lower()
        want_state = (
            rec.get("state") or rec.get("source_state") or ""
        ).strip().upper()

        def score(hit: Any) -> tuple:
            hf = (getattr(hit, "first_name", None) or "").strip().lower()
            hl = (getattr(hit, "last_name", None) or "").strip().lower()
            full = (getattr(hit, "full_name", None) or "").strip().lower()
            st = (
                getattr(hit, "state", None)
                or getattr(hit, "jurisdiction_id", None)
                or ""
            ).strip().upper()
            s = 0
            if want_last and (hl == want_last or want_last in full):
                s += 5
            if want_first_s and (hf.startswith(want_first_s) or want_first_s in full):
                s += 3
            if want_state and st and (st == want_state or want_state in st):
                s += 2
            if getattr(hit, "image_uri", None) or getattr(hit, "offender_uri", None):
                s += 1
            return (s, 1 if getattr(hit, "image_uri", None) else 0)

        ranked = sorted(hits, key=score, reverse=True)
        best = ranked[0]
        if score(best)[0] < 5:
            # Require at least last-name match
            return None
        return best

    def _url_exists(self, url: str) -> bool:
        u = (url or "").strip()
        if not u:
            return False
        if u in self._known_urls:
            return True
        row = self.db._conn.execute(
            "SELECT 1 FROM offenders WHERE source_url = ? LIMIT 1",
            (u,),
        ).fetchone()
        if row is not None:
            self._known_urls.add(u)
            return True
        return False

    def _remember_url(self, url: str) -> None:
        u = (url or "").strip()
        if u:
            self._known_urls.add(u)

    def _merge_demographics(self, record: Dict[str, Any], demo: Dict[str, Any]) -> None:
        """
        Merge report-page demographics into *record* without erasing other sources.

        Race/ethnicity from HTML are stored as a separate sources_json entry so
        a bulk CSV value (e.g. FL ``W``) coexists with a jurisdiction value
        (e.g. CO ``Asian``). Top-level race is rewritten to a multi-source
        display when they disagree.
        """
        from scraper.database.sources import (
            TRACKED_FIELDS,
            attach_source_to_record,
            extract_tracked_fields,
            jurisdiction_from_url,
            make_source,
        )

        report_ok = bool(demo.get("report_fetch_ok"))
        url = (
            (demo.get("report_final_url") or demo.get("report_url") or record.get("source_url") or "")
            .strip()
        )
        if " | " in url:
            url = url.split(" | ", 1)[0].strip()
        jur = (
            (record.get("state") or record.get("source_state") or "")
            or jurisdiction_from_url(url)
        )
        if isinstance(jur, str) and " | " in jur:
            jur = jur.split(" | ", 1)[0].strip()
        jur = str(jur or "").strip().upper()

        # Field values observed on this report fetch (only non-empty)
        demo_fields: Dict[str, Any] = {}
        for key in TRACKED_FIELDS:
            val = demo.get(key)
            if val is None or val == "":
                continue
            demo_fields[key] = val if not isinstance(val, str) else val.strip()
        # Also pull crime aliases
        if not demo_fields.get("crime"):
            for k in ("offense_description", "offense_type"):
                if demo.get(k):
                    demo_fields["crime"] = str(demo.get(k)).strip()
                    break

        html_status = "ok" if report_ok else "empty"
        if demo.get("report_block_reason"):
            html_status = f"blocked:{demo.get('report_block_reason')}"
        elif str(demo.get("report_fetch_status") or "").startswith("error"):
            html_status = str(demo.get("report_fetch_status"))
        elif str(demo.get("report_fetch_status") or "").startswith("blocked"):
            html_status = str(demo.get("report_fetch_status"))

        report_src = make_source(
            source_type="report_html" if report_ok else "nsopw_report",
            jurisdiction=jur,
            origin="report_fetch",
            label=f"{jur or 'Registry'} report HTML",
            external_id=str(record.get("external_id") or ""),
            source_url=url,
            fields=demo_fields,
            html_path=(demo.get("report_html_path") or record.get("report_html_path") or None),
            html_verified=report_ok and bool(demo_fields.get("race") or demo_fields.get("crime")),
            html_status=html_status,
        )
        # Preserve any pre-existing sources (e.g. FL CSV) and add/update this one
        attach_source_to_record(record, report_src, prefer_new_fields=True)

        # Top-level fill: never overwrite a different source's race with blank;
        # multi-source display already applied. Fill blanks for other fields.
        for key in (
            "ethnicity", "gender", "height", "weight",
            "eye_color", "hair_color", "skin_tone", "build", "age",
            "date_of_birth", "county", "city", "address", "risk_level",
            "offense_type", "offense_description", "crime",
            "photo_path", "photo_url", "report_html_path",
        ):
            val = demo.get(key)
            if val is None or val == "":
                continue
            if key in ("crime", "offense_type", "offense_description"):
                if not record.get(key):
                    record[key] = val
            elif not record.get(key):
                record[key] = val
            elif key in ("photo_path", "photo_url", "report_html_path"):
                if not record.get(key):
                    record[key] = val

        # Keep crime in sync with offense fields if only one side was set
        if not record.get("crime"):
            odesc = (record.get("offense_description") or "").strip()
            otype = (record.get("offense_type") or "").strip()
            if odesc or otype:
                record["crime"] = odesc or otype

        try:
            raw = json.loads(record.get("raw_data_json") or "{}")
            if not isinstance(raw, dict):
                raw = {}
        except json.JSONDecodeError:
            raw = {}
        # Preserve original NSOPW payload if present; nest enrichment
        raw["report_enrichment"] = {
            k: demo.get(k)
            for k in (
                "report_url", "report_final_url", "report_resolved_url",
                "report_fetch_status", "report_fetch_ok", "report_html_path",
                "report_block_reason", "photo_path", "photo_url",
                "race", "ethnicity", "gender",
                "height", "weight", "hair_color", "eye_color",
            )
            if k in demo
        }
        record["raw_data_json"] = json.dumps(raw, ensure_ascii=False)[:50000]

        try:
            flags = json.loads(record.get("flags") or "[]")
            if isinstance(flags, dict):
                tags = [str(t) for t in (flags.get("tags") or [])]
                flag_mode = "dict"
                flag_dict = flags
            elif isinstance(flags, list):
                tags = [str(t) for t in flags]
                flag_mode = "list"
                flag_dict = {}
            else:
                tags = [str(flags)]
                flag_mode = "list"
                flag_dict = {}
        except json.JSONDecodeError:
            tags = []
            flag_mode = "list"
            flag_dict = {}

        def _tag(t: str) -> None:
            if t not in tags:
                tags.append(t)

        if demo.get("report_html_path"):
            _tag("html_archived")
        if demo.get("photo_path"):
            _tag("photo_archived")
        if demo.get("report_fetch_ok"):
            _tag("report_enriched")
        else:
            _tag("report_link_saved")
            if demo.get("report_block_reason"):
                _tag(f"blocked:{demo['report_block_reason']}")
        _tag("multi_source")

        if flag_mode == "dict":
            flag_dict["tags"] = tags
            record["flags"] = json.dumps(flag_dict, ensure_ascii=False)
        else:
            record["flags"] = json.dumps(tags)

    def _ensure_photo(self, record: Dict[str, Any], hit: Any, jurisdiction: str) -> None:
        """Download / attach a local offender photo when possible.

        Priority:
          1. Dedicated NSOPW/state photo URL (imageUri / photo_url) — real mugshot
          2. Best image from archived report HTML assets (JPEG/PNG preferred)
          3. Keep existing path only if it is already a solid mugshot file

        HTML assets often include large site chrome GIFs (FL FDLE banners ~30KB).
        Those must never block a dedicated CallImage / imageUri download.
        """
        min_primary = int(getattr(self.reports, "MIN_PRIMARY_PHOTO_BYTES", 2000) or 2000)
        min_any = int(getattr(self.reports, "MIN_PHOTO_BYTES", 80) or 80)

        def _file_ok(path: str, min_bytes: int) -> bool:
            try:
                p = Path(path)
                return p.is_file() and p.stat().st_size >= min_bytes
            except OSError:
                return False

        def _looks_like_mugshot(path: str) -> bool:
            """True for local files that are likely offender photos, not site chrome."""
            try:
                p = Path(path)
                if not p.is_file():
                    return False
                sz = p.stat().st_size
                if sz < min_any:
                    return False
                try:
                    from scraper.mugshot_ethnicity.photo_quality import is_non_mugshot

                    if is_non_mugshot(p):
                        return False
                except Exception:
                    pass
                ext = p.suffix.lower()
                # GIFs on state sites are almost always logos/banners/spacers
                if ext == ".gif":
                    return False
                # Files under HTML *_assets/ are frequently shared site chrome
                # (even large PNGs). Only trust dedicated …/photos/ downloads
                # as final mugshots when a photo_url exists.
                parts_l = [x.lower() for x in p.parts]
                in_assets = any(x.endswith("_assets") or x == "assets" for x in parts_l)
                in_photos = "photos" in parts_l
                if ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
                    if in_photos and sz >= 500:
                        return True
                    if in_assets:
                        # Asset only counts as mugshot if fairly large JPEG-like
                        # and not a known tiny placeholder size band
                        return ext in (".jpg", ".jpeg") and sz >= 5000
                    return sz >= min_primary
                return False
            except OSError:
                return False

        def _set_path(path: str, *, from_url: bool = False) -> None:
            # Never persist GIFs as the offender photo
            if path and str(path).lower().endswith(".gif"):
                return
            try:
                from scraper.mugshot_ethnicity.photo_quality import is_non_mugshot

                if path and is_non_mugshot(path):
                    return
            except Exception:
                pass
            record["photo_path"] = path
            self.stats.photos_saved += 1
            if not from_url:
                return
            try:
                flags = json.loads(record.get("flags") or "[]")
                if not isinstance(flags, list):
                    flags = [str(flags)]
            except json.JSONDecodeError:
                flags = []
            if "photo_archived" not in flags:
                flags.append("photo_archived")
            record["flags"] = json.dumps(flags)

        existing = (record.get("photo_path") or "").strip()
        existing_is_mugshot = bool(existing and _looks_like_mugshot(existing))
        existing_in_photos = bool(
            existing and "photos" in [x.lower() for x in Path(existing).parts]
        )

        from scraper.report_fetcher import extract_dedicated_photo_urls, photo_state_from_url

        def _download_dedicated(url: str) -> Optional[str]:
            """Download mugshot into html_dir/<jur>/photos/ and return local path."""
            host_st = photo_state_from_url(url)
            jur_raw = (
                host_st
                or jurisdiction
                or record.get("state")
                or record.get("source_state")
                or "UNK"
            )
            jur = re.sub(r"[^A-Za-z0-9_-]", "", str(jur_raw).upper())[:12] or "UNK"
            # WatchSystems CDN serves many states — keep under NSOPW/record jurisdiction
            if "watchsystems.com" in url.lower() and not host_st:
                jur = re.sub(
                    r"[^A-Za-z0-9_-]",
                    "",
                    str(jurisdiction or record.get("state") or record.get("source_state") or "UNK").upper(),
                )[:12] or "UNK"
            photo_dir = self.html_dir / jur / "photos"
            stem = sha1(
                (url + "|" + (record.get("source_url") or "")).encode(
                    "utf-8", errors="replace"
                )
            ).hexdigest()[:16]
            referer = (record.get("source_url") or "").strip()
            if not referer and "watchsystems.com" in url.lower():
                referer = "https://www.icrimewatch.net/"
            if not referer:
                referer = "https://www.nsopw.gov/"
            return self.reports.download_photo(
                url,
                photo_dir,
                referer=referer,
                stem=stem,
                reject_gif=True,
            )

        # 1) Dedicated mugshot URL when present — always preferred over HTML assets
        photo_url = (
            (record.get("photo_url") or "").strip()
            or (getattr(hit, "image_uri", None) or "").strip()
        )
        # Recover WatchSystems /pictures/ URL from archived HTML when imageUri missing
        html_path = (record.get("report_html_path") or "").strip()
        if not photo_url and html_path:
            try:
                raw_html = Path(html_path).read_text(encoding="utf-8", errors="replace")
                dedicated = extract_dedicated_photo_urls(raw_html)
                if dedicated:
                    photo_url = dedicated[0]
            except Exception:
                pass

        if photo_url:
            record["photo_url"] = photo_url
            # Only skip network if we already have a dedicated photos/ download
            if not (existing_is_mugshot and existing_in_photos):
                path = _download_dedicated(photo_url)
                if path and _file_ok(path, min_any) and not str(path).lower().endswith(".gif"):
                    _set_path(path, from_url=True)
                    return

        if existing_is_mugshot and existing_in_photos:
            return
        if existing_is_mugshot and not photo_url:
            # Keep decent asset JPEG only when no dedicated URL exists
            return

        # 2) Best image from report HTML assets (not GIF; prefer portrait JPEG)
        if html_path:
            best = self._best_asset_photo(html_path, min_bytes=min_any)
            if best and _looks_like_mugshot(best):
                try:
                    rel = str(Path(best).relative_to(Path.cwd()))
                except ValueError:
                    rel = best
                _set_path(rel, from_url=False)
                return

        # 3) Keep a weak existing path only if nothing better is available
        if existing and _file_ok(existing, min_any) and not existing.lower().endswith(".gif"):
            try:
                from scraper.mugshot_ethnicity.photo_quality import is_non_mugshot

                if is_non_mugshot(existing):
                    record["photo_path"] = None
                    return
            except Exception:
                pass
            # Prefer clearing shared asset placeholders so integrity shows missing
            parts_l = [x.lower() for x in Path(existing).parts]
            if any(x.endswith("_assets") for x in parts_l) and photo_url:
                record["photo_path"] = None
                return
            return
        # Drop GIF / chrome placeholders so integrity shows missing photo
        if existing and existing.lower().endswith(".gif"):
            record["photo_path"] = None
            return
        try:
            from scraper.mugshot_ethnicity.photo_quality import is_non_mugshot

            if existing and is_non_mugshot(existing):
                record["photo_path"] = None
        except Exception:
            pass

    @staticmethod
    def _best_asset_photo(html_path: str, *, min_bytes: int = 80) -> Optional[str]:
        """Pick the most likely mugshot under {stem}_assets next to archived HTML."""
        hp = Path(html_path)
        assets = hp.parent / f"{hp.stem}_assets"
        if not assets.is_dir():
            return None
        best: Optional[Tuple[int, int, Path]] = None  # score, size, path
        try:
            from scraper.mugshot_ethnicity.photo_quality import is_non_mugshot
        except Exception:
            is_non_mugshot = lambda _p: False  # type: ignore
        for cand in assets.iterdir():
            if not cand.is_file():
                continue
            if cand.suffix.lower() not in (
                ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"
            ):
                continue
            try:
                sz = cand.stat().st_size
            except OSError:
                continue
            if sz < min_bytes:
                continue
            try:
                if is_non_mugshot(cand):
                    continue
            except Exception:
                pass
            name = cand.name.lower()
            ext = cand.suffix.lower()
            score = 0
            # FL and others ship large banner/logo GIFs — never prefer them
            if ext == ".gif":
                score -= 40
            if ext in (".jpg", ".jpeg", ".png", ".webp"):
                score += 12
            for bad in (
                "logo", "icon", "sprite", "pixel", "spacer", "banner", "button",
                "header", "footer", "nav", "seal", "badge", "map",
            ):
                if bad in name:
                    score -= 25
            for good in ("photo", "offender", "mug", "portrait", "face", "sor", "image"):
                if good in name:
                    score += 10
            if sz >= 2000:
                score += 8
            # Mild size preference (aspect ratio below matters more for AL banners)
            score += min(sz // 20000, 3)
            # Prefer portrait/square; penalize wide office banners (~800x200)
            try:
                from PIL import Image

                with Image.open(cand) as im:
                    w, h = im.size
                if w > 0 and h > 0:
                    if min(w, h) < 40:
                        score -= 25
                    ratio = max(w, h) / float(min(w, h))
                    if ratio >= 2.4:
                        score -= 30
                    ar = w / float(h)
                    if 0.55 <= ar <= 1.35:
                        score += 14
            except Exception:
                pass
            if best is None or (score, sz) > (best[0], best[1]):
                best = (score, sz, cand)
        if best is None:
            return None
        # Reject GIF winners entirely
        if best[2].suffix.lower() == ".gif":
            return None
        return str(best[2])
