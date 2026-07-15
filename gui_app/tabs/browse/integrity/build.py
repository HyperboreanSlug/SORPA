"""IntegrityBuildMixin."""
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


class IntegrityBuildMixin:
    def _build_integrity(self, tab):
        """
        Integrity layout:
          - Management / requeue pinned at bottom (always visible)
          - Middle area scrolls (summary + state table)
        """
        tab.configure(fg_color=C["surface"])

        # --- Pinned management panel (pack bottom first so it stays visible) ---
        right = _card(tab)
        right.pack(side="bottom", fill="x", padx=12, pady=(4, 8))
        head = ctk.CTkFrame(right, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(10, 4))
        _section_label(head, "Integrity management · requeue incomplete reports").pack(
            side="left"
        )
        self.requeue_incomplete_label = ctk.CTkLabel(
            head, text="", font=FONT_SM, text_color=C["muted"],
        )
        self.requeue_incomplete_label.pack(side="right")
        _muted(
            right,
            "Re-downloads report pages for DB rows that have a source URL but are missing "
            "selected fields (race / crime / photo / HTML). Updates records in place.",
        ).pack(anchor="w", padx=14, pady=(0, 6))

        self.requeue_need_race = ctk.BooleanVar(value=True)
        self.requeue_need_crime = ctk.BooleanVar(value=True)
        self.requeue_need_photo = ctk.BooleanVar(value=True)
        self.requeue_need_html = ctk.BooleanVar(value=False)
        self.requeue_source_scope = ctk.StringVar(value="all")
        self.requeue_ethnicity = ctk.StringVar(value="all")
        chk_row = ctk.CTkFrame(right, fg_color="transparent")
        chk_row.pack(fill="x", padx=12, pady=2)
        for text, var in (
            ("Missing race", self.requeue_need_race),
            ("Missing crime", self.requeue_need_crime),
            ("Missing photo", self.requeue_need_photo),
            ("Missing HTML", self.requeue_need_html),
        ):
            ctk.CTkCheckBox(
                chk_row, text=text, variable=var, font=FONT_SM, text_color=C["text"],
                fg_color=C["accent"], hover_color=C["accent_hover"],
                checkmark_color=C["bg"], border_color=C["border"],
            ).pack(side="left", padx=(0, 14))

        scope_row = ctk.CTkFrame(right, fg_color="transparent")
        scope_row.pack(fill="x", padx=12, pady=(4, 2))
        ctk.CTkLabel(
            scope_row, text="Source scope", font=FONT_SM, text_color=C["muted"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkComboBox(
            scope_row, variable=self.requeue_source_scope, width=150,
            values=["all", "external_imports", "nsopw"],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
        ).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(
            scope_row, text="Ethnicity", font=FONT_SM, text_color=C["muted"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkComboBox(
            scope_row, variable=self.requeue_ethnicity, width=160,
            values=[
                "all", "hispanic", "asian",
                "indian/mena (merged)", "indian", "mena",
                "african_american",
            ],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
        ).pack(side="left")
        _muted(
            right,
            "Source scope: external_imports = bulk/direct CSV only (TX BCP, FL/GA CSV). "
            "Ethnicity uses the same surname classifiers as Misclassify → Analyze.",
        ).pack(anchor="w", padx=14, pady=(0, 4))

        lim_row = ctk.CTkFrame(right, fg_color="transparent")
        lim_row.pack(fill="x", padx=12, pady=(6, 12))
        ctk.CTkLabel(lim_row, text="Max rows", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(0, 6)
        )
        self.requeue_limit_var = ctk.IntVar(value=50)
        ctk.CTkEntry(
            lim_row, textvariable=self.requeue_limit_var, width=70,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left")
        ctk.CTkLabel(lim_row, text="Delay (s)", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(12, 6)
        )
        self.requeue_delay_var = ctk.DoubleVar(value=0.75)
        ctk.CTkEntry(
            lim_row, textvariable=self.requeue_delay_var, width=60,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left")
        ctk.CTkLabel(lim_row, text="Threads", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(12, 6)
        )
        self.requeue_threads_var = ctk.IntVar(value=4)
        ctk.CTkEntry(
            lim_row, textvariable=self.requeue_threads_var, width=48,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left")
        self.requeue_btn = ctk.CTkButton(
            lim_row, text="Requeue incomplete", height=32, width=150,
            command=self._start_requeue,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        )
        self.requeue_btn.pack(side="left", padx=(16, 8))
        self.requeue_cancel_btn = ctk.CTkButton(
            lim_row, text="Cancel", height=32, width=80, command=self._cancel_requeue,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"], state="disabled",
        )
        self.requeue_cancel_btn.pack(side="left", padx=(0, 10))
        self.requeue_status = ctk.CTkLabel(
            lim_row, text="Idle", font=FONT_SM, text_color=C["dim"],
        )
        self.requeue_status.pack(side="left")
        self.requeue_progress = ctk.CTkProgressBar(
            right, progress_color=C["accent"], fg_color=C["elevated"], height=6,
        )
        self.requeue_progress.pack(fill="x", padx=12, pady=(0, 10))
        self.requeue_progress.set(0)
        self._requeue_cancel = False

        # --- Scrollable body: summary + by-state table ---
        scroll = ctk.CTkScrollableFrame(tab, fg_color=C["surface"])
        scroll.pack(side="top", fill="both", expand=True, padx=4, pady=(4, 0))
        self._integrity_scroll = scroll

        top = ctk.CTkFrame(scroll, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(6, 4))
        ctk.CTkButton(
            top, text="Refresh", width=100, command=self._refresh_integrity,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            top, text="Export report CSV…", width=140, command=self._export_integrity_csv,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            top, text="Check duplicates", width=130, command=self._check_duplicates,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            top, text="Remove duplicates…", width=140, command=self._remove_duplicates,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=4)
        self.integrity_status = ctk.CTkLabel(
            top, text="", font=FONT_SM, text_color=C["muted"],
        )
        self.integrity_status.pack(side="right", padx=8)

        summary = _card(scroll)
        summary.pack(fill="x", padx=8, pady=(0, 6))
        _section_label(summary, "Archive integrity").pack(anchor="w", padx=14, pady=(8, 2))
        _muted(
            summary,
            "TOTAL = records in that state.  RACE/CRIME/PHOTO/HTML % = share with that field filled. "
            "Management settings are pinned at the bottom of this tab.",
        ).pack(anchor="w", padx=14, pady=(0, 4))
        self.integrity_summary = ctk.CTkLabel(
            summary, text="Click Refresh to load stats.",
            font=FONT_SM, text_color=C["text"], anchor="w", justify="left",
        )
        self.integrity_summary.pack(fill="x", padx=14, pady=(0, 8))

        table_card = _card(scroll)
        table_card.pack(fill="x", padx=8, pady=(0, 12))
        _section_label(table_card, "By state").pack(anchor="w", padx=14, pady=(10, 4))
        wrap, self.integrity_tree = _tree_frame(table_card)
        wrap.pack(fill="x", padx=10, pady=(0, 12))
        # Fixed tall viewport so many states show; outer frame still scrolls
        wrap.configure(height=420)
        wrap.pack_propagate(False)
        icols = [
            "state", "total", "pct_race", "pct_crime", "pct_photo", "pct_html",
            "with_race", "with_crime", "with_photo", "with_html",
        ]
        self.integrity_tree.configure(columns=icols, show="headings", height=16)
        _stretch_columns(
            self.integrity_tree,
            icols,
            [80, 90, 100, 100, 100, 100, 110, 110, 110, 110],
        )
        _enable_tree_column_sort(
            self.integrity_tree,
            icols,
            labels={
                "state": "STATE",
                "total": "TOTAL",
                "pct_race": "RACE %",
                "pct_crime": "CRIME %",
                "pct_photo": "PHOTO %",
                "pct_html": "HTML %",
                "with_race": "RACE COUNT",
                "with_crime": "CRIME COUNT",
                "with_photo": "PHOTO COUNT",
                "with_html": "HTML COUNT",
            },
        )
        _bind_tree_scroll_isolation(self.integrity_tree, wrap)

        self.after(200, self._refresh_integrity)


