from __future__ import annotations

import queue

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple


from scraper.nsopw.builder_types import *  # noqa: F401,F403
from scraper.database import Database
from scraper.ethnic_names import get_ethnic_database
from scraper.reports.fetcher import ReportFetcher
from scraper.nsopw.client import (
    DEFAULT_JURISDICTIONS,
    NSOPWClient,
    NSOPWOffender,
    normalize_jurisdiction_code,
)
from scraper.nsopw.parallel import JurisdictionReportPool, ReportJob

class BuilderEnrichRunMixin:
    def enrich_misclassified(
        self,
        records: List[Dict[str, Any]],
        *,
        limit: int = 50,
        prefer_missing_photo: bool = True,
        only_missing_data: bool = True,
        enrich_reports: bool = True,
        source_scope: str = "all",
        ethnicity_filter: Optional[str] = None,
        min_confidence: float = 0.5,
        save_html: bool = True,
        log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
        on_update: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        NSOPW / report refresh for misclassification candidates.

        By default only rows **missing** photo, race, crime, or source URL are
        processed (complete rows are skipped).

        source_scope / ethnicity_filter:
          Same semantics as ``requeue_incomplete``. Use ``external_imports``
          to enrich only bulk/direct CSV imports; set ``ethnicity_filter`` to
          the Misclassify ethnicity combo (e.g. ``hispanic``, ``indian``).

        For each person still needing data:
          1. If they already have a source_url → re-fetch report (photo/race/crime).
          2. Else search NSOPW by first+last, pick best matching hit, attach URL
             and optional report/photo enrichment, update the existing DB row.

        Does not insert new rows (avoids duplicates).
        """
        from scraper.database.enrich_scope import filter_records_for_enrich

        def _log(msg: str) -> None:
            if log:
                log(msg)
            else:
                print(msg)

        scope = (source_scope or "all").strip().lower()
        eth_filt = (ethnicity_filter or "").strip().lower() or None
        if eth_filt == "all":
            eth_filt = None

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

        queue, skipped_scope = filter_records_for_enrich(
            queue,
            source_scope=scope,
            ethnicity_filter=eth_filt,
            min_confidence=min_confidence,
            ethnic_db=self.ethnic_db,
        )

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
            "skipped_scope": skipped_scope,
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
        scope_note = ""
        if scope != "all" or eth_filt:
            scope_note = (
                f" scope={scope}"
                + (f" ethnicity={eth_filt}" if eth_filt else "")
                + f" (skipped {skipped_scope} by filter)"
            )
        _log(
            f"NSOPW enrich misclassified: {total_q} incomplete "
            f"(skipped complete={skipped_complete}, "
            f"only_missing={only_missing_data}, reports={enrich_reports})"
            f"{scope_note}"
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
            path_a_ok = False
            had_url = bool(url)
            fetch_url = self._primary_fetch_url(url, st) if url else ""
            if fetch_url and enrich_reports:
                if self.report_limiter.wait(self.cancel_check):
                    _log("Enrich cancelled (during delay).")
                    break
                if self.cancel_check():
                    break
                try:
                    demo = self.reports.fetch_demographics(
                        fetch_url,
                        save_html=save_html,
                        html_dir=self.html_dir,
                        jurisdiction=st if st != "UNK" else None,
                    )
                    summary["reports_fetched"] += 1
                    self._merge_demographics(working, demo)
                    class _Hit:
                        image_uri = working.get("photo_url") or rec.get("photo_url") or ""

                    self._ensure_photo(working, _Hit(), st)
                    path_a_ok = bool(demo.get("report_fetch_ok", True))
                except Exception as e:
                    summary["errors"] += 1
                    _log(f"    ↳ report error: {e}")

            # --- Path B: NSOPW name search ONLY when no existing URL ---
            # Never replace an existing source_url after a transient fetch failure —
            # weak name matches can attach the wrong person permanently.
            run_path_b = bool(first and last) and not had_url

            if run_path_b:
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
                    _log(
                        f"    ↳ no confident NSOPW match among {len(hits)} hit(s) "
                        "(need exact last + first/DOB; ties rejected)"
                    )
                else:
                    summary["nsopw_matched"] += 1
                    hit_rec = best.to_record()
                    for key in (
                        "source_url", "external_id", "photo_url", "state",
                        "source_state", "city", "address", "zip_code",
                        "latitude", "longitude", "gender", "date_of_birth", "age",
                    ):
                        val = hit_rec.get(key)
                        # Fill blanks only — never overwrite existing identity fields
                        if val and not working.get(key):
                            working[key] = val
                            patch[key] = val
                    url = (working.get("source_url") or "").strip()
                    hit_st = (
                        (hit_rec.get("state") or hit_rec.get("source_state") or st) or "UNK"
                    ).upper()
                    _log(f"    ↳ matched NSOPW url={(url or '')[:80]}")

                    fetch_url = self._primary_fetch_url(url, hit_st) if url else ""
                    if enrich_reports and fetch_url:
                        if self.report_limiter.wait(self.cancel_check):
                            _log("Enrich cancelled (during delay).")
                            break
                        if self.cancel_check():
                            break
                        try:
                            demo = self.reports.fetch_demographics(
                                fetch_url,
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
            elif had_url and not path_a_ok and enrich_reports:
                _log(
                    "    ↳ kept existing URL after report failure "
                    "(no NSOPW re-match — avoids wrong-person overwrite)"
                )
            elif not had_url and (not first or not last):
                summary["skipped_no_name"] += 1
                _log("    ↳ skip (need first+last or existing URL)")

            # Build DB patch — align with requeue: persist sources_json; fill blanks
            for key in (
                "source_url", "external_id", "photo_url", "photo_path",
                "report_html_path", "race", "ethnicity", "gender", "height",
                "weight", "eye_color", "hair_color", "crime", "offense_type",
                "offense_description", "county", "city", "address", "risk_level",
                "state", "source_state", "date_of_birth", "age", "zip_code",
                "latitude", "longitude", "flags", "sources_json", "raw_data_json",
            ):
                new_v = working.get(key)
                old_v = rec.get(key)
                if new_v is None or new_v == "":
                    continue
                if key in ("sources_json", "flags", "race"):
                    if new_v != old_v:
                        patch[key] = new_v
                    continue
                # Fill empty fields; allow crime/html/photo updates from verified report
                if not old_v or (
                    key in ("crime", "photo_path", "report_html_path", "ethnicity")
                    and str(new_v) != str(old_v or "")
                ):
                    if key == "source_url" and old_v:
                        # Never replace an existing source_url via enrich
                        continue
                    if key == "photo_url" and old_v:
                        continue
                    patch[key] = new_v

            if patch and rid is not None:
                try:
                    from scraper.database.db_retry import retry_on_db_lock

                    lock = getattr(self, "_db_write_lock", None)

                    def _write() -> bool:
                        if lock is not None:
                            with lock:
                                return self.db.update_offender(int(rid), patch)
                        return self.db.update_offender(int(rid), patch)

                    ok = retry_on_db_lock(
                        _write,
                        attempts=8,
                        base_delay=0.5,
                        max_delay=10.0,
                        log=_log,
                        what=f"enrich apply id={rid}",
                    )
                except Exception as e:
                    summary["errors"] += 1
                    _log(
                        f"    ↳ DB update error id={rid} after retries: {e} "
                        "(continuing)"
                    )
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

    # --- State-wide overnight enrich (re-fetch flyers, flag dead links) -------
    def _looks_dead_link(self, final_url: str, demo: Dict[str, Any]) -> bool:
        """True only when a listing is genuinely dead/removed (404/410/error404).

        Captcha/WAF blocks (403/429/503) and empty pages are NOT dead — they are
        temporary and must never be flagged blocked:http_404.
        """
        from scraper.public_links import _is_fdle_error_page

        if self._is_captcha_block(demo):
            return False
        status = demo.get("report_fetch_status")
        if isinstance(status, int) and status in (404, 410):
            return True
        s = str(status or "")
        if "404" in s or "410" in s or "http_404" in s or "http_410" in s:
            return True
        if _is_fdle_error_page(final_url or ""):
            return True
        br = str(demo.get("report_block_reason") or "").lower()
        if "http_404" in br or "http_410" in br:
            return True
        return False

    @staticmethod
    def _is_captcha_block(demo: Dict[str, Any]) -> bool:
        """True for temporary captcha/WAF walls (not a dead listing)."""
        if demo.get("needs_manual_captcha"):
            return True
        br = str(demo.get("report_block_reason") or "").lower()
        if "captcha" in br or "waf" in br:
            return True
        status = demo.get("report_fetch_status")
        if isinstance(status, int) and status in (403, 429, 503):
            return True
        return False

    @staticmethod
    def _mark_link_dead(rec: Dict[str, Any]) -> None:
        """Add blocked:http_404 to flags so the GUI opens the search home, not a dead page."""
        import json

        raw = rec.get("flags")
        tags: List[str] = []
        mode = "list"
        obj: Optional[Dict[str, Any]] = None
        if isinstance(raw, list):
            tags = [str(t) for t in raw]
        elif isinstance(raw, dict):
            obj = raw
            tags = [str(t) for t in (raw.get("tags") or [])]
            mode = "dict"
        elif isinstance(raw, str) and raw.strip():
            try:
                p = json.loads(raw)
                if isinstance(p, list):
                    tags = [str(t) for t in p]
                elif isinstance(p, dict):
                    obj = p
                    tags = [str(t) for t in (p.get("tags") or [])]
                    mode = "dict"
                else:
                    tags = [str(raw)]
            except Exception:
                tags = [str(raw)]
        if "blocked:http_404" not in tags:
            tags.append("blocked:http_404")
        if mode == "dict":
            obj = obj or {}
            obj["tags"] = tags
            rec["flags"] = json.dumps(obj, ensure_ascii=False)
        else:
            rec["flags"] = json.dumps(tags, ensure_ascii=False)

    def enrich_state(
        self,
        state: str,
        *,
        limit: int = 0,
        save_html: bool = True,
        log: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, int]:
        """Re-fetch report/flyer URLs for one state's records (overnight run).

        Fills missing demographics via the normal identity-gated merge and flags
        dead flyers (HTTP 404 / FDLE error404 landing) with ``blocked:http_404``
        so the GUI falls back to the registry search home instead of a dead page.
        Resumable: skips rows already flagged dead or already HTML-verified.
        """
        st = (state or "").strip().upper()

        def _log(m: str) -> None:
            if log:
                log(m)
            else:
                print(m, flush=True)

        rows = self.db._conn.execute(
            "SELECT * FROM offenders WHERE "
            "(source_state LIKE ? OR state LIKE ?) "
            "AND source_url IS NOT NULL AND TRIM(source_url) != '' "
            "AND (flags IS NULL OR (flags NOT LIKE '%blocked:http_404%' "
            "     AND flags NOT LIKE '%race_html_verified%')) "
            "AND (sources_json IS NULL OR sources_json NOT LIKE '%\"html_verified\": true%') "
            "ORDER BY id ASC",
            (f"%{st}%", f"%{st}%"),
        ).fetchall()
        if limit and int(limit) > 0:
            rows = rows[: int(limit)]
        total = len(rows)
        _log(f"[{st}] overnight enrich: {total:,} unverified records with a source URL")
        stats = {"checked": 0, "alive": 0, "dead": 0, "filled": 0, "empty": 0, "captcha": 0, "errors": 0}
        patch_cols = (
            "race", "ethnicity", "gender", "height", "weight", "eye_color",
            "hair_color", "photo_path", "photo_url", "report_html_path", "crime",
            "offense_type", "offense_description", "flags", "sources_json",
            "raw_data_json", "date_of_birth", "age", "city", "address", "county",
            "risk_level", "source_url",
        )
        consec_captcha = 0
        for i, row in enumerate(rows, 1):
            if self.cancel_check():
                _log(f"[{st}] enrich cancelled at {i:,}/{total:,}")
                break
            rec = dict(row)
            url = self._primary_fetch_url(rec.get("source_url") or "", st)
            if not url:
                continue
            stats["checked"] += 1
            if self.report_limiter.wait(self.cancel_check):
                _log(f"[{st}] enrich cancelled (during delay) at {i:,}/{total:,}")
                break
            try:
                demo = self.reports.fetch_demographics(
                    url, save_html=save_html, html_dir=self.html_dir, jurisdiction=st
                )
            except Exception as e:
                stats["errors"] += 1
                _log(f"  fetch error id={rec.get('id')}: {e}")
                continue
            ok = bool(demo.get("report_fetch_ok"))
            final_url = str(demo.get("report_final_url") or url)
            if ok:
                self._merge_demographics(rec, demo)

                class _Hit:
                    image_uri = rec.get("photo_url") or demo.get("photo_url") or ""

                try:
                    self._ensure_photo(rec, _Hit(), st)
                except Exception:
                    pass
                stats["filled"] += 1
                stats["alive"] += 1
                consec_captcha = 0
            elif self._is_captcha_block(demo):
                # Temporary captcha/WAF wall — never flag dead; leave for retry /
                # manual cookie solve (the fetcher already queued the URL).
                stats["captcha"] += 1
                consec_captcha += 1
            elif self._looks_dead_link(final_url, demo):
                self._mark_link_dead(rec)
                stats["dead"] += 1
                consec_captcha = 0
            else:
                # HTTP 200 but no demographics — empty / JS shell page, not dead.
                stats["empty"] += 1
                stats["alive"] += 1
                consec_captcha = 0
            # Circuit breaker: a run of consecutive captcha walls means the state
            # registry is bot-blocking us — stop burning requests, try the next state.
            if consec_captcha >= 20:
                _log(
                    f"  [{st}] {consec_captcha} consecutive captcha/WAF blocks — "
                    f"state appears bot-walled, moving on to the next state"
                )
                break
            patch: Dict[str, Any] = {}
            for c in patch_cols:
                v = rec.get(c)
                orig = row[c] if c in row.keys() else None
                if v is not None and v != orig:
                    patch[c] = v
            if patch and rec.get("id") is not None:
                try:
                    self.db.update_offender(int(rec["id"]), patch)
                except Exception as e:
                    stats["errors"] += 1
                    _log(f"  db error id={rec.get('id')}: {e}")
            if i % 100 == 0 or i == total:
                try:
                    self.db._conn.commit()
                except Exception:
                    pass
                _log(
                    f"  [{st}] {i:,}/{total:,} · filled={stats['filled']} "
                    f"dead={stats['dead']} empty={stats['empty']} "
                    f"captcha={stats['captcha']} err={stats['errors']}"
                )
        try:
            self.db._conn.commit()
        except Exception:
            pass
        _log(f"[{st}] enrich done: {stats}")
        return stats


