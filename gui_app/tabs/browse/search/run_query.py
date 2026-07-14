"""Browse → Search query runner."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class SearchQueryMixin:
    def _do_search(
        self, name=None, state=None, race=None, ethnicity=None, *_args, **_kwargs
    ):
        """Run search on the UI thread and fill the results tree.

        Keep this synchronous: queries are typically <1s, and scheduling
        tree updates via ``after`` from a worker thread is unreliable on
        Windows (blank table at open).
        """
        try:
            name_ui = (self.search_name_var.get() or "").strip()
            state_ui = (self.search_state_var.get() or "").strip().upper()
            race_ui = (self.search_race_var.get() or "").strip()
            eth_ui = (
                (self.search_ethnicity_var.get() or "").strip()
                if hasattr(self, "search_ethnicity_var")
                else ""
            )
        except Exception:
            name_ui, state_ui, race_ui, eth_ui = "", "", "", ""

        # Explicit kwargs win; None means “read from widgets”.
        name = name_ui if name is None else (name or "").strip()
        state = state_ui if state is None else (state or "").strip().upper()
        race = race_ui if race is None else (race or "").strip()
        eth = eth_ui if ethnicity is None else (ethnicity or "").strip()
        state_f = state if state and state != "ALL" else None
        race_f = race or None
        eth_f = eth or None

        try:
            self.search_status.configure(text="Searching…")
        except Exception:
            pass

        from scraper.searcher import SexOffenderSearcher

        searcher = SexOffenderSearcher(
            db_path=str(getattr(self, "db_path", None) or "data/offenders.db")
        )
        try:
            try:
                records, status = self._search_run_query(
                    searcher, name, state_f, race_f, eth_f
                )
            except Exception as e:
                try:
                    self._populate_search_tree([])
                except Exception:
                    pass
                try:
                    self.search_status.configure(text=f"Search error: {e}")
                except Exception:
                    pass
                try:
                    self.log_queue.put(f"Search error: {e}")
                except Exception:
                    pass
                return
            self._populate_search_tree(records)
            try:
                self.search_status.configure(text=status)
            except Exception:
                pass
        finally:
            searcher.close()

    @staticmethod
    def _search_run_query(
        searcher: Any,
        name: str,
        state_f: Optional[str],
        race_f: Optional[str],
        eth_f: Optional[str],
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Pure query logic shared by the UI path."""
        if name:
            results = searcher.search_by_name(
                name=name,
                state=state_f,
                race=race_f if race_f and race_f.upper() != "INDIAN" else None,
                limit=500,
            )
            records = list(results.records)
            if race_f and race_f.upper() == "INDIAN":
                records = [
                    r
                    for r in records
                    if "indian" in (r.get("race") or "").lower()
                    or "indian" in (r.get("ethnicity") or "").lower()
                    or "indian" in (r.get("likely_ethnicity") or "").lower()
                    or "south asian" in (r.get("race") or "").lower()
                ]
            if eth_f:
                eth_res = searcher.search_by_surname_ethnicity(
                    eth_f, state=state_f, limit=5000
                )
                allowed = {
                    (
                        (r.get("last_name") or "").strip().lower(),
                        (r.get("full_name") or "").strip().lower(),
                    )
                    for r in eth_res.records
                }
                last_only = {a[0] for a in allowed if a[0]}
                records = [
                    r
                    for r in records
                    if (
                        (r.get("last_name") or "").strip().lower(),
                        (r.get("full_name") or "").strip().lower(),
                    )
                    in allowed
                    or (r.get("last_name") or "").strip().lower() in last_only
                ]
            filt = [x for x in (state_f, race_f, eth_f) if x]
            extra = f" · {', '.join(filt)}" if filt else ""
            status = (
                f"{len(records)} name matches{extra} · "
                f"{results.query_time_ms:.0f} ms"
            )
            return records, status

        if eth_f:
            results = searcher.search_by_surname_ethnicity(
                eth_f, state=state_f, limit=500
            )
            records = list(results.records)
            if race_f:
                if race_f.upper() == "INDIAN":
                    records = [
                        r
                        for r in records
                        if "indian" in (r.get("race") or "").lower()
                        or "indian" in (r.get("ethnicity") or "").lower()
                        or "indian" in (r.get("likely_ethnicity") or "").lower()
                        or "south asian" in (r.get("race") or "").lower()
                        or not (r.get("race") or "").strip()
                    ]
                else:
                    records = [
                        r
                        for r in records
                        if (r.get("race") or "").strip().upper() == race_f.upper()
                    ]
            where = f" · {state_f}" if state_f else ""
            status = (
                f"{len(records)} with surname ethnicity {eth_f}{where}"
                + (f" · race {race_f}" if race_f else "")
                + f" · {results.query_time_ms:.0f} ms"
            )
            return records, status

        if race_f:
            results = searcher.search_by_race(race=race_f, state=state_f, limit=500)
            where = f" · {state_f}" if state_f else ""
            return (
                list(results.records),
                f"{len(results.records)} with race {race_f}{where}",
            )

        if state_f:
            results = searcher.search_by_state(state=state_f, limit=500)
            return list(results.records), f"{len(results.records)} in {state_f}"

        results = searcher.search_by_state(state="ALL", limit=500)
        total = searcher.get_total_count()
        shown = len(results.records)
        status = (
            f"{shown} names"
            + (f" (of {total:,} total)" if total > shown else f" · {total:,} total")
            + " · select a row for detail"
        )
        return list(results.records), status
