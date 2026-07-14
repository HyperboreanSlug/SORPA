"""Header path/count status for ArchiverApp."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional


class ShellHeaderMixin:
    """Top-bar DB path, record count, and open-data button."""

    def schedule_header_refresh(self, delay_ms: int = 0) -> None:
        """Thread-safe: refresh header DB path + record count on the UI thread."""
        try:
            if delay_ms and delay_ms > 0:
                self.after(int(delay_ms), self._refresh_header_db_path)
            else:
                self.after(0, self._refresh_header_db_path)
        except Exception:
            try:
                self._refresh_header_db_path()
            except Exception:
                pass

    def _poll_header_record_count(self) -> None:
        """Periodic refresh so the top counter tracks inserts/deletes."""
        if getattr(self, "_closing", False):
            return
        try:
            self._refresh_header_db_path()
        except Exception:
            pass
        interval = 2500 if getattr(self, "is_running", False) else 8000
        try:
            self.after(interval, self._poll_header_record_count)
        except Exception:
            pass

    def _refresh_header_db_path(self) -> None:
        """Show active SQLite path; count runs off the UI thread."""
        try:
            p = Path(self.db_path)
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            else:
                p = p.resolve()
            try:
                show = str(p.relative_to(Path.cwd()))
            except ValueError:
                show = str(p)
            if len(show) > 48:
                show = "…" + show[-46:]
        except Exception:
            show = str(getattr(self, "db_path", "data/offenders.db"))

        # Immediate path paint (use cached count while worker runs)
        cached = getattr(self, "_header_record_count", None)
        n = f"  ·  {cached:,} records" if cached is not None else ""
        if hasattr(self, "header_db_label"):
            try:
                self.header_db_label.configure(text=f"DB: {show}{n}")
            except Exception:
                pass

        if getattr(self, "_header_count_busy", False):
            return
        self._header_count_busy = True
        db_path = str(getattr(self, "db_path", None) or "data/offenders.db")
        path_show = show

        def worker() -> None:
            count: Optional[int] = None
            try:
                from scraper.database import Database

                db = Database(db_path)
                try:
                    count = int(db.get_total_count() or 0)
                finally:
                    db.close()
            except Exception:
                count = None

            def apply() -> None:
                self._header_count_busy = False
                if count is not None:
                    self._header_record_count = count
                n2 = ""
                c = self._header_record_count
                if c is not None:
                    n2 = f"  ·  {c:,} records"
                if hasattr(self, "header_db_label"):
                    try:
                        self.header_db_label.configure(text=f"DB: {path_show}{n2}")
                    except Exception:
                        pass
                if hasattr(self, "stats_label") and count is not None:
                    try:
                        cur = (self.stats_label.cget("text") or "").strip()
                        idle_like = (
                            not cur
                            or cur == "Ready"
                            or cur.endswith(" records")
                            or cur.endswith("record")
                            or "selected" in cur.lower()
                        )
                        if idle_like and not getattr(self, "is_running", False):
                            self.stats_label.configure(text=f"{count:,} records")
                    except Exception:
                        pass

            try:
                self.after(0, apply)
            except Exception:
                self._header_count_busy = False

        threading.Thread(
            target=worker, name="header-count", daemon=True
        ).start()

    def _open_data_folder_header(self) -> None:
        path = Path("data")
        path.mkdir(parents=True, exist_ok=True)
        try:
            dbp = Path(self.db_path)
            if dbp.parent.is_dir():
                path = dbp.parent
        except Exception:
            pass
        self._open_path(path)
