"""DfrOpenMixin."""
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


class DfrOpenMixin:
    def _dfr_open_html(self) -> None:
        path = getattr(self, "_dfr_html_path", None)
        if path is None or not path.is_file():
            return
        if hasattr(self, "_open_path"):
            self._open_path(path)
        else:
            try:
                if os.name == "nt":
                    os.startfile(str(path))  # type: ignore[attr-defined]
                else:
                    webbrowser.open(path.as_uri())
            except Exception as e:
                messagebox.showerror("Open HTML", str(e))


    def _dfr_open_url(self) -> None:
        rec = getattr(self, "_dfr_current_record", None) or {}
        try:
            from scraper.public_links import multi_state_urls

            entries = multi_state_urls(rec)
        except Exception:
            entries = []
        if len(entries) > 1:
            menu = tk.Menu(self, tearoff=0)
            for state_label, u in entries:
                menu.add_command(
                    label=f"{state_label} registry",
                    command=lambda _u=u: webbrowser.open(_u),
                )
            try:
                x = self.dfr_btn_url.winfo_rootx()
                y = self.dfr_btn_url.winfo_rooty() + self.dfr_btn_url.winfo_height()
                menu.tk_popup(x, y)
            finally:
                menu.grab_release()
            return
        url = (getattr(self, "_dfr_source_url", None) or "").strip()
        if not url and entries:
            url = entries[0][1]
        if not url:
            return
        try:
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror("Open URL", str(e))


    def _dfr_open_photo(self) -> None:
        path = getattr(self, "_dfr_photo_open_path", None)
        if path is None or not path.is_file():
            return
        if hasattr(self, "_open_path"):
            self._open_path(path)
        else:
            try:
                if os.name == "nt":
                    os.startfile(str(path))  # type: ignore[attr-defined]
                else:
                    webbrowser.open(path.as_uri())
            except Exception as e:
                messagebox.showerror("Open photo", str(e))


    def _dfr_copy_detail(self) -> None:
        text = (getattr(self, "_dfr_meta_text", None) or "").strip()
        name = ""
        try:
            name = (self.dfr_name.cget("text") or "").strip()
        except Exception:
            pass
        if name and name != "—" and not text.startswith(name):
            text = f"{name}\n{text}" if text else name
        if not text:
            return
        if hasattr(self, "_copy_to_clipboard"):
            self._copy_to_clipboard(text, toast="DeepFace detail copied")
            if hasattr(self, "dfr_status"):
                try:
                    self.dfr_status.configure(text="Copied detail to clipboard")
                except Exception:
                    pass
        else:
            try:
                self.clipboard_clear()
                self.clipboard_append(text)
            except Exception as e:
                messagebox.showerror("Copy", str(e))

    def _dfr_export_card(self) -> None:
        """Export the selected DeepFace record as a share card to the Desktop."""
        rec = getattr(self, "_dfr_current_record", None)
        if not rec:
            return
        try:
            from gui_app.shared.export_card import export_record_card_to_desktop

            path = export_record_card_to_desktop(rec)
            if hasattr(self, "dfr_status"):
                try:
                    self.dfr_status.configure(text=f"Card → {path.name}")
                except Exception:
                    pass
        except Exception as e:
            messagebox.showerror("Export card", str(e))


