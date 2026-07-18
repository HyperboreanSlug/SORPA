"""SearchTreeMixin."""
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


class SearchTreeMixin:
    def _populate_search_tree(self, records):
        # Reset sort so a prior column sort cannot leave the tree looking empty
        try:
            st = getattr(self.search_tree, "_sort_state", None)
            if isinstance(st, dict):
                st["col"] = None
                st["reverse"] = False
        except Exception:
            pass
        # Detach selection/bindings side-effects before delete (avoids select storms)
        try:
            self.search_tree.selection_remove(*self.search_tree.selection())
        except Exception:
            pass
        self.search_tree.delete(*self.search_tree.get_children())
        self._search_records_by_iid = {}
        # Insert rows first so a detail-drawer photo glitch cannot blank results
        for r in records[:500] if records else []:
            name = (
                " ".join(
                    p for p in (
                        r.get("first_name") or "",
                        r.get("middle_name") or "",
                        r.get("last_name") or "",
                    ) if str(p).strip()
                ).strip()
                or (r.get("full_name") or "—")
            )
            if name and name != "—":
                name = str(name).upper()
            crime = (
                (r.get("crime") or r.get("offense_description") or r.get("offense_type") or "")
                or "—"
            )
            st = _format_state_display(r)
            iid = self.search_tree.insert(
                "",
                "end",
                values=(
                    name,  # full name — not truncated
                    _format_race_display(r.get("race")),
                    st,
                    r.get("county") or "—",
                    str(r.get("age") or ""),
                    crime,  # full crime text
                    r.get("address") or "—",
                ),
            )
            self._search_records_by_iid[iid] = dict(r)
        try:
            self.search_tree.yview_moveto(0)
        except Exception:
            pass
        if getattr(self, "search_detail", None) is not None:
            try:
                self._fill_detail_drawer(self.search_detail, None)
            except Exception as e:
                try:
                    self.log_queue.put(f"Detail drawer: {e}")
                except Exception:
                    pass


