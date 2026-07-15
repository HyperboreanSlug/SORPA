from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from scraper.nsopw.builder_types import *  # noqa: F401,F403


class BuilderRequeueIncMixin:
    def requeue_incomplete(
        self,
        *,
        need_race: bool = True,
        need_crime: bool = True,
        need_photo: bool = True,
        need_html: bool = False,
        limit: int = 100,
        state: Optional[str] = None,
        source_scope: str = "all",
        ethnicity_filter: Optional[str] = None,
        min_confidence: float = 0.5,
        save_html: bool = True,
        log: Optional[Callable[[str], None]] = None,
        on_update: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Any]:
        """
        Re-fetch jurisdiction reports for DB rows missing race/crime/photo/HTML.

        Updates existing offender rows in place (does not insert duplicates).
        on_progress(done, total) is called after each attempt when provided.

        source_scope:
          - ``all`` (default): any record with a source URL
          - ``external_imports``: bulk/direct CSV imports only
          - ``nsopw``: NSOPW-scraped rows only

        ethnicity_filter:
          When set (e.g. ``hispanic``, ``indian``), only rows whose surname
          classifies into that family at *min_confidence* are processed.

        When ``report_threads`` > 1, fetches use a worker pool. Same-state
        concurrency is allowed so single-jurisdiction passes (e.g. FL) actually
        parallelize; pacing still uses the shared per-jurisdiction delay.
        """
        from scraper.database.enrich_scope import filter_records_for_enrich

        def _log(msg: str) -> None:
            if log:
                log(msg)
            else:
                print(msg)

        fetch_limit = max(int(limit), 1)
        scope = (source_scope or "all").strip().lower()
        eth_filt = (ethnicity_filter or "").strip().lower() or None
        if eth_filt == "all":
            eth_filt = None
        if scope != "all" or eth_filt:
            # Over-fetch then filter so the final batch still reaches *limit*.
            fetch_limit = min(max(fetch_limit * 8, fetch_limit), 5000)

        rows = self.db.find_incomplete_reports(
            need_race=need_race,
            need_crime=need_crime,
            need_photo=need_photo,
            need_html=need_html,
            require_url=True,
            limit=fetch_limit,
            state=state,
        )
        filtered, skipped_scope = filter_records_for_enrich(
            rows,
            source_scope=scope,
            ethnicity_filter=eth_filt,
            min_confidence=min_confidence,
            ethnic_db=self.ethnic_db,
        )
        if int(limit) > 0:
            filtered = filtered[: int(limit)]

        summary: Dict[str, Any] = {
            "queued": len(filtered),
            "skipped_scope": skipped_scope,
            "attempted": 0,
            "updated": 0,
            "with_race": 0,
            "with_crime": 0,
            "with_photo": 0,
            "with_html": 0,
            "errors": 0,
            "threads": int(getattr(self, "report_threads", 1) or 1),
        }
        total_q = len(filtered)
        scope_note = ""
        if scope != "all" or eth_filt:
            scope_note = (
                f" scope={scope}"
                + (f" ethnicity={eth_filt}" if eth_filt else "")
                + f" (skipped {skipped_scope} by filter)"
            )
        thr = int(getattr(self, "report_threads", 1) or 1)
        _log(
            f"Requeue incomplete reports: {len(filtered)} candidates "
            f"(need race={need_race} crime={need_crime} photo={need_photo} "
            f"html={need_html}) threads={thr}{scope_note}"
        )
        if on_progress:
            try:
                on_progress(0, total_q or 1)
            except Exception:
                pass

        # Prefer same-state registry hosts first so FL/fdle work is not blocked
        # behind captcha walls on cross-jurisdiction URLs attached to a resident.
        def _sort_key(rec: Dict[str, Any]) -> tuple:
            st = (rec.get("state") or rec.get("source_state") or "").upper()
            url = (rec.get("source_url") or "").lower()
            same = 0 if st and st.lower() in url else 1
            return (same, int(rec.get("id") or 0))

        filtered = sorted(filtered, key=_sort_key)

        if thr <= 1:
            self._requeue_sequential(
                filtered,
                summary=summary,
                total_q=total_q,
                save_html=save_html,
                log=_log,
                on_update=on_update,
                on_progress=on_progress,
            )
        else:
            self._requeue_parallel(
                filtered,
                summary=summary,
                total_q=total_q,
                save_html=save_html,
                threads=thr,
                log=_log,
                on_update=on_update,
                on_progress=on_progress,
            )

        _log(
            f"Requeue done: attempted={summary['attempted']} "
            f"updated={summary['updated']} errors={summary['errors']} "
            f"threads={summary.get('threads', 1)}"
        )
        return summary
