"""Log drain, sources load, sash helper, close lifecycle."""
from __future__ import annotations

import queue
from datetime import datetime
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import messagebox


class ShellOpsMixin:
    """Shared shell operations used by multiple tabs."""

    def _on_main_tab_change(self, _name: str | None = None) -> None:
        """Show Activity log on NSOPW and Settings → Scrape."""
        try:
            name = _name or self.tabs.get()
        except Exception:
            name = "Browse"
        scrape_active = False
        if name == "Settings" and hasattr(self, "_settings_scrape_active"):
            try:
                scrape_active = bool(self._settings_scrape_active())
            except Exception:
                scrape_active = False
        want = name == "NSOPW" or scrape_active
        if (
            name == "Settings"
            and not scrape_active
            and hasattr(self, "_settings_refresh_status")
        ):
            try:
                self._settings_refresh_status()
            except Exception:
                pass
        if name == "DeepFace":
            try:
                if getattr(self, "_df_setup_built", False) and hasattr(
                    self, "_deepface_refresh_status"
                ):
                    self.after(30, self._deepface_refresh_status)
            except Exception:
                pass
        if want and not self._log_visible:
            try:
                self._main_split.add(self._log_host, minsize=100, stretch="never")
                self._log_visible = True
                self.after(60, lambda: self._set_sash(self._main_split, 0, 0.78))
            except Exception:
                pass
        elif not want and self._log_visible:
            try:
                self._main_split.forget(self._log_host)
            except Exception:
                try:
                    self._main_split.remove(self._log_host)
                except Exception:
                    pass
            self._log_visible = False

    @staticmethod
    def _set_sash(paned: tk.PanedWindow, index: int, fraction: float) -> None:
        """Place a sash at a fraction of the paned widget size."""
        try:
            paned.update_idletasks()
            orient = str(paned.cget("orient"))
            if orient == tk.VERTICAL or orient == "vertical":
                total = paned.winfo_height()
            else:
                total = paned.winfo_width()
            if total > 40:
                paned.sash_place(
                    index,
                    0 if orient in (tk.VERTICAL, "vertical") else int(total * fraction),
                    int(total * fraction)
                    if orient in (tk.VERTICAL, "vertical")
                    else 0,
                )
        except Exception:
            pass

    def _poll_log(self) -> None:
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        self.is_running = running
        state = "disabled" if running else "normal"
        if hasattr(self, "scrape_btn"):
            try:
                self.scrape_btn.configure(state=state)
            except Exception:
                pass

    def _load_sources(self) -> None:
        from scraper.config import REGISTRIES

        try:
            self.sources = REGISTRIES
            self._populate_scrape_tree()
            self.log_queue.put("Loaded registry configs (50 states + DC).")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _populate_scrape_tree(self) -> None:
        """Fill scrape jurisdiction tree when the Scrape tab has been built."""
        if not hasattr(self, "scrape_tree") or not getattr(self, "sources", None):
            return
        self.scrape_tree.delete(*self.scrape_tree.get_children())
        for reg in self.sources:
            if reg.abbr == "US":
                continue
            tags = ("direct",) if reg.direct_downloads else ()
            self.scrape_tree.insert(
                "",
                "end",
                text=reg.name,
                values=(
                    reg.abbr,
                    reg.scrape_method.upper(),
                    (reg.notes or "")[:70],
                ),
                tags=tags,
            )

    def _open_output_folder(self) -> None:
        if not hasattr(self, "scrape_output_var"):
            path = Path("data/downloads")
        else:
            path = Path(self.scrape_output_var.get())
        path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)

    def _on_close(self) -> None:
        """Window close: cancel jobs, optional backup, then hard process exit."""
        if getattr(self, "_closing", False) and getattr(self, "_shutdown_armed", False):
            return

        if getattr(self, "is_running", False):
            try:
                if not messagebox.askyesno(
                    "Job still running",
                    "A scrape or NSOPW job is still running.\n\n"
                    "Close anyway? In-flight work may be incomplete.\n"
                    "(Prefer Cancel on the job first.)",
                ):
                    return
            except Exception:
                pass

        # Stop workers / after-loops immediately (before slow backup)
        try:
            from gui_app.process_lifecycle import mark_closing

            mark_closing(self)
        except Exception:
            self._closing = True

        if hasattr(self, "settings_backup_on_close"):
            try:
                from scraper.app_settings import save_settings, normalize_settings

                raw = self._settings_collect()
                save_settings(raw)
                self.app_settings = normalize_settings(raw)
                self.db_path = str(self.app_settings.get("db_path") or self.db_path)
            except Exception:
                pass

        do_backup = bool(self.app_settings.get("backup_on_close", False))
        if do_backup:
            try:
                dest, note = self._run_db_backup()
                try:
                    extra = f" ({note})" if note else ""
                    self.stats_label.configure(
                        text=f"Backed up → {Path(dest).name}{extra}"
                    )
                    self.update_idletasks()
                except Exception:
                    pass
            except FileNotFoundError:
                pass
            except Exception as e:
                try:
                    if not messagebox.askokcancel(
                        "Backup failed",
                        f"Could not backup database:\n{e}\n\nClose anyway?",
                    ):
                        self._closing = False
                        try:
                            self._shutdown_armed = False
                        except Exception:
                            pass
                        return
                except Exception:
                    pass

        try:
            from gui_app.process_lifecycle import shutdown_app

            shutdown_app(self)
        except Exception:
            try:
                self.quit()
            except Exception:
                pass
            try:
                self.destroy()
            except Exception:
                pass
