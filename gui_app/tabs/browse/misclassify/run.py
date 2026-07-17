"""MisclassifyRunMixin — analysis off the UI thread + row select."""
from __future__ import annotations

from tkinter import filedialog, messagebox
from typing import Any, Callable, Dict, Optional


class MisclassifyRunMixin:
    def _misclass_on_select(self, _event=None):
        """Show photo + sidebar for the selected mismatch row.

        Paint immediately from the tree cache so the mugshot starts loading
        without waiting on a DB round-trip. Enrich missing photo_path/flags
        in the background and refresh only if the same row is still selected.
        """
        sel = self.misclass_tree.selection()
        if not sel:
            if getattr(self, "misclass_sidebar", None) is not None:
                self.misclass_sidebar.clear()
            return
        iid = sel[0]
        rec = self._misclass_records_by_iid.get(iid)
        if not rec:
            return

        # Immediate paint — photo decode starts now (async).
        self._misclass_show_sidebar(rec)
        self._misclass_prefetch_nearby_photos(iid)

        needs_enrich = bool(rec.get("id")) and (
            not rec.get("photo_path") or rec.get("flags") in (None, "")
        )
        if not needs_enrich:
            return

        db_path = str(getattr(self, "db_path", None) or "data/offenders.db")
        oid = int(rec["id"])
        tags = {
            k: rec[k]
            for k in (
                "_misclass_expected_race",
                "_misclass_likely",
                "_misclass_conf",
                "_misclass_name_conf",
                "_misclass_conf_combined",
                "_deepface",
            )
            if k in rec
        }
        had_photo = bool(str(rec.get("photo_path") or "").strip())

        def work():
            from scraper.database import Database

            db = Database(db_path)
            try:
                full = db.get_offender_by_id(oid)
                return dict(full) if full else None
            finally:
                db.close()

        def done(result=None, error=None):
            if error or not result:
                return
            # Stale if user already moved on.
            cur = self.misclass_tree.selection()
            if not cur or cur[0] != iid:
                if iid in getattr(self, "_misclass_records_by_iid", {}):
                    merged = dict(result)
                    merged.update(tags)
                    self._misclass_records_by_iid[iid] = merged
                return
            result.update(tags)
            self._misclass_records_by_iid[iid] = result
            # Re-show when we gained a photo path or flags (verdict UI).
            new_photo = bool(str(result.get("photo_path") or "").strip())
            if (not had_photo and new_photo) or result.get("flags") not in (
                None,
                "",
            ):
                self._misclass_show_sidebar(result)

        if hasattr(self, "run_bg"):
            self.run_bg(work, done, name="misclass-row")
        else:
            try:
                done(result=work(), error=None)
            except Exception as e:
                done(result=None, error=e)

    def _misclass_prefetch_nearby_photos(self, iid: str) -> None:
        """Warm the sidebar photo cache for nearby tree rows."""
        tree = getattr(self, "misclass_tree", None)
        by_iid = getattr(self, "_misclass_records_by_iid", None) or {}
        if tree is None:
            return
        try:
            kids = list(tree.get_children(""))
            pos = kids.index(iid)
        except Exception:
            return
        paths = []
        for j in (pos + 1, pos + 2, pos + 3, pos - 1):
            if 0 <= j < len(kids):
                r = by_iid.get(kids[j]) or {}
                p = r.get("photo_path")
                if p:
                    paths.append(p)
        if not paths:
            return
        try:
            from gui_app.shared.record_sidebar_photo import prefetch_photo_paths

            size = (340, 340)
            sb = getattr(self, "misclass_sidebar", None)
            if sb is not None and getattr(sb, "photo_size", None):
                size = tuple(sb.photo_size)  # type: ignore[assignment]
            prefetch_photo_paths(paths, box=size, limit=4)
        except Exception:
            pass

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
