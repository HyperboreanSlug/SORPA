"""ScrapeImportMixin — CSV import off the UI thread."""
from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox


class ScrapeImportMixin:
    def _import_downloads_folder(self):
        folder = self.scrape_output_var.get() or "data/downloads"
        if not Path(folder).is_dir():
            messagebox.showwarning("Missing folder", f"Not a directory: {folder}")
            return
        if getattr(self, "_csv_import_running", False):
            messagebox.showinfo("Import", "Import already running…")
            return
        skip = bool(self.scrape_import_skip.get())
        db_path = str(getattr(self, "db_path", None) or "data/offenders.db")
        self._csv_import_running = True
        try:
            self.scrape_import_status.configure(text="Importing folder…")
        except Exception:
            pass

        def work():
            from scraper.database import Database

            db = Database(db_path)
            try:
                return db.import_csv_directory(folder, skip_existing_urls=skip)
            finally:
                db.close()

        def done(result=None, error=None):
            self._csv_import_running = False
            if error is not None:
                messagebox.showerror("Import failed", str(error))
                try:
                    self.scrape_import_status.configure(text=f"Import failed: {error}")
                except Exception:
                    pass
                return
            summary = result or {}
            msg = (
                f"Files: {summary.get('files', 0)} · "
                f"imported {summary.get('imported', 0)} · "
                f"skipped {summary.get('skipped', 0)} · "
                f"rows {summary.get('total_rows', 0)}"
            )
            if summary.get("errors"):
                msg += f" · errors: {len(summary['errors'])}"
            try:
                self.scrape_import_status.configure(text=msg)
            except Exception:
                pass
            self.log_queue.put(f"CSV import folder: {msg}")
            for err in summary.get("errors") or []:
                self.log_queue.put(f"  import error: {err}")
            self._after_db_data_changed()

        if hasattr(self, "run_bg"):
            self.run_bg(work, done, name="csv-import-folder")
        else:
            try:
                done(result=work(), error=None)
            except Exception as e:
                done(result=None, error=e)

    def _import_csv_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("CSV", "*.csv"), ("All", "*.*")],
            initialdir=self.scrape_output_var.get() or "data/downloads",
        )
        if not path:
            return
        if getattr(self, "_csv_import_running", False):
            messagebox.showinfo("Import", "Import already running…")
            return
        skip = bool(self.scrape_import_skip.get())
        db_path = str(getattr(self, "db_path", None) or "data/offenders.db")
        self._csv_import_running = True
        try:
            self.scrape_import_status.configure(text=f"Importing {Path(path).name}…")
        except Exception:
            pass

        def work():
            from scraper.database import Database

            db = Database(db_path)
            try:
                return db.import_csv(path, skip_existing_urls=skip)
            finally:
                db.close()

        def done(result=None, error=None):
            self._csv_import_running = False
            if error is not None:
                messagebox.showerror("Import failed", str(error))
                return
            result = result or {}
            msg = (
                f"{Path(path).name}: imported {result.get('imported', 0)} · "
                f"skipped {result.get('skipped', 0)} · "
                f"rows {result.get('total_rows', 0)}"
            )
            try:
                self.scrape_import_status.configure(text=msg)
            except Exception:
                pass
            self.log_queue.put(f"CSV import: {msg}")
            self._after_db_data_changed()

        if hasattr(self, "run_bg"):
            self.run_bg(work, done, name="csv-import-file")
        else:
            try:
                done(result=work(), error=None)
            except Exception as e:
                done(result=None, error=e)

    def _after_db_data_changed(self) -> None:
        """Refresh header; schedule Integrity only if that tab is built."""
        try:
            if hasattr(self, "schedule_header_refresh"):
                self.schedule_header_refresh(0)
            elif hasattr(self, "_refresh_header_db_path"):
                self._refresh_header_db_path()
        except Exception:
            pass
        if hasattr(self, "integrity_summary") and hasattr(self, "_refresh_integrity"):
            try:
                self.after(50, self._refresh_integrity)
            except Exception:
                pass
        # Publisher: auto-upload when pending listing changes ≥ threshold
        try:
            if hasattr(self, "_maybe_auto_publish_public_db"):
                self.after(800, self._maybe_auto_publish_public_db)
        except Exception:
            pass
        note = "DB updated · open Misclassify → Analyze to include new rows"
        if hasattr(self, "misclass_status"):
            try:
                self.misclass_status.configure(text=note)
            except Exception:
                pass
        if hasattr(self, "mcstat_status"):
            try:
                self.mcstat_status.configure(text=note)
            except Exception:
                pass
        try:
            self.log_queue.put(note)
        except Exception:
            pass
