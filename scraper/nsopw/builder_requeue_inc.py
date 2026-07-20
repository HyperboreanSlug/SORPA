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

        try:
            want = int(limit)
        except (TypeError, ValueError):
            want = 100
        # limit <= 0 → process every incomplete row (no artificial 5k/500 cap).
        unlimited = want <= 0
        if unlimited:
            fetch_limit = 0
        else:
            fetch_limit = max(want, 1)
        scope = (source_scope or "all").strip().lower()
        eth_filt = (ethnicity_filter or "").strip().lower() or None
        if eth_filt == "all":
            eth_filt = None
        # Over-fetch before ranking so limit batches are not just "newest N rows"
        # (id DESC) which often are cross-jurisdiction shells / captcha URLs.
        if not unlimited:
            mult = 1
            if scope != "all" or eth_filt:
                mult = max(mult, 8)
            if state and str(state).upper() not in ("", "ALL"):
                mult = max(mult, 6)
            if mult > 1:
                fetch_limit = max(fetch_limit * mult, fetch_limit)

        from scraper.database.db_retry import retry_on_db_lock

        def _load_rows():
            return self.db.find_incomplete_reports(
                need_race=need_race,
                need_crime=need_crime,
                need_photo=need_photo,
                need_html=need_html,
                require_url=True,
                limit=fetch_limit,
                state=state,
            )

        try:
            rows = retry_on_db_lock(
                _load_rows,
                attempts=10,
                base_delay=0.5,
                max_delay=8.0,
                log=_log,
                what="load incomplete reports",
            )
        except Exception as e:
            _log(f"Requeue aborted: could not read incomplete queue: {e}")
            raise
        filtered, skipped_scope = filter_records_for_enrich(
            rows,
            source_scope=scope,
            ethnicity_filter=eth_filt,
            min_confidence=min_confidence,
            ethnic_db=self.ethnic_db,
        )

        # Rank *before* applying the batch limit so FDLE/same-registry URLs
        # win over NY/WI captcha shells attached to FL residents.
        filtered = sorted(filtered, key=self._requeue_rank_key)
        if not unlimited and want > 0:
            filtered = filtered[:want]

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
