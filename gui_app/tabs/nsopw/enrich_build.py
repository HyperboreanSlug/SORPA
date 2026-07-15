"""NSOPW Enrich sub-tab — state-scoped report enrichment UI."""
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
    _section_label,
    _stretch_columns,
    _tree_frame,
)


class NsopwEnrichBuildMixin:
    def _build_nsopw_enrich(self, tab) -> None:
        """Enrich tab: pick a state, list incomplete rows, fetch detail sheets."""
        tab.configure(fg_color=C["surface"])
        split = _hpaned(tab)
        split.pack(fill="both", expand=True, padx=4, pady=4)
        self._nsopw_enrich_split = split

        opts_host = ctk.CTkFrame(split, fg_color=C["surface"], corner_radius=0)
        list_host = ctk.CTkFrame(split, fg_color=C["surface"], corner_radius=0)
        split.add(opts_host, minsize=300, stretch="never")
        split.add(list_host, minsize=420, stretch="always")
        self.after(140, lambda: self._set_sash(split, 0, 0.32))

        # Defaults for enrich controls
        if not hasattr(self, "nsopw_enrich_limit"):
            self.nsopw_enrich_limit = ctk.StringVar(value="100")
        if not hasattr(self, "nsopw_enrich_delay"):
            self.nsopw_enrich_delay = ctk.DoubleVar(value=0.75)
        if not hasattr(self, "nsopw_enrich_threads"):
            # Default 4 so single-state (e.g. FL) enrich actually parallelizes.
            self.nsopw_enrich_threads = ctk.IntVar(value=4)
        if not hasattr(self, "nsopw_enrich_need_race"):
            self.nsopw_enrich_need_race = ctk.BooleanVar(value=True)
            self.nsopw_enrich_need_crime = ctk.BooleanVar(value=True)
            self.nsopw_enrich_need_photo = ctk.BooleanVar(value=True)
            self.nsopw_enrich_need_html = ctk.BooleanVar(value=False)
        if not hasattr(self, "nsopw_enrich_scope_var"):
            self.nsopw_enrich_scope_var = ctk.StringVar(value="all")
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

        # State
        st_body = _card_body("State")
        if hasattr(self, "_nsopw_build_state_filter"):
            self._nsopw_build_state_filter(st_body)

        # What to fill
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

        # Run options
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
        ctk.CTkLabel(
            run,
            text="Threads fetch report pages in parallel (same state allowed).",
            font=FONT_SM, text_color=C["dim"], anchor="w", wraplength=300,
            justify="left",
        ).pack(fill="x", pady=(0, 2))
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
            run,
            text="Pick a state · reload list · enrich missing race/crime/photo",
            font=FONT_SM, text_color=C["muted"], anchor="w",
            wraplength=300, justify="left",
        )
        self.nsopw_enrich_status.pack(fill="x", pady=(2, 0))
        self.nsopw_enrich_stats_label = ctk.CTkLabel(
            run, text="Incomplete for state: —",
            font=FONT_SM, text_color=C["text"], anchor="w",
        )
        self.nsopw_enrich_stats_label.pack(fill="x", pady=(4, 0))

        # Incomplete list
        list_card = _card(list_host)
        list_card.pack(fill="both", expand=True, padx=(0, 2), pady=2)
        _section_label(
            list_card,
            "Incomplete for selected state · select a row for detail",
        ).pack(anchor="w", padx=12, pady=(10, 4))
        wrap, self.nsopw_enrich_tree = _tree_frame(list_card)
        wrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        cols = ["name", "state", "race", "crime", "photo", "url"]
        self.nsopw_enrich_tree.configure(columns=cols, show="headings")
        _stretch_columns(self.nsopw_enrich_tree, cols, [140, 48, 90, 180, 50, 200])
        _enable_tree_column_sort(
            self.nsopw_enrich_tree, cols,
            labels={c: c.upper() for c in cols},
        )
        _bind_tree_scroll_isolation(self.nsopw_enrich_tree, wrap)
        self.nsopw_enrich_tree.bind(
            "<<TreeviewSelect>>", self._nsopw_enrich_on_select
        )
        self.after(200, self._nsopw_enrich_reload_list)
