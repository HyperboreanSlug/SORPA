"""NSOPW Enrich sub-tab — state-scoped report enrichment + integrity coverage UI."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import customtkinter as ctk
from tkinter import ttk

from gui_app.theme import C, FONT_BOLD, FONT_SM
from gui_app.widgets import (
    _bind_tree_scroll_isolation,
    _card,
    _enable_tree_column_sort,
    _hpaned,
    _muted,
    _section_label,
    _stretch_columns,
    _tree_frame,
    _vpaned,
)


class NsopwEnrichBuildMixin:
    def _build_nsopw_enrich(self, tab) -> None:
        """Enrich tab: state pick, incomplete list, integrity coverage, dedupe."""
        tab.configure(fg_color=C["surface"])
        split = _hpaned(tab)
        split.pack(fill="both", expand=True, padx=4, pady=4)
        self._nsopw_enrich_split = split

        opts_host = ctk.CTkFrame(split, fg_color=C["surface"], corner_radius=0)
        right_host = ctk.CTkFrame(split, fg_color=C["surface"], corner_radius=0)
        split.add(opts_host, minsize=300, stretch="never")
        split.add(right_host, minsize=420, stretch="always")
        self.after(140, lambda: self._set_sash(split, 0, 0.30))

        if not hasattr(self, "nsopw_enrich_limit"):
            self.nsopw_enrich_limit = ctk.StringVar(value="100")
        if not hasattr(self, "nsopw_enrich_delay"):
            self.nsopw_enrich_delay = ctk.DoubleVar(value=0.75)
        if not hasattr(self, "nsopw_enrich_threads"):
            self.nsopw_enrich_threads = ctk.IntVar(value=4)
        if not hasattr(self, "nsopw_enrich_need_race"):
            self.nsopw_enrich_need_race = ctk.BooleanVar(value=True)
            self.nsopw_enrich_need_crime = ctk.BooleanVar(value=True)
            self.nsopw_enrich_need_photo = ctk.BooleanVar(value=True)
            self.nsopw_enrich_need_html = ctk.BooleanVar(value=False)
        if not hasattr(self, "nsopw_enrich_scope_var"):
            self.nsopw_enrich_scope_var = ctk.StringVar(value="all")
        if not hasattr(self, "nsopw_enrich_ethnicity_var"):
            self.nsopw_enrich_ethnicity_var = ctk.StringVar(value="all")
        self._nsopw_enrich_cancel = False
        self._nsopw_enrich_records: Dict[str, Dict[str, Any]] = {}

        opts = ctk.CTkScrollableFrame(
            opts_host, fg_color=C["surface"], corner_radius=0,
            scrollbar_button_color=C["elevated"],
            scrollbar_button_hover_color=C["border"],
        )
        opts.pack(fill="both", expand=True, padx=(2, 0), pady=2)

        def _card_body(title: str):
            outer = _card(opts)
            outer.pack(fill="x", padx=4, pady=(0, 6))
            ctk.CTkLabel(
                outer, text=title, font=FONT_BOLD, text_color=C["text"], anchor="w",
            ).pack(fill="x", padx=10, pady=(8, 2))
            body = ctk.CTkFrame(outer, fg_color="transparent")
            body.pack(fill="x", padx=8, pady=(0, 8))
            return body

        st_body = _card_body("State")
        if hasattr(self, "_nsopw_build_state_filter"):
            self._nsopw_build_state_filter(st_body)

        need = _card_body("Missing fields to fill")
        for text, var in (
            ("Race", self.nsopw_enrich_need_race),
            ("Crime", self.nsopw_enrich_need_crime),
            ("Photo", self.nsopw_enrich_need_photo),
            ("HTML archive", self.nsopw_enrich_need_html),
        ):
            ctk.CTkCheckBox(
                need, text=text, variable=var, font=FONT_SM, text_color=C["text"],
                fg_color=C["accent"], hover_color=C["accent_hover"],
                checkmark_color=C["bg"], border_color=C["border"],
            ).pack(anchor="w", pady=2)

        run = _card_body("Run")
        lim_row = ctk.CTkFrame(run, fg_color="transparent")
        lim_row.pack(fill="x", pady=2)
        ctk.CTkLabel(
            lim_row, text="Limit (0=all pending)", font=FONT_SM, text_color=C["muted"],
        ).pack(side="left")
        ctk.CTkEntry(
            lim_row, textvariable=self.nsopw_enrich_limit, width=72,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="right")
        del_row = ctk.CTkFrame(run, fg_color="transparent")
        del_row.pack(fill="x", pady=2)
        ctk.CTkLabel(
            del_row, text="Report delay (s)", font=FONT_SM, text_color=C["muted"],
        ).pack(side="left")
        ctk.CTkEntry(
            del_row, textvariable=self.nsopw_enrich_delay, width=72,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="right")
        thr_row = ctk.CTkFrame(run, fg_color="transparent")
        thr_row.pack(fill="x", pady=2)
        ctk.CTkLabel(
            thr_row, text="Report threads", font=FONT_SM, text_color=C["muted"],
        ).pack(side="left")
        ctk.CTkEntry(
            thr_row, textvariable=self.nsopw_enrich_threads, width=72,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="right")
        scope_row = ctk.CTkFrame(run, fg_color="transparent")
        scope_row.pack(fill="x", pady=(4, 2))
        ctk.CTkLabel(
            scope_row, text="Source scope", font=FONT_SM, text_color=C["muted"],
        ).pack(side="left")
        ctk.CTkComboBox(
            scope_row, variable=self.nsopw_enrich_scope_var, width=140,
            values=["all", "external_imports", "nsopw"],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
        ).pack(side="right")
        eth_row = ctk.CTkFrame(run, fg_color="transparent")
        eth_row.pack(fill="x", pady=(2, 2))
        ctk.CTkLabel(
            eth_row, text="Ethnicity", font=FONT_SM, text_color=C["muted"],
        ).pack(side="left")
        ctk.CTkComboBox(
            eth_row, variable=self.nsopw_enrich_ethnicity_var, width=140,
            values=["all", *(__import__(
                "scraper.searcher_race", fromlist=["ETHNICITY_FILTER_UI"]
            ).ETHNICITY_FILTER_UI)],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
        ).pack(side="right")

        self.nsopw_enrich_start_btn = ctk.CTkButton(
            run, text="Enrich incomplete for state", height=34, font=FONT_BOLD,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
            command=self._start_nsopw_state_enrich,
        )
        self.nsopw_enrich_start_btn.pack(fill="x", pady=(8, 4))
        act = ctk.CTkFrame(run, fg_color="transparent")
        act.pack(fill="x")
        self.nsopw_enrich_cancel_btn = ctk.CTkButton(
            act, text="Cancel", height=30, state="disabled",
            fg_color=C["elevated"], hover_color=C["danger"], text_color=C["text"],
            border_width=1, border_color=C["border"],
            command=self._cancel_nsopw_state_enrich,
        )
        self.nsopw_enrich_cancel_btn.pack(side="left", fill="x", expand=True, padx=(0, 3))
        ctk.CTkButton(
            act, text="Reload list", height=30,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
            command=self._nsopw_enrich_reload_list,
        ).pack(side="left", fill="x", expand=True, padx=(3, 0))

        self.nsopw_enrich_progress = ctk.CTkProgressBar(
            run, mode="determinate", progress_color=C["accent"],
            fg_color=C["elevated"], height=10,
        )
        self.nsopw_enrich_progress.pack(fill="x", pady=(8, 2))
        self.nsopw_enrich_progress.set(0)
        self.nsopw_enrich_status = ctk.CTkLabel(
            run, text="Pick a state · reload list · enrich missing fields",
            font=FONT_SM, text_color=C["muted"], anchor="w", wraplength=300, justify="left",
        )
        self.nsopw_enrich_status.pack(fill="x", pady=(2, 0))
        self.nsopw_enrich_stats_label = ctk.CTkLabel(
            run, text="Incomplete for state: —", font=FONT_SM, text_color=C["text"], anchor="w",
        )
        self.nsopw_enrich_stats_label.pack(fill="x", pady=(4, 0))

        integ = _card_body("Integrity")
        ibtn_row = ctk.CTkFrame(integ, fg_color="transparent")
        ibtn_row.pack(fill="x", pady=(0, 4))
        ctk.CTkButton(
            ibtn_row, text="Refresh", width=80, height=28,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
            command=self._nsopw_enrich_refresh_integrity,
        ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(
            ibtn_row, text="Export CSV…", width=90, height=28,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
            command=self._nsopw_enrich_export_csv,
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            ibtn_row, text="Check dupes", width=90, height=28,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
            command=self._check_duplicates,
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            ibtn_row, text="Remove dupes…", width=100, height=28,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
            command=self._remove_duplicates,
        ).pack(side="left", padx=2)
        self.nsopw_enrich_integrity_status = ctk.CTkLabel(
            integ, text="", font=FONT_SM, text_color=C["dim"], anchor="w",
        )
        self.nsopw_enrich_integrity_status.pack(fill="x")

        # --- Right: vertical split (coverage top, incomplete list bottom) ---
        vsplit = _vpaned(right_host)
        vsplit.pack(fill="both", expand=True, padx=(0, 2), pady=2)
        cov_host = ctk.CTkFrame(vsplit, fg_color=C["surface"], corner_radius=0)
        list_host = ctk.CTkFrame(vsplit, fg_color=C["surface"], corner_radius=0)
        vsplit.add(cov_host, minsize=160, stretch="always")
        vsplit.add(list_host, minsize=180, stretch="always")
        self.after(160, lambda: self._set_sash(vsplit, 0, 0.45))

        cov_card = _card(cov_host)
        cov_card.pack(fill="both", expand=True, padx=2, pady=2)
        _section_label(cov_card, "Archive integrity").pack(anchor="w", padx=12, pady=(8, 2))
        self.nsopw_enrich_summary = ctk.CTkLabel(
            cov_card, text="Click Refresh to load stats.",
            font=FONT_SM, text_color=C["text"], anchor="w", justify="left",
        )
        self.nsopw_enrich_summary.pack(fill="x", padx=12, pady=(0, 2))
        self.nsopw_enrich_incomplete_count = ctk.CTkLabel(
            cov_card, text="", font=FONT_SM, text_color=C["muted"], anchor="w",
        )
        self.nsopw_enrich_incomplete_count.pack(fill="x", padx=12, pady=(0, 4))
        wrap, self.nsopw_enrich_coverage_tree = _tree_frame(cov_card)
        wrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        icols = [
            "state", "total", "pct_race", "pct_crime", "pct_photo", "pct_html",
            "with_race", "with_crime", "with_photo", "with_html",
        ]
        self.nsopw_enrich_coverage_tree.configure(columns=icols, show="headings", height=10)
        _stretch_columns(
            self.nsopw_enrich_coverage_tree, icols,
            [70, 80, 80, 80, 80, 80, 90, 90, 90, 90],
        )
        _enable_tree_column_sort(
            self.nsopw_enrich_coverage_tree, icols,
            labels={
                "state": "STATE", "total": "TOTAL",
                "pct_race": "RACE %", "pct_crime": "CRIME %",
                "pct_photo": "PHOTO %", "pct_html": "HTML %",
                "with_race": "RACE #", "with_crime": "CRIME #",
                "with_photo": "PHOTO #", "with_html": "HTML #",
            },
        )
        _bind_tree_scroll_isolation(self.nsopw_enrich_coverage_tree, wrap)

        list_card = _card(list_host)
        list_card.pack(fill="both", expand=True, padx=2, pady=2)
        _section_label(
            list_card, "Incomplete for selected state · select a row for detail",
        ).pack(anchor="w", padx=12, pady=(8, 4))
        wrap2, self.nsopw_enrich_tree = _tree_frame(list_card)
        wrap2.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        cols = ["name", "state", "race", "crime", "photo", "url"]
        self.nsopw_enrich_tree.configure(columns=cols, show="headings")
        _stretch_columns(self.nsopw_enrich_tree, cols, [140, 48, 90, 180, 50, 200])
        _enable_tree_column_sort(
            self.nsopw_enrich_tree, cols, labels={c: c.upper() for c in cols},
        )
        _bind_tree_scroll_isolation(self.nsopw_enrich_tree, wrap2)
        self.nsopw_enrich_tree.bind("<<TreeviewSelect>>", self._nsopw_enrich_on_select)
        self.after(200, self._nsopw_enrich_reload_list)
        self.after(300, self._nsopw_enrich_refresh_integrity)
