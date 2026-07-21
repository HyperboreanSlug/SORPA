"""Shell"""
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
from typing import Any, Dict, List, Optional, Set, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from gui_app.lazy_tabs import LazyTabHost
from gui_app.paths import ROOT
from gui_app.theme import (
    C,
    FONT_BOLD,
    FONT_MONO,
    FONT_SECTION,
    FONT_SM,
    FONT_TITLE,
    FONT_UI,
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


class DeepfaceShellMixin:
    def _build_deepface(self, tab):
        """Nested sub-tabs: Scan (primary) and Setup (status / weights / install)."""
        tab.configure(fg_color=C["surface"])
        self._df_status_busy = False
        self._df_setup_built = False
        self._df_scan_running = False
        self._df_scan_cancel = False
        self._df_scan_hits: list = []

        sub = ctk.CTkTabview(
            tab,
            fg_color=C["surface"],
            segmented_button_fg_color=C["elevated"],
            segmented_button_selected_color=C["accent_dim"],
            segmented_button_selected_hover_color=C["select"],
            segmented_button_unselected_color=C["elevated"],
            segmented_button_unselected_hover_color=C["panel"],
            text_color=C["text"],
            corner_radius=10,
            border_width=0,
        )
        sub.pack(fill="both", expand=True, padx=6, pady=6)
        self.deepface_tabs = sub

        host = LazyTabHost(sub, on_change=self._on_deepface_subtab_change)
        self._deepface_lazy = host
        host.register("Scan", lambda p: self._build_deepface_scan(p) or True)
        host.register("Review", lambda p: self._build_deepface_reports(p) or True)
        host.register("Setup", lambda p: self._build_deepface_setup(p) or True)

        try:
            sub.set("Scan")
        except Exception:
            pass
        host.ensure("Scan")
        return host


    def _on_deepface_subtab_change(self, name: Optional[str] = None) -> None:
        try:
            name = name or self.deepface_tabs.get()
        except Exception:
            name = "Scan"
        if name == "Setup" and hasattr(self, "_deepface_refresh_status"):
            if getattr(self, "_df_setup_built", False):
                try:
                    self.after(30, self._deepface_refresh_status)
                except Exception:
                    pass
        if name == "Review" and hasattr(self, "_dfr_refresh"):
            try:
                self.after(50, self._dfr_refresh)
            except Exception:
                pass


    def _deepface_goto_setup(self) -> None:
        try:
            self.deepface_tabs.set("Setup")
            if hasattr(self, "_deepface_lazy"):
                self._deepface_lazy.ensure("Setup")
        except Exception:
            pass


