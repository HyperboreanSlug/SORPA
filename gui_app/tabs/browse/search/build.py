"""SearchBuildMixin."""
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


class SearchBuildMixin:
    def _build_search(self, tab):
        tab.configure(fg_color=C["surface"])
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))

        def _opt_label(text: str, *, first: bool = False) -> None:
            ctk.CTkLabel(
                bar, text=text, font=FONT_SM, text_color=C["muted"],
            ).pack(side="left", padx=((0 if first else 10), 4))

        _opt_label("Name", first=True)
        self.search_name_var = ctk.StringVar()
        ctk.CTkEntry(
            bar, textvariable=self.search_name_var, placeholder_text="First or last…",
            width=180, fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left", padx=(0, 0))

        _opt_label("State")
        self.search_state_var = ctk.StringVar(value="")
        _US_STATES = [
            "", "ALL",
            "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL", "GA",
            "HI", "IA", "ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME",
            "MI", "MN", "MO", "MS", "MT", "NC", "ND", "NE", "NH", "NJ", "NM",
            "NV", "NY", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX",
            "UT", "VA", "VT", "WA", "WI", "WV", "WY",
        ]
        ctk.CTkComboBox(
            bar, variable=self.search_state_var, width=90,
            values=_US_STATES,
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            button_hover_color=C["border"], dropdown_fg_color=C["panel"],
            dropdown_hover_color=C["elevated"], text_color=C["text"],
        ).pack(side="left", padx=(0, 0))

        _opt_label("Race")
        self.search_race_var = ctk.StringVar(value="")
        ctk.CTkComboBox(
            bar, variable=self.search_race_var, width=120,
            values=[
                "", "WHITE", "BLACK", "HISPANIC", "ASIAN", "INDIAN",
                "NATIVE AMERICAN", "OTHER",
            ],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            button_hover_color=C["border"], dropdown_fg_color=C["panel"],
            text_color=C["text"],
        ).pack(side="left", padx=(0, 0))

        # Surname-ethnicity lists (name-based; includes indian + high-confidence)
        _opt_label("Ethnicity")
        self.search_ethnicity_var = ctk.StringVar(value="")
        ctk.CTkComboBox(
            bar, variable=self.search_ethnicity_var, width=170,
            values=[
                "",
                "indian",
                "indian_high_confidence",
                "hispanic",
                "asian",
                "african_american",
                "arabic",
                "jewish",
                "portuguese",
                "native_american",
            ],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            button_hover_color=C["border"], dropdown_fg_color=C["panel"],
            text_color=C["text"],
        ).pack(side="left", padx=(0, 0))

        ctk.CTkButton(
            bar, text="Search", width=100, command=lambda: self._do_search(),
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(side="left", padx=(12, 6))
        ctk.CTkButton(
            bar, text="Show all", width=100,
            command=self._search_show_all,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left")

        mid = _hpaned(tab)
        mid.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 4))
        left = ctk.CTkFrame(mid, fg_color="transparent")
        mid.add(left, minsize=360, stretch="always")
        self.search_detail = self._make_detail_drawer(mid)
        mid.add(self.search_detail, minsize=220, stretch="never")
        self.after(160, lambda: self._set_sash(mid, 0, 0.72))

        wrap, self.search_tree = _tree_frame(left)
        wrap.pack(fill="both", expand=True)
        cols = ["name", "race", "state", "county", "age", "crime", "address"]
        self.search_tree.configure(columns=cols, show="headings")
        _stretch_columns(self.search_tree, cols, [140, 90, 50, 90, 45, 180, 160])
        _enable_tree_column_sort(
            self.search_tree, cols, labels={c: c.upper() for c in cols}
        )
        _bind_tree_scroll_isolation(self.search_tree, wrap)
        self.search_tree.bind("<<TreeviewSelect>>", self._search_on_select)
        self._search_records_by_iid: Dict[str, Dict[str, Any]] = {}

        self.search_status = ctk.CTkLabel(
            tab,
            text="Loading names…",
            font=FONT_SM, text_color=C["muted"],
        )
        self.search_status.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 10))
        # Default view: list of names (not race distribution stats)
        self.after(100, self._search_show_all)


    def _search_show_all(self) -> None:
        """Clear filters in the UI, then list all names."""
        try:
            self.search_name_var.set("")
            self.search_state_var.set("")
            self.search_race_var.set("")
            if hasattr(self, "search_ethnicity_var"):
                self.search_ethnicity_var.set("")
        except Exception:
            pass
        self._do_search(name="", state="", race="", ethnicity="")


