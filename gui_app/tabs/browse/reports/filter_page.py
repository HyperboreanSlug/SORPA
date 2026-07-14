"""FPage"""
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


class ReportsFilterPageMixin:
    def _reports_on_layout_change(self, value: Optional[str] = None) -> None:
        """Toggle Grid vs List cards; keep BooleanVar in sync for Open HTML."""
        raw = (
            value
            if value is not None
            else (getattr(self, "report_layout_mode", None) and self.report_layout_mode.get())
            or "Grid"
        )
        is_grid = str(raw or "Grid").strip().lower().startswith("g")
        if not hasattr(self, "report_grid_view") or self.report_grid_view is None:
            self.report_grid_view = ctk.BooleanVar(value=is_grid)
        else:
            try:
                self.report_grid_view.set(is_grid)
            except Exception:
                self.report_grid_view = ctk.BooleanVar(value=is_grid)
        if hasattr(self, "report_layout_hint"):
            try:
                self.report_layout_hint.configure(
                    text=(
                        "Grid · Export toggle → 1×2 / 2×2"
                        if is_grid
                        else "List · Export toggle next to Correct"
                    )
                )
            except Exception:
                pass
        try:
            self._reports_rebuild_cards(refilter=False)
        except Exception:
            pass


    def _reports_is_grid(self) -> bool:
        """True when layout is Grid (segment or legacy checkbox)."""
        if hasattr(self, "report_layout_mode") and self.report_layout_mode is not None:
            try:
                return str(self.report_layout_mode.get() or "").strip().lower().startswith("g")
            except Exception:
                pass
        return bool(
            getattr(self, "report_grid_view", None) and self.report_grid_view.get()
        )


    def _reports_page_size(self) -> int:
        try:
            n = int(self.report_max_var.get())
        except (TypeError, ValueError):
            n = 40
        return max(1, min(n if n > 0 else 40, 500))


    def _reports_on_filter_change(self, show_value: Optional[str] = None) -> None:
        """Race/verdict/photos filter changed — rebuild pool from page 0."""
        if show_value is not None:
            try:
                self.report_verdict_filter.set(str(show_value))
            except Exception:
                pass
        self._report_page = 0
        self._reports_rebuild_cards(refilter=True)


    def _reports_apply_page(self) -> list:
        """Slice _report_pool into current page; update page label."""
        pool = list(getattr(self, "_report_pool", None) or self._report_items or [])
        page_size = self._reports_page_size()
        total = len(pool)
        n_pages = max(1, (total + page_size - 1) // page_size) if total else 1
        page = int(getattr(self, "_report_page", 0) or 0)
        page = max(0, min(page, n_pages - 1))
        self._report_page = page
        start = page * page_size
        end = min(start + page_size, total)
        slice_ = pool[start:end]
        self._report_items = slice_
        if hasattr(self, "report_page_label"):
            if total == 0:
                self.report_page_label.configure(text="Page — · 0 people")
            else:
                self.report_page_label.configure(
                    text=(
                        f"Page {page + 1} / {n_pages}  ·  "
                        f"showing {start + 1}–{end} of {total:,}"
                    )
                )
        return slice_


    def _reports_next_page(self) -> None:
        pool = getattr(self, "_report_pool", None) or []
        if not pool and self._misclass_results:
            self._report_pool = self._reports_filtered_source()
            pool = self._report_pool
        page_size = self._reports_page_size()
        n_pages = max(1, (len(pool) + page_size - 1) // page_size) if pool else 1
        cur = int(getattr(self, "_report_page", 0) or 0)
        if cur + 1 >= n_pages:
            if hasattr(self, "report_status"):
                self.report_status.configure(text="Already on last page")
            return
        self._report_page = cur + 1
        self._reports_rebuild_cards(refilter=False)


    def _reports_prev_page(self) -> None:
        cur = int(getattr(self, "_report_page", 0) or 0)
        if cur <= 0:
            if hasattr(self, "report_status"):
                self.report_status.configure(text="Already on first page")
            return
        self._report_page = cur - 1
        self._reports_rebuild_cards(refilter=False)


