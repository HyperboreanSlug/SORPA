"""Duplicate removal (preview + delete) off the UI thread."""
from __future__ import annotations

from tkinter import messagebox


class ScrapeDedupeRemoveMixin:
    def _remove_duplicates(self) -> None:
        """Preview then remove duplicates — both steps off the UI thread."""
        if getattr(self, "_dup_remove_running", False):
            messagebox.showinfo("Remove duplicates", "Already running…")
            return
        self._dup_remove_running = True
        if hasattr(self, "integrity_status"):
            try:
                self.integrity_status.configure(text="Scanning duplicates…")
            except Exception:
                pass
        db_path = str(getattr(self, "db_path", None) or "data/offenders.db")

        def preview_work():
            from scraper.database import DEFAULT_DEDUPE_STRATEGIES, Database

            strats = list(DEFAULT_DEDUPE_STRATEGIES)
            db = Database(db_path)
            try:
                return db.remove_duplicates_all(
                    strats, dry_run=True, merge_fields=True, safe_only=True
                )
            finally:
                db.close()

        def after_preview(result=None, error=None):
            if error is not None:
                self._dup_remove_running = False
                messagebox.showerror("Duplicate scan failed", str(error))
                return
            preview = result or {}
            would = int(preview.get("total_deleted") or 0)
            skipped_u = int(preview.get("total_skipped_unsafe") or 0)
            merged_preview = int(preview.get("total_merged_fields") or 0)
            if would <= 0:
                self._dup_remove_running = False
                messagebox.showinfo(
                    "Remove duplicates",
                    "No safe duplicates found for URL / external id / name+DOB.\n"
                    f"(Skipped {skipped_u} portal/CAPTCHA URL clusters.)",
                )
                return
            detail_lines = []
            for r in preview.get("strategies") or []:
                if r.get("deleted"):
                    detail_lines.append(
                        f"  · {r['strategy']}: {r['deleted']:,} rows in "
                        f"{r['groups']:,} groups"
                        + (
                            f" · ~{r.get('merged_fields', 0)} field merges"
                            if r.get("merged_fields")
                            else ""
                        )
                    )
            detail = "\n".join(detail_lines) if detail_lines else ""
            ok = messagebox.askyesno(
                "Remove duplicates?",
                (
                    f"About to permanently delete {would:,} safe duplicate row(s).\n\n"
                    f"{detail}\n\n"
                    f"Portal/CAPTCHA clusters skipped: {skipped_u}\n"
                    f"Field merges (preview): {merged_preview:,}\n\n"
                    "Keeps richest record per group; merges details before delete.\n\n"
                    "Continue?"
                ),
            )
            if not ok:
                self._dup_remove_running = False
                return
            if hasattr(self, "integrity_status"):
                try:
                    self.integrity_status.configure(text="Removing duplicates…")
                except Exception:
                    pass

            def remove_work():
                from scraper.database import DEFAULT_DEDUPE_STRATEGIES, Database

                strats = list(DEFAULT_DEDUPE_STRATEGIES)
                db = Database(db_path)
                try:
                    return db.remove_duplicates_all(
                        strats, dry_run=False, merge_fields=True, safe_only=True
                    )
                finally:
                    db.close()

            def after_remove(result=None, error=None):
                self._dup_remove_running = False
                if error is not None:
                    messagebox.showerror("Remove duplicates failed", str(error))
                    return
                result = result or {}
                deleted = int(result.get("total_deleted") or 0)
                left = int(result.get("total_offenders") or 0)
                skipped = int(result.get("total_skipped_unsafe") or 0)
                merged_n = int(result.get("total_merged_fields") or 0)
                msg = (
                    f"Deleted {deleted:,} duplicates · {left:,} remain"
                    + (f" · merged {merged_n:,} fields" if merged_n else "")
                    + (f" · skipped {skipped} unsafe clusters" if skipped else "")
                )
                self.log_queue.put(f"Dedupe: {msg}")
                if hasattr(self, "integrity_status"):
                    try:
                        self.integrity_status.configure(text=msg)
                    except Exception:
                        pass
                messagebox.showinfo("Duplicates removed", msg)
                if hasattr(self, "_after_db_data_changed"):
                    self._after_db_data_changed()

            if hasattr(self, "run_bg"):
                self.run_bg(remove_work, after_remove, name="dup-remove")
            else:
                try:
                    after_remove(result=remove_work(), error=None)
                except Exception as e:
                    after_remove(result=None, error=e)

        if hasattr(self, "run_bg"):
            self.run_bg(preview_work, after_preview, name="dup-preview")
        else:
            try:
                after_preview(result=preview_work(), error=None)
            except Exception as e:
                after_preview(result=None, error=e)
