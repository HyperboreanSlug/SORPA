"""Browse tab: nested lazy sub-tabs (Search, Misclassify, Reports)."""
from __future__ import annotations

from typing import Any, Optional

import customtkinter as ctk

from gui_app.lazy_tabs import LazyTabHost
from gui_app.theme import C


class BrowseTabMixin:
    """Nested Browse tab host; sub-tabs load on first click."""

    def _build_browse(self, tab) -> Any:
        """Primary tab: search, misclassify (analyze + statistics), reports."""
        tab.configure(fg_color=C["surface"])
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
        self.browse_tabs = sub

        host = LazyTabHost(sub, on_change=self._on_browse_tab_change)
        self._browse_lazy = host

        host.register("Search", lambda p: self._build_search(p) or True)
        host.register("Misclassify", lambda p: self._build_misclass_host(p) or True)
        host.register("Reports", lambda p: self._build_reports(p) or True)

        try:
            sub.set("Search")
        except Exception:
            pass
        host.ensure("Search")
        return host

    def _build_misclass_host(self, tab) -> None:
        """Misclassify host: nested Analyze + Statistics sub-tabs."""
        tab.configure(fg_color=C["surface"])
        inner = ctk.CTkTabview(
            tab,
            fg_color=C["surface"],
            segmented_button_fg_color=C["elevated"],
            segmented_button_selected_color=C["accent_dim"],
            segmented_button_selected_hover_color=C["select"],
            segmented_button_unselected_color=C["elevated"],
            segmented_button_unselected_hover_color=C["panel"],
            text_color=C["text"],
            corner_radius=8,
            border_width=0,
        )
        inner.pack(fill="both", expand=True, padx=2, pady=2)
        self._misclass_inner_tabs = inner

        analyze_tab = inner.add("Analyze")
        stats_tab = inner.add("Statistics")
        self._build_misclass(analyze_tab)
        self._build_misclass_statistics(stats_tab)

    def _on_browse_tab_change(self, _name: Optional[str] = None) -> None:
        """Hook for Browse sub-tab switches."""
        pass
