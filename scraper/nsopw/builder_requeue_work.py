"""Sequential + parallel workers for incomplete report requeue."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from scraper.nsopw.builder_types import *  # noqa: F401,F403
from scraper.nsopw.parallel import JurisdictionReportPool, ReportJob


class BuilderRequeueWorkMixin:
    def _requeue_sequential(
        self,
        filtered: List[Dict[str, Any]],
        *,
        summary: Dict[str, Any],
        total_q: int,
        save_html: bool,
        log: Callable[[str], None],
        on_update: Optional[Callable[[Dict[str, Any]], None]],
        on_progress: Optional[Callable[[int, int], None]],
    ) -> None:
        for rec in filtered:
            if self.cancel_check():
                log("Requeue cancelled.")
                break
            if self.report_limiter.wait(self.cancel_check):
                log("Requeue cancelled (during delay).")
                break
            if self.cancel_check():
                log("Requeue cancelled.")
                break
            prepared = self._requeue_prepare(rec)
            if prepared is None:
                summary["errors"] += 1
                continue
            summary["attempted"] += 1
            rid, st, name, fetch_url = prepared
            log(f"  [{summary['attempted']}/{total_q}] Re-fetch [{st}] {name[:50]}")
            try:
                demo = self.reports.fetch_demographics(
                    fetch_url,
                    save_html=save_html,
                    html_dir=self.html_dir,
                    jurisdiction=st,
                )
            except Exception as e:
                summary["errors"] += 1
                log(f"    ↳ error: {e}")
                self._requeue_progress(on_progress, summary["attempted"], total_q)
                continue
            record = dict(rec)
            self._merge_demographics(record, demo)
            if demo.get("report_html_path"):
                record["report_html_path"] = demo["report_html_path"]
            if demo.get("photo_path"):
                record["photo_path"] = demo["photo_path"]

            class _Hit:
                image_uri = rec.get("photo_url") or ""

            self._ensure_photo(record, _Hit(), st)
            self._requeue_apply_patch(
                rec, record, demo, summary, log=log, on_update=on_update
            )
            self._requeue_progress(on_progress, summary["attempted"], total_q)

    def _requeue_parallel(
        self,
        filtered: List[Dict[str, Any]],
        *,
        summary: Dict[str, Any],
        total_q: int,
        save_html: bool,
        threads: int,
        log: Callable[[str], None],
        on_update: Optional[Callable[[Dict[str, Any]], None]],
        on_progress: Optional[Callable[[int, int], None]],
    ) -> None:
        originals: Dict[int, Dict[str, Any]] = {}
        jobs: List[ReportJob] = []
        for rec in filtered:
            prepared = self._requeue_prepare(rec)
            if prepared is None:
                summary["errors"] += 1
                continue
            rid, st, name, fetch_url = prepared
            try:
                rid_i = int(rid)
            except (TypeError, ValueError):
                summary["errors"] += 1
                continue
            originals[rid_i] = rec

            class _Hit:
                image_uri = rec.get("photo_url") or ""

            jobs.append(
                ReportJob(
                    jurisdiction=st,
                    url=fetch_url,
                    record=dict(rec),
                    hit=_Hit(),
                    is_eth_match=False,
                    save_html=save_html,
                    names_label=name,
                )
            )

        if not jobs:
            return

        max_per = max(1, min(int(threads), MAX_REPORT_THREADS))
        log(
            f"  Parallel requeue: {len(jobs)} jobs · {threads} threads "
            f"(max {max_per}/jurisdiction, delay={self.report_delay}s)"
        )
        pool = JurisdictionReportPool(
            num_threads=threads,
            make_fetcher=self._make_report_fetcher,
            worker_fn=self._worker_fetch,
            report_delay=self.report_delay,
            cancel_check=self.cancel_check,
            log=log,
            max_per_jurisdiction=max_per,
        )
        try:
            for job in jobs:
                pool.submit(job)
            for done in pool.collect(len(jobs)):
                if self.cancel_check():
                    log("Requeue cancelled.")
                    break
                summary["attempted"] += 1
                rec = originals.get(int(done.record.get("id") or 0)) or done.record
                name = done.names_label or f"id={done.record.get('id')}"
                st = done.jurisdiction
                log(
                    f"  [{summary['attempted']}/{total_q}] "
                    f"Re-fetch [{st}] {name[:50]}"
                )
                if done.error:
                    summary["errors"] += 1
                    log(f"    ↳ error: {done.error}")
                elif done.demo is None and not done.fetched:
                    summary["errors"] += 1
                    log("    ↳ skipped/cancelled")
                else:
                    demo = done.demo or {}
                    self._requeue_apply_patch(
                        rec,
                        done.record,
                        demo,
                        summary,
                        log=log,
                        on_update=on_update,
                    )
                self._requeue_progress(on_progress, summary["attempted"], total_q)
        finally:
            pool.close()

    def _requeue_prepare(
        self, rec: Dict[str, Any]
    ) -> Optional[tuple]:
        url = (rec.get("source_url") or "").strip()
        if not url:
            return None
        rid = rec.get("id")
        st = (rec.get("state") or rec.get("source_state") or "UNK").upper()
        name = (
            (rec.get("full_name") or "").strip()
            or f"{rec.get('first_name') or ''} {rec.get('last_name') or ''}".strip()
            or f"id={rid}"
        )
        fetch_url = self._primary_fetch_url(url, st)
        if not fetch_url:
            return None
        return rid, st, name, fetch_url

    def _requeue_apply_patch(
        self,
        rec: Dict[str, Any],
        record: Dict[str, Any],
        demo: Dict[str, Any],
        summary: Dict[str, Any],
        *,
        log: Callable[[str], None],
        on_update: Optional[Callable[[Dict[str, Any]], None]],
    ) -> None:
        rid = rec.get("id")
        patch: Dict[str, Any] = {}
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
                if new_v != old_v:
                    patch[key] = new_v
                continue
            if new_v and (not old_v or (key in ("crime",) and new_v != old_v)):
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
                if (
                    merged.get("crime")
                    or merged.get("offense_description")
                    or merged.get("offense_type")
                ):
                    summary["with_crime"] += 1
                if merged.get("photo_path"):
                    summary["with_photo"] += 1
                if merged.get("report_html_path"):
                    summary["with_html"] += 1
                log(
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
                log(f"    ↳ no DB change for id={rid}")
        else:
            log(
                f"    ↳ no new fields "
                f"(status={demo.get('report_fetch_status')} "
                f"{demo.get('report_block_reason') or ''})"
            )

    @staticmethod
    def _requeue_progress(
        on_progress: Optional[Callable[[int, int], None]],
        done: int,
        total: int,
    ) -> None:
        if not on_progress:
            return
        try:
            on_progress(done, total or 1)
        except Exception:
            pass
