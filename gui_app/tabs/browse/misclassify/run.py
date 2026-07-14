"""MisclassifyRunMixin — analysis off the UI thread."""
from __future__ import annotations

from tkinter import filedialog, messagebox
from typing import Callable, Optional


class MisclassifyRunMixin:
    def _misclass_on_select(self, _event=None):
        """Show photo + detail for the selected mismatch row."""
        sel = self.misclass_tree.selection()
        if not sel:
            return
        rec = self._misclass_records_by_iid.get(sel[0])
        if not rec:
            return
        iid = sel[0]
        if rec.get("id") and not rec.get("photo_path"):
            db_path = str(getattr(self, "db_path", None) or "data/offenders.db")
            oid = int(rec["id"])
            tags = {
                k: rec[k]
                for k in (
                    "_misclass_expected_race",
                    "_misclass_likely",
                    "_misclass_conf",
                )
                if k in rec
            }

            def work():
                from scraper.database import Database

                db = Database(db_path)
                try:
                    full = db.get_offender_by_id(oid)
                    return dict(full) if full else None
                finally:
                    db.close()

            def done(result=None, error=None):
                row = rec
                if result and not error:
                    result.update(tags)
                    self._misclass_records_by_iid[iid] = result
                    row = result
                if getattr(self, "misclass_detail", None) is not None:
                    self._fill_detail_drawer(self.misclass_detail, row)

            if hasattr(self, "run_bg"):
                self.run_bg(work, done, name="misclass-row")
                return
        if getattr(self, "misclass_detail", None) is not None:
            self._fill_detail_drawer(self.misclass_detail, rec)

    def _run_misclassification(self, on_done: Optional[Callable[[], None]] = None):
        """Analyze ethnicities off the UI thread; optional callback when painted."""
        if getattr(self, "_misclass_running", False):
            try:
                if hasattr(self, "misclass_status"):
                    self.misclass_status.configure(text="Analyze already running…")
            except Exception:
                pass
            return
        self._ensure_misclass_filter_vars()
        eth = (self.misclass_ethnicity_var.get() or "all").strip()
        try:
            min_conf = float(self.misclass_conf_var.get())
            limit = int(self.misclass_limit_var.get())
        except Exception as e:
            messagebox.showerror("Misclassify", f"Invalid options: {e}")
            return
        db_path = str(getattr(self, "db_path", None) or "data/offenders.db")
        eth_filter = None if eth == "all" else eth
        self._misclass_running = True
        if hasattr(self, "misclass_status"):
            try:
                self.misclass_status.configure(text="Analyzing… (UI stays responsive)")
            except Exception:
                pass

        def work():
            from scraper.searcher import SexOffenderSearcher

            searcher = SexOffenderSearcher(db_path=db_path)
            try:
                db_total = searcher.get_total_count()
                results, eth_base = searcher.analyze_ethnicities(
                    min_confidence=min_conf,
                    limit=limit,
                    ethnicity_filter=eth_filter,
                    return_base_count=True,
                )
                return {
                    "results": results,
                    "eth_base": eth_base,
                    "db_total": db_total,
                    "limit": limit,
                    "min_conf": min_conf,
                    "eth": eth,
                }
            finally:
                searcher.close()

        def done(result=None, error=None):
            self._misclass_running = False
            if error is not None:
                try:
                    if hasattr(self, "misclass_status"):
                        self.misclass_status.configure(text=f"Analyze error: {error}")
                except Exception:
                    pass
                messagebox.showerror("Misclassify", str(error))
                return
            self._apply_misclass_results(result or {})
            if on_done:
                try:
                    on_done()
                except Exception as e:
                    messagebox.showerror("Reports", str(e))

        if hasattr(self, "run_bg"):
            self.run_bg(work, done, name="misclass-analyze")
        else:
            try:
                done(result=work(), error=None)
            except Exception as e:
                done(result=None, error=e)

    def _export_misclass(self):
        self._ensure_misclass_filter_vars()
        path = filedialog.asksaveasfilename(defaultextension=".csv")
        if not path:
            return
        eth = (self.misclass_ethnicity_var.get() or "all").strip()
        try:
            min_conf = float(self.misclass_conf_var.get())
        except Exception as e:
            messagebox.showerror("Export", str(e))
            return
        db_path = str(getattr(self, "db_path", None) or "data/offenders.db")

        def work():
            from scraper.searcher import SexOffenderSearcher

            searcher = SexOffenderSearcher(db_path=db_path)
            try:
                return searcher.export_misclassifications(
                    path,
                    min_confidence=min_conf,
                    ethnicity_filter=None if eth == "all" else eth,
                )
            finally:
                searcher.close()

        def done(result=None, error=None):
            if error is not None:
                messagebox.showerror("Export failed", str(error))
                return
            messagebox.showinfo("Exported", f"{result} rows → {path}")

        if hasattr(self, "run_bg"):
            if hasattr(self, "misclass_status"):
                try:
                    self.misclass_status.configure(text="Exporting…")
                except Exception:
                    pass
            self.run_bg(work, done, name="misclass-export")
        else:
            try:
                done(result=work(), error=None)
            except Exception as e:
                done(result=None, error=e)
