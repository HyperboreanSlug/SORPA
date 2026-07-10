"""NSOPW main tab."""
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



class NsopwTabMixin:
    def _build_nsopw(self, tab):
        """NSOPW tab: options column (left) + full-height live inserts (right)."""
        tab.configure(fg_color=C["surface"])
        # Horizontal split: narrow options · wide results (uses empty width)
        split = _hpaned(tab)
        split.pack(fill="both", expand=True, padx=4, pady=4)
        self._nsopw_split = split

        opts_host = ctk.CTkFrame(split, fg_color=C["surface"], corner_radius=0)
        results_host = ctk.CTkFrame(split, fg_color=C["surface"], corner_radius=0)
        split.add(opts_host, minsize=300, stretch="never")
        split.add(results_host, minsize=420, stretch="always")
        self.after(120, lambda: self._set_sash(split, 0, 0.30))

        # First-name letters: default full A–Z; Indian abbreviated is optional
        self.nsopw_first_mode_var = ctk.StringVar(value="initials")
        self.nsopw_db_path = self.db_path
        self.nsopw_html_dir = "data/report_pages"
        self._nsopw_insert_count = 0

        # StringVars (blank max = unlimited)
        self.nsopw_max_searches = ctk.StringVar(value="40")
        self.nsopw_max_reports = ctk.StringVar(value="80")
        self.nsopw_search_delay = ctk.DoubleVar(value=3.0)
        self.nsopw_report_delay = ctk.DoubleVar(value=0.75)
        self.nsopw_enrich = ctk.BooleanVar(value=True)
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
                "hispanic", "asian", "indian", "indian_high_confidence",
                "african_american", "african", "arabic", "jewish",
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

        ctk.CTkLabel(
            limits,
            text="Live during a run · list/scope apply on next Start",
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

    def _nsopw_clear_tree(self):
        self.nsopw_tree.delete(*self.nsopw_tree.get_children())
        if getattr(self, "nsopw_tree_other", None) is not None:
            self.nsopw_tree_other.delete(*self.nsopw_tree_other.get_children())
        self._nsopw_insert_count = 0
        self._nsopw_other_count = 0
        self._nsopw_photo_by_iid = {}
        self._nsopw_records_by_iid = {}
        if getattr(self, "nsopw_detail", None) is not None:
            self._fill_detail_drawer(self.nsopw_detail, None)

    def _nsopw_toggle_surname_cap(self):
        """Enable max-surnames entry only when the limit toggle is on."""
        if self.nsopw_limit_surnames.get():
            self.nsopw_surnames_entry.configure(state="normal")
        else:
            self.nsopw_surnames_entry.configure(state="disabled")
        self._nsopw_update_surname_count()

    def _nsopw_on_ethnicity_change(self, _choice=None):
        self._nsopw_refresh_subcategories()
        self._nsopw_update_surname_count()

    def _nsopw_on_subcategory_change(self, _choice=None):
        self._nsopw_update_surname_count()

    def _nsopw_refresh_subcategories(self):
        """Reload subcategory dropdown for the current ethnicity."""
        from scraper.ethnic_names import get_ethnic_database

        eth = (self.nsopw_ethnicity.get() or "hispanic").strip().lower()
        db = get_ethnic_database()
        subs = db.subcategories(eth)
        if not subs:
            subs = ["all"]
        self.nsopw_sub_combo.configure(values=subs)
        # Default to all when list changes
        self.nsopw_subcategory.set("all" if "all" in subs else subs[0])
        # Enable only when real subgroups exist
        if db.has_subcategories(eth):
            self.nsopw_sub_combo.configure(state="normal")
        else:
            self.nsopw_sub_combo.configure(state="disabled")

    def _nsopw_surname_selection_params(self) -> tuple:
        """Return (ethnicity, subcategory, all_surnames, surnames_limit)."""
        eth = (self.nsopw_ethnicity.get() or "hispanic").strip().lower()
        sub = (self.nsopw_subcategory.get() or "all").strip().lower()
        limit_on = bool(self.nsopw_limit_surnames.get())
        all_surnames = not limit_on
        try:
            surnames_limit = int(self.nsopw_surnames_limit.get()) if limit_on else 0
        except (TypeError, ValueError):
            surnames_limit = 15 if limit_on else 0
        return eth, sub, all_surnames, surnames_limit

    def _nsopw_update_surname_count(self):
        """Show how many unique surnames the current filters select."""
        try:
            from scraper.ethnic_names import get_ethnic_database
            from scraper.nsopw_builder import (
                FIRST_INITIALS,
                NSOPWEthnicDatabaseBuilder,
                describe_first_mode,
                estimate_compact_query_count,
                first_initials_for_mode,
            )

            eth, sub, all_surnames, surnames_limit = self._nsopw_surname_selection_params()
            first_mode = (
                (self.nsopw_first_mode_var.get() or "initials").strip().lower()
                if hasattr(self, "nsopw_first_mode_var")
                else "initials"
            )
            firsts = first_initials_for_mode(first_mode)
            # Avoid full builder init (HTTP clients) — only need ethnic_db for selection
            light = object.__new__(NSOPWEthnicDatabaseBuilder)
            light.ethnic_db = get_ethnic_database()
            pairs = NSOPWEthnicDatabaseBuilder.surnames_for_ethnicity(
                light,
                eth,
                limit_per_group=surnames_limit,
                all_surnames=all_surnames,
                subcategory=sub,
            )
            n = len(pairs)
            naive = n * len(firsts)
            az_naive = n * len(FIRST_INITIALS)
            use_compact = bool(self.app_settings.get("nsopw_compact_prefixes", True))
            if hasattr(self, "settings_compact_prefixes"):
                use_compact = bool(self.settings_compact_prefixes.get())
            try:
                mcl = int(self.app_settings.get("nsopw_min_combined_len", 3))
                if hasattr(self, "settings_min_combined"):
                    mcl = int(str(self.settings_min_combined.get()).strip() or "3")
            except (TypeError, ValueError):
                mcl = 3
            mcl = max(3, min(mcl, 10))
            from scraper.nsopw_builder import (
                is_abbreviated_first_mode,
                last_prefix_whitelist_for,
            )
            last_allow = last_prefix_whitelist_for(
                eth,
                pairs,
                abbreviated=is_abbreviated_first_mode(first_mode),
                mode=first_mode,
            )
            if use_compact:
                est = estimate_compact_query_count(
                    pairs, firsts, min_combined=mcl, allowed_last_prefixes=last_allow
                )
                est_az = estimate_compact_query_count(
                    pairs, FIRST_INITIALS, min_combined=mcl
                )
                mode_txt = (
                    f"Est. queries: {est:,}  ·  {describe_first_mode(first_mode)}"
                )
                if first_mode not in ("initials", "all", "") and est_az != est:
                    mode_txt += f"  (A–Z would be {est_az:,})"
            else:
                est = naive
                mode_txt = (
                    f"Est. queries: {est:,} full surnames × {len(firsts)} firsts"
                )
                if first_mode not in ("initials", "all", "") and az_naive != est:
                    mode_txt += f"  (A–Z would be {az_naive:,})"
            scope = f"{eth}" + (f" / {sub}" if sub and sub != "all" else " / all groups")
            self.nsopw_surname_count_label.configure(
                text=f"Surnames in list: {n:,}  ({scope})  ·  {mode_txt}"
            )
        except Exception as e:
            self.nsopw_surname_count_label.configure(
                text=f"Surnames to search: (error computing count: {e})"
            )

    def _nsopw_append_row(self, record: Dict[str, Any]) -> None:
        """UI-thread: route insert into ethnicity-match or other-surnames table."""
        name = (
            (record.get("full_name") or "").strip()
            or f"{record.get('first_name') or ''} {record.get('last_name') or ''}".strip()
        )
        race = (record.get("race") or "").strip()
        eth = (record.get("ethnicity") or "").strip()
        race_disp = race
        if eth and eth.lower() != race.lower():
            race_disp = f"{race} / {eth}" if race else eth
        if not race_disp:
            race_disp = "—"
        photo_path = (record.get("photo_path") or "").strip()
        photo_mark = "yes" if photo_path and Path(photo_path).is_file() else (
            "url" if (record.get("photo_url") or "").strip() else "—"
        )
        crime = (
            (record.get("crime") or record.get("offense_description") or record.get("offense_type") or "")
            .strip()
            or "—"
        )
        vals = (
            name,
            record.get("state") or record.get("source_state") or "",
            race_disp,
            crime,
            photo_mark,
            record.get("source_url") or "",
            record.get("report_html_path") or "",
        )

        bucket = (record.get("nsopw_result_bucket") or "").strip().lower()
        if not bucket:
            # Fallback from flags JSON if builder field missing
            try:
                flags = record.get("flags")
                fl = json.loads(flags) if isinstance(flags, str) else (flags or [])
                if "other_surname" in fl:
                    bucket = "other"
                else:
                    bucket = "matched"
            except Exception:
                bucket = "matched"
        is_other = bucket == "other"
        tree = self.nsopw_tree_other if is_other else self.nsopw_tree

        sort_state = getattr(tree, "_sort_state", None) or {}
        if sort_state.get("col"):
            iid = tree.insert("", "end", values=vals)
        else:
            iid = tree.insert("", 0, values=vals)
        self._nsopw_records_by_iid[iid] = dict(record)
        if photo_path:
            self._nsopw_photo_by_iid[iid] = photo_path
        # Cap live table size
        kids = tree.get_children()
        if len(kids) > 200:
            for drop in kids[200:]:
                self._nsopw_photo_by_iid.pop(drop, None)
                self._nsopw_records_by_iid.pop(drop, None)
                tree.delete(drop)
        reapply = getattr(tree, "_reapply_sort", None)
        if callable(reapply) and sort_state.get("col"):
            reapply()

        if is_other:
            self._nsopw_other_count += 1
        else:
            self._nsopw_insert_count += 1
        # Keep chip stats in sync with live inserts (progress callback may lag)
        if hasattr(self, "_nsopw_stat_vars"):
            try:
                self._nsopw_stat_vars["matched"].configure(text=str(self._nsopw_insert_count))
                self._nsopw_stat_vars["other"].configure(text=str(self._nsopw_other_count))
            except Exception:
                pass
        # Do not wipe the current-search line — keep last query terms visible
        terms = getattr(self, "_nsopw_last_search_terms", "") or ""
        if terms:
            self.nsopw_status.configure(
                text=(
                    f"Running… {terms} · matched {self._nsopw_insert_count} · "
                    f"other {self._nsopw_other_count} (live)"
                )
            )
        else:
            self.nsopw_status.configure(
                text=(
                    f"Running… matched {self._nsopw_insert_count} · "
                    f"other surnames {self._nsopw_other_count} (live)"
                )
            )

    def _nsopw_on_tree_select(self, event=None):
        tree = event.widget if event is not None else self.nsopw_tree
        sel = tree.selection() if isinstance(tree, ttk.Treeview) else ()
        if not sel:
            return
        iid = sel[0]
        rec = self._nsopw_records_by_iid.get(iid)
        if rec is None:
            rec = {}
        # Attach photo from map / HTML assets if missing
        path = self._nsopw_photo_by_iid.get(iid) or (rec.get("photo_path") or "").strip()
        if not path or not Path(path).is_file():
            vals = tree.item(iid, "values")
            html_path = vals[-1] if len(vals) >= 5 else ""
            if html_path and html_path != "—":
                hp = Path(str(html_path))
                assets = hp.parent / f"{hp.stem}_assets"
                if assets.is_dir():
                    for cand in sorted(assets.iterdir()):
                        if cand.suffix.lower() in (
                            ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"
                        ) and cand.stat().st_size > 80:
                            path = str(cand)
                            self._nsopw_photo_by_iid[iid] = path
                            rec = dict(rec)
                            rec["photo_path"] = path
                            self._nsopw_records_by_iid[iid] = rec
                            break
        elif path and not rec.get("photo_path"):
            rec = dict(rec)
            rec["photo_path"] = path
            self._nsopw_records_by_iid[iid] = rec
        if getattr(self, "nsopw_detail", None) is not None:
            self._fill_detail_drawer(self.nsopw_detail, rec or None)

    def _nsopw_open_data_folder(self):
        path = Path("data")
        path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)

    def _open_path(self, path: Path):
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            messagebox.showerror("Cannot open", str(e))

    @staticmethod
    def _format_eta(seconds: Optional[float]) -> str:
        """Human ETA string from seconds remaining (None → still calculating)."""
        if seconds is None:
            return "ETA …"
        try:
            s = max(0, int(round(float(seconds))))
        except (TypeError, ValueError):
            return "ETA …"
        if s < 5:
            return "ETA <5s"
        if s < 60:
            return f"ETA ~{s}s"
        mins, sec = divmod(s, 60)
        if mins < 60:
            return f"ETA ~{mins}m {sec:02d}s" if sec else f"ETA ~{mins}m"
        hours, mins = divmod(mins, 60)
        if hours < 48:
            return f"ETA ~{hours}h {mins:02d}m"
        days, hours = divmod(hours, 24)
        return f"ETA ~{days}d {hours}h"

    def _nsopw_estimate_eta_seconds(self, info: Dict[str, Any]) -> Optional[float]:
        """
        Estimate remaining runtime from observed pace.

        Prefers new-search throughput when max_searches is set; otherwise plan
        steps. Blends wall-clock rate with configured search_delay as a floor.
        """
        import time as _time

        t0 = getattr(self, "_nsopw_run_t0", None)
        if t0 is None:
            return None
        now = _time.monotonic()
        elapsed = now - t0
        if elapsed < 1.0:
            return None

        searches = int(info.get("searches") or 0)
        plan_i = int(info.get("plan_i") or 0)
        plan_total = int(info.get("plan_total") or 0)
        search_cap = info.get("search_cap")
        try:
            search_delay = float(info.get("search_delay") or self.nsopw_search_delay.get() or 3.0)
        except (TypeError, ValueError):
            search_delay = 3.0
        search_delay = max(2.0, search_delay)

        # Work unit: new searches under a cap, else plan cursor
        remaining: Optional[float] = None
        rate: Optional[float] = None  # units per second

        if search_cap is not None:
            try:
                cap = int(search_cap)
            except (TypeError, ValueError):
                cap = 0
            if cap > 0:
                remaining = max(0.0, float(cap - searches))
                if searches >= 1:
                    rate = searches / elapsed
        if remaining is None and plan_total > 0:
            remaining = max(0.0, float(plan_total - plan_i))
            if plan_i >= 1:
                rate = plan_i / elapsed

        if remaining is not None and remaining <= 0:
            return 0.0
        if remaining is None:
            return None

        # Pace from recent samples (smoother than whole-run average)
        samples = getattr(self, "_nsopw_eta_samples", None)
        if samples is None:
            self._nsopw_eta_samples = []
            samples = self._nsopw_eta_samples
        work_done = float(searches if search_cap is not None else plan_i)
        samples.append((now, work_done))
        # Keep ~2 minutes of samples
        cutoff = now - 120.0
        self._nsopw_eta_samples = [(t, w) for t, w in samples if t >= cutoff]
        samples = self._nsopw_eta_samples
        if len(samples) >= 2:
            t_a, w_a = samples[0]
            t_b, w_b = samples[-1]
            dt = t_b - t_a
            dw = w_b - w_a
            if dt >= 3.0 and dw > 0:
                recent_rate = dw / dt
                rate = recent_rate if rate is None else (0.4 * rate + 0.6 * recent_rate)

        if rate is None or rate <= 0:
            # Before first completed unit: lower-bound from configured delay
            if search_cap is not None and remaining is not None:
                return remaining * search_delay
            return None

        eta = remaining / rate
        # Don't estimate faster than delay allows for remaining *new* searches
        if search_cap is not None:
            eta = max(eta, remaining * search_delay * 0.85)
        # Cap absurd estimates
        if eta > 7 * 24 * 3600:
            eta = 7 * 24 * 3600
        return eta

    def _nsopw_reset_progress_ui(self) -> None:
        """Zero the progress bar and statistic chips."""
        try:
            self.nsopw_progress.set(0)
            if hasattr(self, "nsopw_progress_label"):
                self.nsopw_progress_label.configure(text="0%")
            if hasattr(self, "nsopw_eta_label"):
                self.nsopw_eta_label.configure(text="ETA —")
            if hasattr(self, "nsopw_current_search_label"):
                self.nsopw_current_search_label.configure(text="—")
            self._nsopw_last_search_terms = ""
            self._nsopw_eta_samples = []
            for key, lbl in getattr(self, "_nsopw_stat_vars", {}).items():
                lbl.configure(text="0" if key != "plan" else "—")
        except Exception:
            pass

    def _nsopw_update_progress(self, info: Dict[str, Any]) -> None:
        """UI-thread: update determinate progress bar + stat chips + ETA."""
        try:
            done = float(info.get("done") or info.get("plan_i") or 0)
            total = float(info.get("total") or info.get("plan_total") or 0)
            # Prefer search-cap progress when available (matches live max searches)
            sc = info.get("search_cap")
            searches = int(info.get("searches") or 0)
            if sc is not None:
                try:
                    sc_n = float(sc)
                    if sc_n > 0:
                        total = sc_n
                        done = float(searches)
                except (TypeError, ValueError):
                    pass
            if total <= 0:
                frac = 0.0
            else:
                frac = min(1.0, max(0.0, done / total))
            self.nsopw_progress.set(frac)
            if hasattr(self, "nsopw_progress_label"):
                self.nsopw_progress_label.configure(text=f"{int(round(frac * 100))}%")

            eta_sec = self._nsopw_estimate_eta_seconds(info)
            eta_txt = self._format_eta(eta_sec)
            if hasattr(self, "nsopw_eta_label"):
                phase0 = (info.get("phase") or "").strip()
                if phase0 == "done":
                    self.nsopw_eta_label.configure(text="ETA done")
                elif phase0 == "cancelled":
                    self.nsopw_eta_label.configure(text="ETA —")
                else:
                    self.nsopw_eta_label.configure(text=eta_txt)

            plan_i = int(info.get("plan_i") or 0)
            plan_total = int(info.get("plan_total") or 0)
            skipped = int(info.get("searches_skipped") or 0)
            matched = int(info.get("inserted_matched") or self._nsopw_insert_count or 0)
            other = int(info.get("inserted_other") or self._nsopw_other_count or 0)
            hits = int(info.get("search_hits") or 0)
            html = int(info.get("html_saved") or 0)
            photos = int(info.get("photos_saved") or 0)
            race = int(info.get("reports_with_race") or 0)

            vars_ = getattr(self, "_nsopw_stat_vars", {})
            if "plan" in vars_:
                vars_["plan"].configure(
                    text=f"{plan_i}/{plan_total}" if plan_total else str(plan_i)
                )
            if "searches" in vars_:
                cap = info.get("search_cap")
                cap_s = f"/{cap}" if cap is not None else ""
                vars_["searches"].configure(
                    text=f"{searches}{cap_s}" + (f" (+{skipped} skip)" if skipped else "")
                )
            if "matched" in vars_:
                vars_["matched"].configure(text=str(matched))
            if "other" in vars_:
                vars_["other"].configure(text=str(other))
            if "hits" in vars_:
                vars_["hits"].configure(text=str(hits))
            if "html" in vars_:
                vars_["html"].configure(text=str(html))
            if "photos" in vars_:
                vars_["photos"].configure(text=str(photos))
            if "race" in vars_:
                vars_["race"].configure(text=str(race))

            phase = (info.get("phase") or "").strip()
            current = (info.get("current") or "").strip()
            # Structured search terms (preferred) or free-text current
            sf = (info.get("search_first") or "").strip()
            sl = (info.get("search_last") or "").strip()
            covers = (info.get("search_covers") or "").strip()
            lab = (info.get("search_label") or "").strip()
            if sf or sl:
                terms = f"first='{sf}' last='{sl}'"
                if covers:
                    terms += f" · covers {covers}"
                if lab:
                    terms += f" · {lab}"
            else:
                terms = current
            if terms and phase not in ("done", "cancelled", "start"):
                self._nsopw_last_search_terms = terms
            if hasattr(self, "nsopw_current_search_label"):
                if phase == "done":
                    self.nsopw_current_search_label.configure(text="complete")
                elif phase == "cancelled":
                    self.nsopw_current_search_label.configure(text="cancelled")
                elif phase == "start" or not terms:
                    self.nsopw_current_search_label.configure(text="starting…")
                elif phase == "resume_skip":
                    self.nsopw_current_search_label.configure(
                        text=f"skip {terms}" if terms else "skip…"
                    )
                else:
                    self.nsopw_current_search_label.configure(text=terms or "—")

            if phase == "done":
                pass  # status set by completion handler
            elif phase == "cancelled":
                self.nsopw_status.configure(text="Cancelled")
            elif terms or current:
                display = terms or current
                self.nsopw_status.configure(
                    text=(
                        f"Running… search {display} · {eta_txt} · "
                        f"matched {matched} · other {other} · "
                        f"plan {plan_i}/{plan_total or '—'}"
                    )
                )
        except Exception:
            pass

    def _cancel_nsopw(self):
        self._nsopw_cancel = True
        self.log_queue.put("NSOPW cancel requested… (stops within ~50ms of delay)")
        try:
            self.nsopw_status.configure(text="Cancelling… stopping ASAP")
            if hasattr(self, "nsopw_current_search_label"):
                self.nsopw_current_search_label.configure(text="cancelling…")
            if hasattr(self, "nsopw_eta_label"):
                self.nsopw_eta_label.configure(text="ETA —")
        except Exception:
            pass

    def _nsopw_parse_optional_limit(self, raw: Any) -> Optional[int]:
        """Blank / 0 / non-numeric → None (unlimited)."""
        text = (str(raw) if raw is not None else "").strip()
        if not text:
            return None
        try:
            n = int(text)
        except (TypeError, ValueError):
            return None
        return None if n <= 0 else n

    def _nsopw_capture_runtime_options(self) -> Dict[str, Any]:
        """Read current NSOPW operational knobs from the UI (main thread)."""
        try:
            search_delay = max(2.0, float(self.nsopw_search_delay.get()))
        except (TypeError, ValueError):
            search_delay = 3.0
        try:
            report_delay = max(0.25, float(self.nsopw_report_delay.get()))
        except (TypeError, ValueError):
            report_delay = 0.75
        repeat_old = bool(self.nsopw_repeat_searches.get())
        return {
            "max_searches": self._nsopw_parse_optional_limit(self.nsopw_max_searches.get()),
            "max_names": self._nsopw_parse_optional_limit(self.nsopw_max_reports.get()),
            "search_delay": search_delay,
            "report_delay": report_delay,
            "enrich_reports": bool(self.nsopw_enrich.get()),
            "save_html": bool(self.nsopw_save_html.get()),
            "skip_existing_urls": bool(self.nsopw_skip_existing.get()),
            "skip_completed_searches": not repeat_old,
            "new_files_only": bool(self.nsopw_new_files_only.get()),
        }

    def _nsopw_sync_runtime_options(self, *_args: Any) -> None:
        """Main-thread: snapshot UI options for the worker."""
        try:
            snap = self._nsopw_capture_runtime_options()
        except Exception:
            return
        with self._nsopw_runtime_lock:
            self._nsopw_runtime = snap

    def _nsopw_live_options(self) -> Dict[str, Any]:
        """Worker-thread: copy of latest operational knobs."""
        with self._nsopw_runtime_lock:
            return dict(self._nsopw_runtime)

    def _nsopw_bind_live_option_traces(self) -> None:
        """Re-sync runtime snapshot whenever the user edits live knobs."""
        if getattr(self, "_nsopw_live_traces_bound", False):
            return
        self._nsopw_live_traces_bound = True
        vars_ = [
            self.nsopw_max_searches,
            self.nsopw_max_reports,
            self.nsopw_search_delay,
            self.nsopw_report_delay,
            self.nsopw_enrich,
            self.nsopw_save_html,
            self.nsopw_repeat_searches,
            self.nsopw_skip_existing,
            self.nsopw_new_files_only,
        ]
        for v in vars_:
            try:
                v.trace_add("write", self._nsopw_sync_runtime_options)
            except Exception:
                try:
                    v.trace("w", self._nsopw_sync_runtime_options)  # type: ignore[attr-defined]
                except Exception:
                    pass

    def _start_nsopw(self):
        if self.is_running:
            return

        db_path = self.nsopw_db_path
        html_dir = self.nsopw_html_dir
        # Snapshot plan + initial knobs (plan is fixed; knobs stay live via callback)
        self._nsopw_sync_runtime_options()
        live0 = self._nsopw_live_options()
        search_delay = float(live0.get("search_delay") or 3.0)
        report_delay = float(live0.get("report_delay") or 0.75)
        eth, sub, all_surnames, surnames_limit = self._nsopw_surname_selection_params()
        # Settings tab: compact 3-letter partials (default on)
        use_compact = bool(self.app_settings.get("nsopw_compact_prefixes", True))
        if hasattr(self, "settings_compact_prefixes"):
            use_compact = bool(self.settings_compact_prefixes.get())
        try:
            min_combined = int(self.app_settings.get("nsopw_min_combined_len", 3))
            if hasattr(self, "settings_min_combined"):
                min_combined = int(str(self.settings_min_combined.get()).strip() or "3")
        except (TypeError, ValueError):
            min_combined = 3
        min_combined = max(3, min(min_combined, 10))

        self._nsopw_cancel = False
        self._nsopw_insert_count = 0
        self._nsopw_other_count = 0
        self._set_running(True)
        self.nsopw_start_btn.configure(state="disabled")
        self.nsopw_cancel_btn.configure(state="normal")
        self._nsopw_reset_progress_ui()
        import time as _time

        self._nsopw_run_t0 = _time.monotonic()
        self._nsopw_eta_samples = []
        if hasattr(self, "nsopw_eta_label"):
            self.nsopw_eta_label.configure(text="ETA …")
        self.nsopw_status.configure(
            text="Running NSOPW search… (edit delays/caps/checkboxes anytime)"
        )
        self.nsopw_tree.delete(*self.nsopw_tree.get_children())
        if getattr(self, "nsopw_tree_other", None) is not None:
            self.nsopw_tree_other.delete(*self.nsopw_tree_other.get_children())
        self._nsopw_photo_by_iid = {}
        self._nsopw_records_by_iid = {}
        if getattr(self, "nsopw_detail", None) is not None:
            self._fill_detail_drawer(self.nsopw_detail, None)

        def log(msg):
            self.log_queue.put(msg)

        def on_insert(record: Dict[str, Any]) -> None:
            # Marshal to UI thread
            self.after(0, lambda r=dict(record): self._nsopw_append_row(r))

        def on_progress(info: Dict[str, Any]) -> None:
            self.after(0, lambda d=dict(info): self._nsopw_update_progress(d))

        def worker():
            from scraper.nsopw_builder import NSOPWEthnicDatabaseBuilder

            builder = NSOPWEthnicDatabaseBuilder(
                db_path=db_path,
                delay=search_delay,
                report_delay=report_delay,
                html_dir=html_dir,
                cancel_check=lambda: self._nsopw_cancel,
            )
            try:
                stats = builder.build(
                    ethnicity=eth,
                    surnames_limit=surnames_limit,
                    all_surnames=all_surnames,
                    subcategory=sub,
                    first_names=None,
                    first_mode=(
                        (self.nsopw_first_mode_var.get() or "initials").strip().lower()
                        if hasattr(self, "nsopw_first_mode_var")
                        else "initials"
                    ),
                    jurisdictions=None,
                    max_searches=live0.get("max_searches"),
                    max_names=live0.get("max_names"),
                    skip_existing_urls=bool(live0.get("skip_existing_urls", True)),
                    skip_completed_searches=bool(live0.get("skip_completed_searches", True)),
                    new_files_only=bool(live0.get("new_files_only", True)),
                    enrich_reports=bool(live0.get("enrich_reports", True)),
                    save_html=bool(live0.get("save_html", True)),
                    use_compact_prefixes=use_compact,
                    min_combined_len=min_combined,
                    log=log,
                    on_insert=on_insert,
                    on_progress=on_progress,
                    live_options=self._nsopw_live_options,
                )

                def done():
                    self._set_running(False)
                    self.nsopw_start_btn.configure(state="normal")
                    self.nsopw_cancel_btn.configure(state="disabled")
                    # Final bar + chips from completed stats
                    self._nsopw_update_progress({
                        "plan_i": getattr(stats, "searches", 0) + getattr(stats, "searches_skipped", 0),
                        "plan_total": max(
                            getattr(stats, "searches", 0) + getattr(stats, "searches_skipped", 0),
                            1,
                        ),
                        "done": 1,
                        "total": 1,
                        "searches": stats.searches,
                        "searches_skipped": stats.searches_skipped,
                        "search_hits": stats.search_hits,
                        "inserted_matched": getattr(stats, "inserted_matched", stats.inserted),
                        "inserted_other": getattr(stats, "inserted_other", 0),
                        "html_saved": stats.html_saved,
                        "photos_saved": getattr(stats, "photos_saved", 0),
                        "reports_with_race": stats.reports_with_race,
                        "current": "complete",
                        "phase": "done",
                    })
                    self.nsopw_progress.set(1.0)
                    if hasattr(self, "nsopw_progress_label"):
                        self.nsopw_progress_label.configure(text="100%")
                    if hasattr(self, "nsopw_eta_label"):
                        self.nsopw_eta_label.configure(text="ETA done")
                    matched_n = getattr(stats, "inserted_matched", stats.inserted)
                    other_n = getattr(stats, "inserted_other", 0)
                    self.nsopw_status.configure(
                        text=(
                            f"Done · matched {matched_n} · other {other_n} · "
                            f"{stats.reports_with_race} with race · "
                            f"{stats.html_saved} HTML · "
                            f"{getattr(stats, 'photos_saved', 0)} photos · "
                            f"{stats.searches} new searches · "
                            f"{stats.searches_skipped} skipped (already done)"
                        )
                    )
                    self.db_path = db_path
                    messagebox.showinfo(
                        "NSOPW complete",
                        (
                            f"Inserted {stats.inserted} "
                            f"(ethnicity match {matched_n}, other surnames {other_n})\n"
                            f"New searches: {stats.searches}\n"
                            f"Skipped completed searches: {stats.searches_skipped}\n"
                            f"Reports with race: {stats.reports_with_race}\n"
                            f"HTML saved: {stats.html_saved}\n"
                            f"Photos saved: {getattr(stats, 'photos_saved', 0)}\n"
                            f"HTML skipped (cached): {stats.reports_skipped_existing_file}\n"
                            f"{db_path}"
                        ),
                    )

                self.after(0, done)
            except Exception as e:
                log(f"NSOPW ERROR: {e}")

                def fail():
                    self._set_running(False)
                    self.nsopw_start_btn.configure(state="normal")
                    self.nsopw_cancel_btn.configure(state="disabled")
                    self.nsopw_status.configure(text=f"Error: {e}")
                    messagebox.showerror("NSOPW error", str(e))

                self.after(0, fail)
            finally:
                builder.close()

        threading.Thread(target=worker, daemon=True).start()

    def _nsopw_open_selected(self, event=None):
        tree = event.widget if event is not None else self.nsopw_tree
        if not isinstance(tree, ttk.Treeview):
            tree = self.nsopw_tree
        sel = tree.selection()
        if not sel and getattr(self, "nsopw_tree_other", None) is not None:
            # Fallback: selection on the other tab
            for t in (self.nsopw_tree, self.nsopw_tree_other):
                if t.selection():
                    tree = t
                    sel = t.selection()
                    break
        if not sel:
            return
        iid = sel[0]
        vals = tree.item(iid, "values")
        # columns: name, state, race, crime, photo, url, html  (legacy layouts supported)
        if len(vals) >= 7:
            url, html_path = vals[5], vals[6]
        elif len(vals) >= 6:
            url, html_path = vals[4], vals[5]
        elif len(vals) >= 5:
            url, html_path = vals[3], vals[4]
        elif len(vals) >= 4:
            url, html_path = vals[2], vals[3]
        else:
            return

        photo_path = self._nsopw_photo_by_iid.get(iid)
        # Prefer opening HTML (includes embedded photos offline), then photo, then URL
        if html_path and html_path != "—":
            p = Path(html_path)
            if p.exists():
                self._open_path(p)
                return
        if photo_path and Path(photo_path).is_file():
            self._open_path(Path(photo_path))
            return
        if url:
            try:
                from scraper.public_links import resolve_public_source_url

                # Tree may only have the raw URL; prefer FL personId fix / search home
                target = resolve_public_source_url(url) or url
                webbrowser.open(target)
            except Exception as e:
                messagebox.showerror("Open link", str(e))

    # -----------------------------------------------------------------------
    # Shared
    # -----------------------------------------------------------------------
