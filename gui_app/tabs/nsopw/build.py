"""NSOPW tab layout."""
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


class NsopwBuildMixin:
    def _build_nsopw(self, tab):
        """NSOPW host: Search (surname harvest) + Enrich (state-scoped backfill)."""
        tab.configure(fg_color=C["surface"])
        self.nsopw_db_path = self.db_path
        self.nsopw_html_dir = "data/report_pages"
        self._nsopw_insert_count = 0

        host = ctk.CTkTabview(
            tab,
            fg_color=C["surface"],
            segmented_button_fg_color=C["elevated"],
            segmented_button_selected_color=C["accent_dim"],
            segmented_button_selected_hover_color=C["border"],
            segmented_button_unselected_color=C["elevated"],
            segmented_button_unselected_hover_color=C["border"],
            text_color=C["text"],
        )
        host.pack(fill="both", expand=True, padx=4, pady=4)
        self._nsopw_host_tabs = host
        search_tab = host.add("Search")
        enrich_tab = host.add("Enrich")
        self._build_nsopw_search(search_tab)
        if hasattr(self, "_build_nsopw_enrich"):
            self._build_nsopw_enrich(enrich_tab)

    def _build_nsopw_search(self, tab):
        """Search sub-tab: options column (left) + live inserts (right)."""
        tab.configure(fg_color=C["surface"])
        # Horizontal split: narrow options · wide results (uses empty width)
        split = _hpaned(tab)
        split.pack(fill="both", expand=True, padx=2, pady=2)
        self._nsopw_split = split

        opts_host = ctk.CTkFrame(split, fg_color=C["surface"], corner_radius=0)
        results_host = ctk.CTkFrame(split, fg_color=C["surface"], corner_radius=0)
        split.add(opts_host, minsize=300, stretch="never")
        split.add(results_host, minsize=420, stretch="always")
        self.after(120, lambda: self._set_sash(split, 0, 0.30))

        # First-name letters: default full A–Z; Indian abbreviated is optional
        self.nsopw_first_mode_var = ctk.StringVar(value="initials")

        # StringVars (blank max = unlimited)
        self.nsopw_max_searches = ctk.StringVar(value="40")
        self.nsopw_max_reports = ctk.StringVar(value="80")
        self.nsopw_search_delay = ctk.DoubleVar(value=3.0)
        self.nsopw_report_delay = ctk.DoubleVar(value=0.75)
        # Parallel report-fetch worker threads (1 = sequential). Applied on Start.
        self.nsopw_report_threads = ctk.IntVar(value=1)
        self.nsopw_enrich = ctk.BooleanVar(value=True)
        # When enrich is on: all hits vs ethnicity-list matches only
        self.nsopw_enrich_scope = ctk.StringVar(value="all")
        self.nsopw_save_html = ctk.BooleanVar(value=True)
        self.nsopw_skip_existing = ctk.BooleanVar(value=True)
        # Default: never re-run finished first+last API queries.
        self.nsopw_repeat_searches = ctk.BooleanVar(value=False)
        self.nsopw_new_files_only = ctk.BooleanVar(value=True)
        self.nsopw_limit_surnames = ctk.BooleanVar(value=False)
        self.nsopw_surnames_limit = ctk.IntVar(value=15)

        # ---- Left: scrollable options column ----
        opts = ctk.CTkScrollableFrame(
            opts_host, fg_color=C["surface"], corner_radius=0,
            scrollbar_button_color=C["elevated"],
            scrollbar_button_hover_color=C["border"],
        )
        opts.pack(fill="both", expand=True, padx=(2, 0), pady=2)

        def _opt_card(title: str) -> ctk.CTkFrame:
            outer = _card(opts)
            outer.pack(fill="x", padx=4, pady=(0, 6))
            ctk.CTkLabel(
                outer, text=title, font=FONT_BOLD, text_color=C["text"], anchor="w",
            ).pack(fill="x", padx=10, pady=(8, 2))
            body = ctk.CTkFrame(outer, fg_color="transparent")
            body.pack(fill="x", padx=8, pady=(0, 8))
            return body

        def _field_label(parent, text: str) -> None:
            ctk.CTkLabel(
                parent, text=text, font=FONT_SM, text_color=C["muted"], anchor="w",
            ).pack(fill="x", pady=(4, 1))

        # Run actions
        run = _opt_card("Run")
        self.nsopw_start_btn = ctk.CTkButton(
            run, text="Start NSOPW search", height=34, font=FONT_BOLD,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
            command=self._start_nsopw,
        )
        self.nsopw_start_btn.pack(fill="x", pady=(2, 4))
        act_row = ctk.CTkFrame(run, fg_color="transparent")
        act_row.pack(fill="x")
        self.nsopw_cancel_btn = ctk.CTkButton(
            act_row, text="Cancel", height=30, state="disabled",
            fg_color=C["elevated"], hover_color=C["danger"], text_color=C["text"],
            border_width=1, border_color=C["border"],
            command=self._cancel_nsopw,
        )
        self.nsopw_cancel_btn.pack(side="left", fill="x", expand=True, padx=(0, 3))
        ctk.CTkButton(
            act_row, text="Data folder", height=30,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
            command=self._nsopw_open_data_folder,
        ).pack(side="left", fill="x", expand=True, padx=3)
        ctk.CTkButton(
            act_row, text="Clear", height=30, width=64,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
            command=self._nsopw_clear_tree,
        ).pack(side="left", fill="x", expand=True, padx=(3, 0))

        prog_row = ctk.CTkFrame(run, fg_color="transparent")
        prog_row.pack(fill="x", pady=(8, 2))
        self.nsopw_progress = ctk.CTkProgressBar(
            prog_row, mode="determinate", progress_color=C["accent"],
            fg_color=C["elevated"], height=10,
        )
        self.nsopw_progress.pack(side="left", fill="x", expand=True)
        self.nsopw_progress.set(0)
        self.nsopw_progress_label = ctk.CTkLabel(
            prog_row, text="0%", font=FONT_SM, text_color=C["accent"], width=40, anchor="e",
        )
        self.nsopw_progress_label.pack(side="left", padx=(6, 0))
        eta_row = ctk.CTkFrame(run, fg_color="transparent")
        eta_row.pack(fill="x")
        self.nsopw_eta_label = ctk.CTkLabel(
            eta_row, text="ETA —", font=FONT_SM, text_color=C["muted"], anchor="w",
        )
        self.nsopw_eta_label.pack(side="left")
        self._nsopw_run_t0: Optional[float] = None
        self._nsopw_eta_samples: List[Tuple[float, float]] = []

        # Search scope
        scope = _opt_card("Search scope")
        _field_label(scope, "Surname list")
        self.nsopw_ethnicity = ctk.StringVar(value="hispanic")
        self.nsopw_eth_combo = ctk.CTkComboBox(
            scope,
            variable=self.nsopw_ethnicity,
            values=[
                "hispanic", "asian",
                "indian/mena (merged)", "indian", "mena",
                "african_american", "african", "jewish",
                "portuguese", "native_american", "european", "all",
            ],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
            command=self._nsopw_on_ethnicity_change,
        )
        self.nsopw_eth_combo.pack(fill="x")

        _field_label(scope, "Subcategory")
        self.nsopw_subcategory = ctk.StringVar(value="all")
        self.nsopw_sub_combo = ctk.CTkComboBox(
            scope,
            variable=self.nsopw_subcategory,
            values=["all"],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
            command=self._nsopw_on_subcategory_change,
            state="disabled",
        )
        self.nsopw_sub_combo.pack(fill="x")

        _field_label(scope, "First letters")
        self.nsopw_first_mode_combo = ctk.CTkComboBox(
            scope,
            variable=self.nsopw_first_mode_var,
            values=["initials", "indian", "indian_wide"],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
            command=lambda _c=None: self._nsopw_update_surname_count(),
        )
        self.nsopw_first_mode_combo.pack(fill="x")
        ctk.CTkLabel(
            scope,
            text="initials = A–Z · indian = abbreviated firsts + digraphs",
            font=FONT_SM, text_color=C["dim"], anchor="w", wraplength=260, justify="left",
        ).pack(fill="x", pady=(2, 4))

        cap_row = ctk.CTkFrame(scope, fg_color="transparent")
        cap_row.pack(fill="x", pady=(2, 0))
        ctk.CTkCheckBox(
            cap_row, text="Limit surnames/group",
            variable=self.nsopw_limit_surnames, font=FONT_SM, text_color=C["text"],
            fg_color=C["accent"], hover_color=C["accent_hover"],
            checkmark_color=C["bg"], border_color=C["border"],
            command=self._nsopw_toggle_surname_cap,
        ).pack(side="left")
        self.nsopw_surnames_entry = ctk.CTkEntry(
            cap_row, textvariable=self.nsopw_surnames_limit, width=52,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
            state="disabled",
        )
        self.nsopw_surnames_entry.pack(side="right")
        self.nsopw_surnames_entry.bind(
            "<KeyRelease>", lambda _e: self._nsopw_update_surname_count()
        )
        self.nsopw_surnames_entry.bind(
            "<FocusOut>", lambda _e: self._nsopw_update_surname_count()
        )
        self.nsopw_surname_count_label = ctk.CTkLabel(
            scope, text="Surnames to search: —",
            font=FONT_SM, text_color=C["text"], anchor="w",
        )
        self.nsopw_surname_count_label.pack(fill="x", pady=(6, 0))
        self._nsopw_refresh_subcategories()

        # Limits & delays — 2×2 grid
        limits = _opt_card("Limits & delays")
        lim_grid = ctk.CTkFrame(limits, fg_color="transparent")
        lim_grid.pack(fill="x")
        lim_grid.grid_columnconfigure((0, 1), weight=1)
        for i, (label, var, ph) in enumerate((
            ("Max searches", self.nsopw_max_searches, "∞"),
            ("Max names", self.nsopw_max_reports, "∞"),
            ("Search delay (s)", self.nsopw_search_delay, None),
            ("Report delay (s)", self.nsopw_report_delay, None),
        )):
            cell = ctk.CTkFrame(lim_grid, fg_color="transparent")
            cell.grid(row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)
            ctk.CTkLabel(
                cell, text=label, font=FONT_SM, text_color=C["muted"], anchor="w",
            ).pack(fill="x")
            ent = ctk.CTkEntry(
                cell, textvariable=var,
                fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
            )
            if ph:
                ent.configure(placeholder_text=ph)
            ent.pack(fill="x")

        thr_row = ctk.CTkFrame(limits, fg_color="transparent")
        thr_row.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(
            thr_row, text="Report threads", font=FONT_SM, text_color=C["muted"],
            anchor="w",
        ).pack(side="left")
        ctk.CTkEntry(
            thr_row, textvariable=self.nsopw_report_threads, width=52,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="right")
        ctk.CTkLabel(
            limits,
            text=(
                "Report threads fetch state pages in parallel (1 = sequential). "
                "No two threads ever hit the same state site at once; the search "
                "API stays serial. Applies on next Start."
            ),
            font=FONT_SM, text_color=C["dim"], anchor="w", wraplength=260,
            justify="left",
        ).pack(fill="x", pady=(2, 0))

        ctk.CTkLabel(
            limits,
            text="Live during a run · list/scope/threads apply on next Start",
            font=FONT_SM, text_color=C["dim"], anchor="w", wraplength=260, justify="left",
        ).pack(fill="x", pady=(4, 0))

        # Options checkboxes — stacked (readable, no horizontal crush)
        flags = _opt_card("Options")
        for text, var in (
            ("Fetch detail sheets", self.nsopw_enrich),
            ("Archive HTML", self.nsopw_save_html),
            ("Skip known URLs", self.nsopw_skip_existing),
            ("New HTML only", self.nsopw_new_files_only),
            ("Repeat old searches", self.nsopw_repeat_searches),
        ):
            ctk.CTkCheckBox(
                flags, text=text, variable=var, font=FONT_SM, text_color=C["text"],
                fg_color=C["accent"], hover_color=C["accent_hover"],
                checkmark_color=C["bg"], border_color=C["border"],
            ).pack(anchor="w", pady=2)

        enrich_scope_row = ctk.CTkFrame(flags, fg_color="transparent")
        enrich_scope_row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(
            enrich_scope_row, text="Enrich scope", font=FONT_SM, text_color=C["muted"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkComboBox(
            enrich_scope_row, variable=self.nsopw_enrich_scope, width=180,
            values=["all", "ethnicity_match"],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
        ).pack(side="left")
        ctk.CTkLabel(
            flags,
            text=(
                "all = every hit · ethnicity_match = only surnames on the "
                "selected ethnicity list (other surnames still saved, not enriched)"
            ),
            font=FONT_SM, text_color=C["dim"], anchor="w", wraplength=260, justify="left",
        ).pack(fill="x", pady=(2, 0))

        self._nsopw_bind_live_option_traces()
        self._nsopw_sync_runtime_options()

        # Live stats — compact 2-column grid
        stats = _opt_card("Live stats")
        stats_grid = ctk.CTkFrame(stats, fg_color=C["elevated"], corner_radius=8)
        stats_grid.pack(fill="x")
        stats_grid.grid_columnconfigure((0, 1), weight=1)
        self._nsopw_stat_vars: Dict[str, ctk.CTkLabel] = {}
        for i, (key, title) in enumerate((
            ("plan", "Plan"),
            ("searches", "Searches"),
            ("matched", "Matched"),
            ("other", "Other"),
            ("hits", "Hits"),
            ("html", "HTML"),
            ("photos", "Photos"),
            ("race", "Race"),
        )):
            cell = ctk.CTkFrame(stats_grid, fg_color="transparent")
            cell.grid(row=i // 2, column=i % 2, sticky="ew", padx=8, pady=4)
            ctk.CTkLabel(
                cell, text=title, font=FONT_SM, text_color=C["dim"], anchor="w",
            ).pack(anchor="w")
            val = ctk.CTkLabel(
                cell, text="—", font=FONT_BOLD, text_color=C["text"], anchor="w",
            )
            val.pack(anchor="w")
            self._nsopw_stat_vars[key] = val

        # Current query + status
        cur = _opt_card("Current search")
        self.nsopw_current_search_label = ctk.CTkLabel(
            cur, text="—", font=FONT_BOLD, text_color=C["accent"],
            anchor="w", wraplength=260, justify="left",
        )
        self.nsopw_current_search_label.pack(fill="x", pady=(0, 4))
        self._nsopw_last_search_terms = ""
        self.nsopw_status = ctk.CTkLabel(
            cur,
            text="Ready · blank max = unlimited · drag sash to resize",
            font=FONT_SM, text_color=C["muted"], anchor="w",
            wraplength=260, justify="left",
        )
        self.nsopw_status.pack(fill="x")
        self._nsopw_reset_progress_ui()

        # ---- Right: full-height live inserts + detail ----
        prev = _card(results_host)
        prev.pack(fill="both", expand=True, padx=(0, 2), pady=2)
        head = ctk.CTkFrame(prev, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(10, 4))
        _section_label(
            head,
            "Recent inserts · select for photo · double-click HTML / photo / URL",
        ).pack(side="left", anchor="w")

        # Resizable: tables | detail drawer
        inserts_split = _hpaned(prev)
        inserts_split.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        tables_host = ctk.CTkFrame(inserts_split, fg_color="transparent")
        inserts_split.add(tables_host, minsize=280, stretch="always")
        self.nsopw_detail = self._make_detail_drawer(inserts_split)
        inserts_split.add(self.nsopw_detail, minsize=200, stretch="never")
        self.after(200, lambda: self._set_sash(inserts_split, 0, 0.70))

        insert_tabs = ctk.CTkTabview(
            tables_host,
            fg_color=C["panel"],
            segmented_button_fg_color=C["elevated"],
            segmented_button_selected_color=C["accent_dim"],
            segmented_button_selected_hover_color=C["border"],
            segmented_button_unselected_color=C["elevated"],
            segmented_button_unselected_hover_color=C["border"],
            text_color=C["text"],
        )
        insert_tabs.pack(fill="both", expand=True, padx=0, pady=0)
        tab_matched = insert_tabs.add("Ethnicity match")
        tab_other = insert_tabs.add("Other surnames")
        self.nsopw_insert_tabs = insert_tabs

        cols = ["name", "state", "race", "crime", "photo", "url", "html"]
        col_labels = {
            "name": "NAME",
            "state": "STATE",
            "race": "RACE",
            "crime": "CRIME",
            "photo": "PHOTO",
            "url": "URL",
            "html": "HTML",
        }
        col_widths = [120, 48, 90, 160, 50, 180, 120]

        def _setup_insert_tree(parent) -> ttk.Treeview:
            wrap, tree = _tree_frame(parent)
            wrap.pack(fill="both", expand=True, padx=4, pady=4)
            tree.configure(columns=cols, show="headings")
            _stretch_columns(tree, cols, col_widths)
            _enable_tree_column_sort(tree, list(cols), labels=col_labels)
            _bind_tree_scroll_isolation(tree, wrap)
            tree.bind("<Double-1>", self._nsopw_open_selected)
            tree.bind("<<TreeviewSelect>>", self._nsopw_on_tree_select)
            return tree

        self.nsopw_tree = _setup_insert_tree(tab_matched)
        self.nsopw_tree_other = _setup_insert_tree(tab_other)
        self._nsopw_insert_count = 0
        self._nsopw_other_count = 0
        self._nsopw_records_by_iid: Dict[str, Dict[str, Any]] = {}
        self._nsopw_photo_by_iid: Dict[str, str] = {}


