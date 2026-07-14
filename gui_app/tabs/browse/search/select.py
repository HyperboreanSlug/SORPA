"""SearchSelectMixin."""
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


class SearchSelectMixin:
    def _search_on_select(self, _event=None):
        sel = self.search_tree.selection()
        if not sel:
            return
        iid = sel[0]
        rec = self._search_records_by_iid.get(iid)
        if not rec:
            return
        # Paint cached row immediately; refresh photo/html off UI if missing
        self._fill_detail_drawer(self.search_detail, rec)
        if rec.get("id") and not rec.get("photo_path"):
            db_path = str(getattr(self, "db_path", None) or "data/offenders.db")
            oid = int(rec["id"])

            def work():
                from scraper.database import Database

                db = Database(db_path)
                try:
                    full = db.get_offender_by_id(oid)
                    return dict(full) if full else None
                finally:
                    db.close()

            def done(result=None, error=None):
                if error or not result:
                    return
                # Only update if selection still this row
                try:
                    cur = self.search_tree.selection()
                    if not cur or cur[0] != iid:
                        return
                except Exception:
                    return
                self._search_records_by_iid[iid] = result
                self._fill_detail_drawer(self.search_detail, result)

            if hasattr(self, "run_bg"):
                self.run_bg(work, done, name="search-row")


    def _show_race_distribution(self, dist):
        self.search_tree.delete(*self.search_tree.get_children())
        self._search_records_by_iid = {}
        self._fill_detail_drawer(self.search_detail, None)
        total = sum(d.get("count", 0) for d in dist) or 1
        for d in dist:
            race = d.get("race") or "—"
            count = d.get("count", 0)
            pct = count / total * 100
            bar = "▮" * max(1, int(pct / 4))
            self.search_tree.insert(
                "", "end", values=(race, str(count), f"{pct:.1f}%", bar, "", "", "")
            )


