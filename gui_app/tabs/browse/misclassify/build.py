"""MisclassifyBuildMixin — Analyze filters + results tree + sidebar."""
from __future__ import annotations

from typing import Any, Dict

import customtkinter as ctk

from gui_app.shared.record_sidebar import RecordSidebar
from gui_app.tabs.browse.misclassify.constants import (
    MISCLASS_ACTUAL_RACES,
    MISCLASS_COLS,
    MISCLASS_LABELS,
)
from gui_app.theme import C, FONT_SM
from gui_app.widgets import (
    _bind_tree_scroll_isolation,
    _card,
    _enable_tree_column_sort,
    _hpaned,
    _muted,
    _section_label,
    _stretch_columns,
    _tree_frame,
)
from gui_app.widgets_flow import FlowRow, after_idle_reflow


class MisclassifyBuildMixin:
    def _ensure_misclass_filter_vars(self) -> None:
        """Create Analyze filter vars even if Misclassify tab was never opened.

        Reports → Analyze & build and CSV export call into this path while the
        Misclassify UI (which used to create the vars) may still be lazy-unbuilt.
        """
        if not hasattr(self, "misclass_ethnicity_var"):
            self.misclass_ethnicity_var = ctk.StringVar(value="all")
        if not hasattr(self, "misclass_conf_var"):
            self.misclass_conf_var = ctk.DoubleVar(value=0.5)
        if not hasattr(self, "misclass_limit_var"):
            # 0 = scan entire DB; when capped, Analyze walks newest ids first
            self.misclass_limit_var = ctk.IntVar(value=0)
        if not hasattr(self, "misclass_hide_no_photo_var"):
            # Default on: tree only shows rows with a photo on disk
            self.misclass_hide_no_photo_var = ctk.BooleanVar(value=True)
        if not hasattr(self, "misclass_listed_var"):
            # Registry-listed race (recorded race), not surname ethnicity
            self.misclass_listed_var = ctk.StringVar(value="All")
        if not hasattr(self, "enrich_limit_var"):
            self.enrich_limit_var = ctk.IntVar(value=25)
        if not hasattr(self, "enrich_external_only_var"):
            self.enrich_external_only_var = ctk.BooleanVar(value=False)
        if not hasattr(self, "_misclass_results"):
            self._misclass_results = []
        if not hasattr(self, "_misclass_meta"):
            self._misclass_meta = {}

    def _misclass_controls_bar(self, parent) -> ctk.CTkFrame:
        """Shared Analyze filters (used by Misclassify + Statistics); wraps on resize."""
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        self._ensure_misclass_filter_vars()
        flow = FlowRow(bar, padx=5, pady=3)
        h = flow.host

        def _chip(label: str):
            chip = flow.chip()
            ctk.CTkLabel(
                chip, text=label, font=FONT_SM, text_color=C["muted"]
            ).pack(side="left", padx=(2, 4), pady=2)
            return chip

        eth = _chip("Likely ethnicity")
        from scraper.searcher_race import ETHNICITY_FILTER_UI_MISCLASS

        ctk.CTkComboBox(
            eth, variable=self.misclass_ethnicity_var, width=200,
            values=["all", *ETHNICITY_FILTER_UI_MISCLASS],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
        ).pack(side="left", pady=2)
        flow.add(eth)

        listed = _chip("Listed as")
        ctk.CTkComboBox(
            listed, variable=self.misclass_listed_var, width=110,
            values=["All", "White", "Black", "Other"],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
            command=lambda _v: self._misclass_on_display_filter_toggle(),
        ).pack(side="left", pady=2)
        flow.add(listed)

        conf = _chip("Min conf.")
        ctk.CTkEntry(
            conf, textvariable=self.misclass_conf_var, width=60,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left", pady=2)
        flow.add(conf)

        cap = _chip("Scan cap (0=all)")
        ctk.CTkEntry(
            cap, textvariable=self.misclass_limit_var, width=80,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left", pady=2)
        flow.add(cap)

        flow.add(
            ctk.CTkCheckBox(
                h, text="Remove without photo",
                variable=self.misclass_hide_no_photo_var,
                font=FONT_SM, text_color=C["text"],
                fg_color=C["accent"], hover_color=C["accent_hover"],
                checkmark_color=C["bg"], border_color=C["border"],
                command=self._misclass_on_display_filter_toggle,
            )
        )
        flow.add(
            ctk.CTkButton(
                h, text="Analyze", width=100, command=self._run_misclassification,
                fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
            )
        )
        flow.add(
            ctk.CTkButton(
                h, text="Export CSV", width=100, command=self._export_misclass,
                fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
                border_width=1, border_color=C["border"],
            )
        )
        enr = _chip("Enrich lim")
        ctk.CTkEntry(
            enr, textvariable=self.enrich_limit_var, width=52,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left", pady=2)
        flow.add(enr)
        flow.add(
            ctk.CTkCheckBox(
                h, text="External imports only",
                variable=self.enrich_external_only_var,
                font=FONT_SM, text_color=C["text"],
                fg_color=C["accent"], hover_color=C["accent_hover"],
                checkmark_color=C["bg"], border_color=C["border"],
            )
        )
        flow.add(
            ctk.CTkButton(
                h, text="NSOPW enrich", width=120,
                command=self._start_enrich_misclassified,
                fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
                border_width=1, border_color=C["border"],
            )
        )
        after_idle_reflow(self, flow)
        return bar

    def _build_misclass(self, tab):
        tab.configure(fg_color=C["surface"])
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        bar = self._misclass_controls_bar(tab)
        bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))

        mid = _hpaned(tab)
        mid.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 4))
        left = ctk.CTkFrame(mid, fg_color="transparent")
        mid.add(left, minsize=360, stretch="always")

        # MAPA-style confirm sidebar (wider pane kept for SORPA)
        self.misclass_sidebar = RecordSidebar(mid, photo_size=(340, 340))
        self.misclass_sidebar._actual_race_options = list(MISCLASS_ACTUAL_RACES)
        self.misclass_sidebar.bind_after(self.after)
        self.misclass_sidebar.bind_verdict(self._misclass_sidebar_verdict)
        self.misclass_sidebar.bind_actual_race(self._misclass_sidebar_actual_race)
        # Alias for any code that still expects misclass_detail
        self.misclass_detail = self.misclass_sidebar.frame
        mid.add(self.misclass_sidebar.frame, minsize=420, stretch="always")
        self.after(160, lambda: self._set_sash(mid, 0, 0.62))

        results_card = _card(left)
        results_card.pack(fill="both", expand=True)
        _section_label(results_card, "Potential mismatches").pack(
            anchor="w", padx=14, pady=(12, 4)
        )
        _muted(
            results_card,
            "Surname ethnicity does not match recorded race. "
            "Select a row → confirm classification in the sidebar.",
        ).pack(anchor="w", padx=14, pady=(0, 6))

        wrap, self.misclass_tree = _tree_frame(results_card)
        wrap.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        cols = list(MISCLASS_COLS)
        self.misclass_tree.configure(columns=cols, show="headings")
        # name · race · likely · conf · deepface · crime · confirmation
        _stretch_columns(
            self.misclass_tree, cols, [140, 90, 120, 70, 100, 140, 110]
        )
        _enable_tree_column_sort(
            self.misclass_tree,
            cols,
            labels=dict(MISCLASS_LABELS),
        )
        _bind_tree_scroll_isolation(self.misclass_tree, wrap)
        self.misclass_tree.bind("<<TreeviewSelect>>", self._misclass_on_select)
        self._misclass_records_by_iid: Dict[str, Dict[str, Any]] = {}
        self._misclass_mc_by_iid: Dict[str, Any] = {}

        self.misclass_status = ctk.CTkLabel(
            tab,
            text=(
                "Compare recorded race to surname ethnicity · "
                "sidebar: Classified correctly / incorrectly"
            ),
            font=FONT_SM, text_color=C["muted"],
        )
        self.misclass_status.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 10))
