"""ArchiverApp shell: header, tab host, activity log, lifecycle."""
from __future__ import annotations

import csv
import json
import os
import queue
import re
import subprocess
import sys
import threading
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from gui_app.theme import (
    C,
    FONT_BOLD,
    FONT_MONO,
    FONT_SECTION,
    FONT_SM,
    FONT_TITLE,
    FONT_UI,
    _style_treeview,
)
from gui_app.widgets import (
    _bind_tree_scroll_isolation,
    _card,
    _enable_tree_column_sort,
    _format_race_display,
    _format_state_display,
    _hpaned,
    _misclass_race_bucket,
    _muted,
    _render_bar_chart,
    _render_pie_chart,
    _section_label,
    _stretch_columns,
    _tree_frame,
    _vpaned,
    _wire_wide_scroll,
)
from gui_app.paths import ROOT


from gui_app.lazy_tabs import LazyTabHost
from gui_app.shared.detail_drawer import DetailDrawerMixin
from gui_app.tabs.browse import BrowseTabMixin
from gui_app.tabs.browse.integrity import IntegrityTabMixin
from gui_app.tabs.browse.misclassify import MisclassifyTabMixin
from gui_app.tabs.browse.reports import ReportsTabMixin
from gui_app.tabs.browse.search import SearchTabMixin
from gui_app.tabs.browse.statistics import StatisticsTabMixin
from gui_app.tabs.nsopw import NsopwTabMixin
from gui_app.tabs.scrape import ScrapeTabMixin
from gui_app.tabs.settings import SettingsTabMixin


