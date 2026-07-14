"""ArchiverApp shell: header, tab host, activity log, lifecycle."""
from __future__ import annotations

import queue
import threading
from typing import Any, Dict, Optional

import customtkinter as ctk

from gui_app.async_jobs import AsyncJobsMixin
from gui_app.lazy_tabs import LazyTabHost
from gui_app.paths import ROOT
from gui_app.shell_header import ShellHeaderMixin
from gui_app.shell_ops import ShellOpsMixin
from gui_app.shell_sync import ShellSyncMixin
from gui_app.shared.detail_drawer import DetailDrawerMixin
from gui_app.tabs.browse import BrowseTabMixin
from gui_app.tabs.browse.deepface_reports import DeepfaceReportsTabMixin
from gui_app.tabs.browse.integrity import IntegrityTabMixin
from gui_app.tabs.browse.misclassify import MisclassifyTabMixin
from gui_app.tabs.browse.reports import ReportsTabMixin
from gui_app.tabs.browse.search import SearchTabMixin
from gui_app.tabs.browse.statistics import StatisticsTabMixin
from gui_app.tabs.deepface import DeepfaceTabMixin
from gui_app.tabs.nsopw import NsopwTabMixin
from gui_app.tabs.scrape import ScrapeTabMixin
from gui_app.tabs.settings import SettingsTabMixin
from gui_app.theme import (
    C,
    FONT_BOLD,
    FONT_MONO,
    FONT_SM,
    FONT_TITLE,
    _style_treeview,
)
from gui_app.widgets import _card, _vpaned


class ArchiverApp(
    AsyncJobsMixin,
    ShellSyncMixin,
    ShellHeaderMixin,
    ShellOpsMixin,
    DetailDrawerMixin,
    BrowseTabMixin,
    SearchTabMixin,
    IntegrityTabMixin,
    MisclassifyTabMixin,
    StatisticsTabMixin,
    ReportsTabMixin,
    DeepfaceReportsTabMixin,
    NsopwTabMixin,
    ScrapeTabMixin,
    DeepfaceTabMixin,
    SettingsTabMixin,
    ctk.CTk,
):
    """Top-level window: lazy tabs + shared DB/settings lifecycle."""

    def __init__(self) -> None:
        super().__init__()
        self.title("SOR Public Archiver")
        self.geometry("1320x860")
        self.minsize(940, 650)
        self.configure(fg_color=C["bg"])

        _style_treeview(self)

        self.sources: list = []
        self.selected_states: set = set()
        self.log_queue: queue.Queue = queue.Queue()
        self.is_running = False
        self._nsopw_cancel = False
        self._misclass_results: list = []
        self._report_verdicts: Dict[str, str] = {}
        self._report_items: list = []
        self._report_image_refs: list = []
        self._closing = False
        self._nsopw_runtime_lock = threading.Lock()
        self._nsopw_runtime: Dict[str, Any] = {}

        from scraper.app_settings import load_settings

        self.app_settings = load_settings()
        self.db_path = str(self.app_settings.get("db_path") or "data/offenders.db")
        self._report_verdicts_path = ROOT / "data" / "report_verdicts.json"
        self._load_report_verdicts()

        self._init_async_jobs()
        self._build()
        self._load_sources()
        self._poll_log()
        self._bind_global_copy_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            from gui_app.resize_perf import bind_root_resize_throttle

            bind_root_resize_throttle(self, settle_ms=80)
        except Exception:
            pass
        self.after(400, self._maybe_prompt_or_sync_database)

    def _build(self) -> None:
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
            db_row, text="", font=FONT_SM, text_color=C["muted"], anchor="w"
        )
        self.header_db_label.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            db_row,
            text="Open data",
            width=88,
            height=28,
            command=self._open_data_folder_header,
            fg_color=C["elevated"],
            hover_color=C["border"],
            text_color=C["text"],
            border_width=1,
            border_color=C["border"],
        ).pack(side="left")

        self.stats_label = ctk.CTkLabel(
            header, text="Ready", font=FONT_SM, text_color=C["accent"]
        )
        self.stats_label.pack(side="right", padx=14)
        self._header_record_count: Optional[int] = None
        self._header_refresh_after_id = None
        self.after(50, self._refresh_header_db_path)
        self.after(2000, self._poll_header_record_count)

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
            command=None,
        )
        self.tabs.pack(fill="both", expand=True)

        self._main_lazy = LazyTabHost(self.tabs, on_change=self._on_main_tab_change)
        self._main_lazy.register("Browse", lambda p: self._build_browse(p) or True)
        self._main_lazy.register("NSOPW", lambda p: self._build_nsopw(p) or True)
        self._main_lazy.register("Scrape", lambda p: self._build_scrape(p) or True)
        self._main_lazy.register("DeepFace", lambda p: self._build_deepface(p) or True)
        self._main_lazy.register("Settings", lambda p: self._build_settings(p) or True)

        try:
            self.tabs.set("Browse")
        except Exception:
            pass
        self._main_lazy.ensure("Browse")

        log_card = _card(log_host)
        log_card.pack(fill="both", expand=True, padx=0, pady=(4, 0))
        ctk.CTkLabel(
            log_card,
            text="Activity  ·  shown on NSOPW & Scrape · drag sash to resize",
            font=FONT_BOLD,
            text_color=C["muted"],
            anchor="w",
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
