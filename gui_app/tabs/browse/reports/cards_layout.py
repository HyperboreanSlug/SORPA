"""CLayout"""
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


class ReportsCardsLayoutMixin:
    def _reports_rebuild_cards(self, *, refilter: bool = True):
        """Destroy and recreate card widgets for the current page of results."""
        scroll = getattr(self, "_report_scroll", None)
        if scroll is None:
            return

        if refilter and (
            self._misclass_results
            or bool(
                getattr(self, "report_include_deepface", None)
                and self.report_include_deepface.get()
            )
        ):
            # Snapshot on UI thread, then filter (may open DB — keep refilter snappy)
            snap = self._reports_filter_snapshot()
            base = self._reports_filtered_source(
                verdict_key="all", snapshot=snap
            )
            self._report_metrics_base = base
            vfilter = str(snap.get("vfilter") or self._reports_verdict_filter_key())
            if vfilter == "all":
                self._report_pool = list(base)
            else:
                self._report_pool = [
                    mc
                    for mc in base
                    if self._reports_verdict_passes_filter(
                        self._verdict_for_mc(mc), vfilter
                    )
                ]
            # Keep page in range after refilter
            page_size = self._reports_page_size()
            n_pages = max(
                1,
                (len(self._report_pool) + page_size - 1) // page_size,
            ) if self._report_pool else 1
            self._report_page = min(int(getattr(self, "_report_page", 0) or 0), n_pages - 1)

        items = self._reports_apply_page()

        for child in list(scroll.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        self._report_image_refs = []

        if not items:
            empty = ctk.CTkLabel(
                scroll,
                text=(
                    "No people match the current Show / race filters.\n"
                    "Try Show → Unconfirmed, Confirmed incorrect, or Confirmed correct · "
                    "or enable White/Black/Other · re-run Analyze."
                ),
                font=FONT_SM, text_color=C["dim"], justify="left",
            )
            empty.pack(anchor="w", padx=16, pady=24)
            self._reports_update_metrics()
            return

        pool_n = len(getattr(self, "_report_pool", None) or items)
        page_size = self._reports_page_size()
        page = int(getattr(self, "_report_page", 0) or 0)
        offset = page * page_size
        grid = self._reports_is_grid()
        if grid:
            host = ctk.CTkFrame(scroll, fg_color="transparent")
            host.pack(fill="both", expand=True, padx=4, pady=4)
            # 1080p: ~2 rows (tile 332 + tight gaps). Prefer wider tiles / big photos.
            try:
                w = int(scroll.winfo_width() or 0)
            except Exception:
                w = 0
            if w < 200:
                w = 1000
            n_cols = max(3, min(6, w // 182))
            for c in range(n_cols):
                host.grid_columnconfigure(c, weight=1, uniform="rg")
            for i, mc in enumerate(items):
                card = self._reports_add_card(
                    host, mc, index=offset + i + 1, total=pool_n, grid=True
                )
                if card is not None:
                    card.grid(
                        row=i // n_cols,
                        column=i % n_cols,
                        padx=2,
                        pady=2,
                        sticky="nsew",
                    )
            # Re-flow columns once the scroll area has a real width
            try:
                self.after(
                    120,
                    lambda h=host, it=items, off=offset, pn=pool_n: (
                        self._reports_reflow_grid(h, it, offset=off, pool_n=pn)
                    ),
                )
            except Exception:
                pass
        else:
            for i, mc in enumerate(items):
                self._reports_add_card(
                    scroll, mc, index=offset + i + 1, total=pool_n, grid=False
                )

        # Re-bind fast scroll after widgets change
        try:
            tab = getattr(self, "_report_tab", None)
            if tab is not None:
                self.after(40, lambda: self._reports_bind_fast_scroll(tab, scroll))
        except Exception:
            pass

        self._reports_update_metrics()
        if hasattr(self, "report_status"):
            conf = sum(
                1 for mc in (getattr(self, "_report_pool", None) or items)
                if self._verdict_for_mc(mc) == "confirmed"
            )
            show = (self.report_verdict_filter.get() or "Unconfirmed").strip()
            self.report_status.configure(
                text=(
                    f"Show: {show} · pool {pool_n:,} · page {page + 1} · "
                    f"{conf:,} confirmed incorrect in pool · "
                    "Confirmed correct leaves Unconfirmed"
                )
            )


    def _reports_reflow_grid(
        self, host, items: list, *, offset: int, pool_n: int
    ) -> None:
        """Recompute grid columns after the scroll frame gets a real width."""
        if not self._reports_is_grid():
            return
        scroll = getattr(self, "_report_scroll", None)
        if scroll is None or host is None:
            return
        try:
            if not host.winfo_exists():
                return
        except Exception:
            return
        try:
            w = int(scroll.winfo_width() or 0)
        except Exception:
            w = 0
        if w < 200:
            return
        n_cols = max(3, min(6, w // 182))
        try:
            kids = [c for c in host.winfo_children() if c.winfo_exists()]
        except Exception:
            return
        if not kids:
            return
        for c in range(n_cols):
            try:
                host.grid_columnconfigure(c, weight=1, uniform="rg")
            except Exception:
                pass
        for i, child in enumerate(kids):
            try:
                child.grid(
                    row=i // n_cols,
                    column=i % n_cols,
                    padx=2,
                    pady=2,
                    sticky="nsew",
                )
            except Exception:
                pass


    def _reports_drop_card(self, card_widget, mc) -> None:
        """Remove one card from the UI and pools without rebuilding the page."""
        try:
            card_widget.destroy()
        except Exception:
            pass

        def _same(a, b) -> bool:
            try:
                return self._report_item_key(a) == self._report_item_key(b)
            except Exception:
                return a is b

        if getattr(self, "_report_items", None):
            self._report_items = [x for x in self._report_items if not _same(x, mc)]
        if getattr(self, "_report_pool", None):
            self._report_pool = [x for x in self._report_pool if not _same(x, mc)]

        self._reports_update_metrics()
        if hasattr(self, "report_status"):
            page_n = len(getattr(self, "_report_items", []) or [])
            pool_n = len(getattr(self, "_report_pool", []) or [])
            self.report_status.configure(
                text=f"Dropped · remaining on page {page_n:,} · pool {pool_n:,}"
            )


    def _reports_bind_fast_scroll(self, tab, scroll_frame) -> None:
        """Snappy wheel scrolling over report cards (fraction of viewport)."""
        try:
            canvas = scroll_frame._parent_canvas  # type: ignore[attr-defined]
        except Exception:
            return
        PAGE_FRAC = 0.22

        def _scroll(notches: int) -> None:
            if notches == 0:
                return
            try:
                first, last = canvas.yview()
                page = max(last - first, 0.05)
                step = notches * max(PAGE_FRAC * page, 0.08)
                canvas.yview_moveto(max(0.0, min(1.0, first + step)))
            except Exception:
                canvas.yview_scroll(notches * 10, "units")

        def _wheel(event):
            delta = getattr(event, "delta", 0) or 0
            if delta:
                notches = int(-delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
                if notches == 0:
                    notches = -1 if delta > 0 else 1
                _scroll(notches)
            else:
                num = getattr(event, "num", 0)
                if num == 4:
                    _scroll(-1)
                elif num == 5:
                    _scroll(1)
            return "break"

        def _walk(w):
            try:
                w.bind("<MouseWheel>", _wheel)
                w.bind("<Button-4>", _wheel)
                w.bind("<Button-5>", _wheel)
            except Exception:
                pass
            try:
                for ch in w.winfo_children():
                    _walk(ch)
            except Exception:
                pass

        try:
            _walk(tab)
            _walk(scroll_frame)
            for w in (
                tab,
                getattr(scroll_frame, "_parent_frame", None),
                canvas,
                scroll_frame,
            ):
                if w is None:
                    continue
                try:
                    w.bind("<MouseWheel>", _wheel)
                    w.bind("<Button-4>", _wheel)
                    w.bind("<Button-5>", _wheel)
                except Exception:
                    pass
        except Exception:
            pass


    def _reports_load_thumb(self, photo_path: str, max_size: tuple) -> Optional[Any]:
        """Load CTkImage fitted to *max_size* (no crop, keep aspect); keep refs for GC."""
        try:
            from PIL import Image

            from gui_app.shared.export_card_photo import contain_photo

            img = Image.open(photo_path)
            if getattr(img, "n_frames", 1) > 1:
                img.seek(0)
            img = img.convert("RGB")
            box = (max(1, int(max_size[0])), max(1, int(max_size[1])))
            # Letterbox into the tile box — same rules as export cards / HTML contain
            fitted = contain_photo(img, box)
            ctk_img = ctk.CTkImage(
                light_image=fitted, dark_image=fitted, size=box
            )
            if not hasattr(self, "_report_image_refs") or self._report_image_refs is None:
                self._report_image_refs = []
            self._report_image_refs.append(ctk_img)
            if len(self._report_image_refs) > 120:
                self._report_image_refs = self._report_image_refs[-80:]
            return ctk_img
        except Exception:
            return None