class ArchiverApp(
    DetailDrawerMixin,
    BrowseTabMixin,
    SearchTabMixin,
    IntegrityTabMixin,
    MisclassifyTabMixin,
    StatisticsTabMixin,
    ReportsTabMixin,
    NsopwTabMixin,
    ScrapeTabMixin,
    SettingsTabMixin,
    ctk.CTk,
):
    def __init__(self):
        super().__init__()
        self.title("SOR Public Archiver")
        self.geometry("1280x820")
        self.minsize(980, 640)
        self.configure(fg_color=C["bg"])

        _style_treeview(self)

        # State
        self.sources: list = []
        self.selected_states: set = set()
        self.log_queue: queue.Queue = queue.Queue()
        self.is_running = False
        self._nsopw_cancel = False
        self._misclass_results: list = []
        self._report_verdicts: Dict[str, str] = {}  # key → confirmed|correct|skip
        self._report_items: list = []  # filtered Misclassification rows for Reports
        self._report_image_refs: list = []
        self._closing = False
        # NSOPW options snapshot (main thread writes; worker reads under lock)
        self._nsopw_runtime_lock = threading.Lock()
        self._nsopw_runtime: Dict[str, Any] = {}

        # Persistent settings (DB path, backups, NSOPW compact search)
        from scraper.app_settings import load_settings

        self.app_settings = load_settings()
        self.db_path = str(self.app_settings.get("db_path") or "data/offenders.db")
        self._report_verdicts_path = ROOT / "data" / "report_verdicts.json"
        self._load_report_verdicts()

        self._build()
        self._load_sources()
        self._poll_log()
        self._bind_global_copy_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self):
        # Compact header
        header = ctk.CTkFrame(self, fg_color=C["surface"], height=44, corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text="SOR Public Archiver",
            font=FONT_TITLE,
            text_color=C["text"],
        ).pack(side="left", padx=14, pady=8)

        db_row = ctk.CTkFrame(header, fg_color="transparent")
        db_row.pack(side="left", padx=(8, 0), fill="y")
        self.header_db_label = ctk.CTkLabel(
            db_row,
            text="",
            font=FONT_SM,
            text_color=C["muted"],
            anchor="w",
        )
        self.header_db_label.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            db_row, text="Open data", width=88, height=28,
            command=self._open_data_folder_header,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left")

        self.stats_label = ctk.CTkLabel(
            header,
            text="Ready",
            font=FONT_SM,
            text_color=C["accent"],
        )
        self.stats_label.pack(side="right", padx=14)
        self.after(50, self._refresh_header_db_path)

        body = ctk.CTkFrame(self, fg_color=C["bg"])
        body.pack(fill="both", expand=True, padx=8, pady=(4, 6))

        main_split = _vpaned(body)
        main_split.pack(fill="both", expand=True)

        tabs_host = ctk.CTkFrame(main_split, fg_color=C["bg"], corner_radius=0)
        log_host = ctk.CTkFrame(main_split, fg_color=C["bg"], corner_radius=0)
        main_split.add(tabs_host, minsize=280, stretch="always")
        self._main_split = main_split
        self._tabs_host = tabs_host
        self._log_host = log_host
        self._log_visible = False

        self.tabs = ctk.CTkTabview(
            tabs_host,
            fg_color=C["surface"],
            segmented_button_fg_color=C["elevated"],
            segmented_button_selected_color=C["accent_dim"],
            segmented_button_selected_hover_color=C["select"],
            segmented_button_unselected_color=C["elevated"],
            segmented_button_unselected_hover_color=C["panel"],
            text_color=C["text"],
            text_color_disabled=C["dim"],
            corner_radius=12,
            border_width=1,
            border_color=C["border"],
            command=None,  # LazyTabHost owns command
        )
        self.tabs.pack(fill="both", expand=True)

        self._main_lazy = LazyTabHost(self.tabs, on_change=self._on_main_tab_change)
        self._main_lazy.register("Browse", lambda p: self._build_browse(p) or True)
        self._main_lazy.register("NSOPW", lambda p: self._build_nsopw(p) or True)
        self._main_lazy.register("Scrape", lambda p: self._build_scrape(p) or True)
        self._main_lazy.register("Settings", lambda p: self._build_settings(p) or True)

        try:
            self.tabs.set("Browse")
        except Exception:
            pass
        self._main_lazy.ensure("Browse")

        # Log (shown only on NSOPW / Scrape via _on_main_tab_change)
        log_card = _card(log_host)
        log_card.pack(fill="both", expand=True, padx=0, pady=(4, 0))
        ctk.CTkLabel(
            log_card, text="Activity  ·  shown on NSOPW & Scrape · drag sash to resize",
            font=FONT_BOLD, text_color=C["muted"], anchor="w",
        ).pack(fill="x", padx=14, pady=(10, 4))
        self.log_text = ctk.CTkTextbox(
            log_card,
            height=100,
            font=FONT_MONO,
            fg_color=C["bg"],
            text_color=C["muted"],
            border_color=C["border"],
            border_width=1,
            corner_radius=8,
            activate_scrollbars=True,
        )
        self.log_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.log_text.configure(state="disabled")

        self.after(80, self._on_main_tab_change)

    def _on_main_tab_change(self, _name: Optional[str] = None):
        """Show Activity log only on NSOPW and Scrape tabs."""
        try:
            name = _name or self.tabs.get()
        except Exception:
            name = "Browse"
        want = name in ("NSOPW", "Scrape")
        if name == "Settings" and hasattr(self, "_settings_refresh_status"):
            try:
                self._settings_refresh_status()
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
                    int(total * fraction) if orient in (tk.VERTICAL, "vertical") else 0,
                )
        except Exception:
            pass

    def _refresh_header_db_path(self):
        """Show active SQLite path in the header."""
        try:
            p = Path(self.db_path)
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            else:
                p = p.resolve()
            # Prefer short relative path when under project
            try:
                show = str(p.relative_to(Path.cwd()))
            except ValueError:
                show = str(p)
            if len(show) > 52:
                show = "…" + show[-50:]
            n = ""
            try:
                from scraper.database import Database
                db = Database(self.db_path)
                try:
                    n = f"  ·  {db.get_total_count():,} records"
                finally:
                    db.close()
            except Exception:
                pass
            if hasattr(self, "header_db_label"):
                self.header_db_label.configure(text=f"DB: {show}{n}")
        except Exception:
            if hasattr(self, "header_db_label"):
                self.header_db_label.configure(text=f"DB: {self.db_path}")

    def _open_data_folder_header(self):
        path = Path("data")
        path.mkdir(parents=True, exist_ok=True)
        # Prefer folder containing the DB
        try:
            dbp = Path(self.db_path)
            if dbp.parent.is_dir():
                path = dbp.parent
        except Exception:
            pass
        self._open_path(path)

    def _poll_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    def _append_log(self, message: str):
        self.log_text.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_running(self, running: bool):
        self.is_running = running
        state = "disabled" if running else "normal"
        # Scrape/NSOPW buttons may not exist until those tabs are lazy-loaded
        if hasattr(self, "scrape_btn"):
            try:
                self.scrape_btn.configure(state=state)
            except Exception:
                pass

    def _load_sources(self):
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
                values=(reg.abbr, reg.scrape_method.upper(), (reg.notes or "")[:70]),
                tags=tags,
            )

    def _open_output_folder(self):
        if not hasattr(self, "scrape_output_var"):
            path = Path("data/downloads")
        else:
            path = Path(self.scrape_output_var.get())
        path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)

    def _on_close(self) -> None:
        """Window close: optional DB backup, then destroy."""
        if self._closing:
            return

        # Don't silently abandon a running scrape/NSOPW/requeue
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

        self._closing = True

        # Persist latest Settings UI values if the tab was built
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
                # No DB yet — fine
                pass
            except Exception as e:
                try:
                    if not messagebox.askokcancel(
                        "Backup failed",
                        f"Could not backup database:\n{e}\n\nClose anyway?",
                    ):
                        self._closing = False
                        return
                except Exception:
                    pass

        try:
            self.destroy()
        except Exception:
            pass


