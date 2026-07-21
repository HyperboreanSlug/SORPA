"""NSOPW Enrich sub-tab — run state-scoped report enrichment."""
from __future__ import annotations

import threading

from tkinter import messagebox


class NsopwEnrichRunMixin:
    def _cancel_nsopw_state_enrich(self) -> None:
        self._nsopw_enrich_cancel = True
        try:
            self.nsopw_enrich_status.configure(text="Cancelling…")
        except Exception:
            pass

    def _start_nsopw_state_enrich(self) -> None:
        if getattr(self, "is_running", False):
            messagebox.showwarning("Busy", "Wait for the current job to finish.")
            return
        try:
            lim_raw = (self.nsopw_enrich_limit.get() or "100").strip()
            limit = int(lim_raw) if lim_raw else 0
            if limit < 0:
                limit = 0
            delay = max(0.25, float(self.nsopw_enrich_delay.get()))
            thr_raw = getattr(self, "nsopw_enrich_threads", None)
            threads = int(thr_raw.get()) if thr_raw is not None else 4
            threads = max(1, min(threads, 16))
        except (TypeError, ValueError) as e:
            messagebox.showerror("Enrich", f"Invalid limit/delay/threads: {e}")
            return

        need_race = bool(self.nsopw_enrich_need_race.get())
        need_crime = bool(self.nsopw_enrich_need_crime.get())
        need_photo = bool(self.nsopw_enrich_need_photo.get())
        need_html = bool(self.nsopw_enrich_need_html.get())
        if not any((need_race, need_crime, need_photo, need_html)):
            messagebox.showwarning(
                "Nothing selected", "Enable at least one missing field."
            )
            return

        state = (
            self._nsopw_selected_state_code()
            if hasattr(self, "_nsopw_selected_state_code")
            else None
        )
        scope = (self.nsopw_enrich_scope_var.get() or "all").strip().lower()
        ethnicity = (
            self.nsopw_enrich_ethnicity_var.get().strip().lower()
            if hasattr(self, "nsopw_enrich_ethnicity_var")
            else "all"
        )
        if ethnicity == "all":
            ethnicity = None
        # 0 = process all incomplete rows for this state (no batch cap).
        run_limit = limit if limit > 0 else 0
        limit_label = "all pending" if run_limit == 0 else str(run_limit)

        self._nsopw_enrich_cancel = False
        self._nsopw_enrich_busy = True
        if hasattr(self, "_set_running"):
            self._set_running(True)
        self.nsopw_enrich_start_btn.configure(state="disabled")
        self.nsopw_enrich_cancel_btn.configure(state="normal")
        self.nsopw_enrich_progress.set(0)
        self.nsopw_enrich_status.configure(
            text=(
                f"Enriching {state or 'all states'} "
                f"(limit {limit_label}, {threads} threads)…"
            )
        )
        db_path = str(
            getattr(self, "nsopw_db_path", None)
            or getattr(self, "db_path", None)
            or "data/offenders.db"
        )
        html_dir = str(getattr(self, "nsopw_html_dir", None) or "data/report_pages")

        def log(msg: str) -> None:
            if hasattr(self, "log_queue"):
                self.log_queue.put(msg)

        def on_progress(done: int, total: int, **kw) -> None:
            # Accept optional updated/with_* from callers; ignore extras safely.
            updated = int(kw.get("updated") or 0)
            frac = (done / total) if total else 0.0

            def ui():
                self.nsopw_enrich_progress.set(min(1.0, max(0.0, frac)))
                extra = f" · saved {updated}" if updated else ""
                self.nsopw_enrich_status.configure(
                    text=f"Enrich {state or 'all'} {done}/{total}{extra}…"
                )

            self.after(0, ui)

        def worker():
            from scraper.nsopw_builder import NSOPWEthnicDatabaseBuilder

            builder = NSOPWEthnicDatabaseBuilder(
                db_path=db_path,
                delay=2.0,
                report_delay=delay,
                html_dir=html_dir,
                cancel_check=lambda: getattr(self, "_nsopw_enrich_cancel", False),
                report_threads=threads,
            )
            try:
                summary = builder.requeue_incomplete(
                    need_race=need_race,
                    need_crime=need_crime,
                    need_photo=need_photo,
                    need_html=need_html,
                    limit=run_limit,
                    state=state,
                    source_scope=scope,
                    ethnicity_filter=ethnicity,
                    save_html=True,
                    log=log,
                    on_progress=on_progress,
                )

                def done():
                    self._nsopw_enrich_busy = False
                    if hasattr(self, "_set_running"):
                        self._set_running(False)
                    self.nsopw_enrich_start_btn.configure(state="normal")
                    self.nsopw_enrich_cancel_btn.configure(state="disabled")
                    self.nsopw_enrich_progress.set(1.0)
                    self.nsopw_enrich_status.configure(
                        text=(
                            f"Done · queued {summary.get('queued', 0)} · "
                            f"updated {summary.get('updated', 0)} · "
                            f"race {summary.get('with_race', 0)} · "
                            f"crime {summary.get('with_crime', 0)} · "
                            f"photo {summary.get('with_photo', 0)} · "
                            f"errors {summary.get('errors', 0)} · "
                            f"threads {summary.get('threads', threads)}"
                        )
                    )
                    # Refresh state enriched%/total + incomplete list after a run.
                    if hasattr(self, "_nsopw_refresh_state_dropdown"):
                        self._nsopw_refresh_state_dropdown()
                    elif hasattr(self, "_nsopw_enrich_reload_list"):
                        self._nsopw_enrich_reload_list()
                    if hasattr(self, "_refresh_header_db_path"):
                        self._refresh_header_db_path()
                    if hasattr(self, "_after_db_data_changed"):
                        try:
                            self._after_db_data_changed()
                        except Exception:
                            pass

                self.after(0, done)
            except Exception as e:
                err_s = str(e)
                log(f"State enrich ERROR: {err_s}")
                if "locked" in err_s.lower() or "busy" in err_s.lower():
                    log(
                        "  Hint: another process may be writing the DB "
                        "(Browse refresh, DeepFace, second enrich). "
                        "Wait and re-run — per-record updates now retry "
                        "automatically on lock."
                    )

                def fail():
                    self._nsopw_enrich_busy = False
                    if hasattr(self, "_set_running"):
                        self._set_running(False)
                    self.nsopw_enrich_start_btn.configure(state="normal")
                    self.nsopw_enrich_cancel_btn.configure(state="disabled")
                    # Keep progress bar where it was so partial work is visible
                    short = err_s[:120] + ("…" if len(err_s) > 120 else "")
                    self.nsopw_enrich_status.configure(
                        text=f"Error (partial run may be saved): {short}"
                    )
                    if hasattr(self, "_nsopw_refresh_state_dropdown"):
                        try:
                            self._nsopw_refresh_state_dropdown()
                        except Exception:
                            pass

                self.after(0, fail)
            finally:
                builder.close()

        threading.Thread(target=worker, daemon=True).start()
