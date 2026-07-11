"""Browse tab: nested lazy sub-tabs (Search, Integrity, …)."""
from __future__ import annotations

from typing import Any, Optional

import customtkinter as ctk

from gui_app.lazy_tabs import LazyTabHost
from gui_app.theme import C


class BrowseTabMixin:
    """Nested Browse tab host; sub-tabs load on first click."""

    def _build_browse(self, tab) -> Any:
        """Primary tab: search, integrity, misclassification, stats, reports."""
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
        host.register("Integrity", lambda p: self._build_integrity(p) or True)
        host.register("Misclassify", lambda p: self._build_misclass(p) or True)
        host.register("Statistics", lambda p: self._build_misclass_statistics(p) or True)
        host.register("Reports", lambda p: self._build_reports(p) or True)
        host.register("DeepFace", lambda p: self._build_deepface_reports(p) or True)

        try:
            sub.set("Search")
        except Exception:
            pass
        host.ensure("Search")
        return host

    def _on_browse_tab_change(self, _name: Optional[str] = None) -> None:
        """Hook for Browse sub-tab switches."""
        try:
            name = _name or self.browse_tabs.get()
        except Exception:
            name = ""
        if name == "DeepFace" and hasattr(self, "_dfr_refresh"):
            # Refresh hit list when opening the DeepFace review tab
            try:
                self.after(50, self._dfr_refresh)
            except Exception:
                pass
