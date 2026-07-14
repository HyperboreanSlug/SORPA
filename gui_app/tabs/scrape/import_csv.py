"""ScrapeImportMixin."""
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


class ScrapeImportMixin:
    def _import_downloads_folder(self):
        from scraper.database import Database

        folder = self.scrape_output_var.get() or "data/downloads"
        if not Path(folder).is_dir():
            messagebox.showwarning("Missing folder", f"Not a directory: {folder}")
            return
        skip = bool(self.scrape_import_skip.get())
        try:
            db = Database(self.db_path)
            try:
                summary = db.import_csv_directory(folder, skip_existing_urls=skip)
            finally:
                db.close()
        except Exception as e:
            messagebox.showerror("Import failed", str(e))
            return
        msg = (
            f"Files: {summary['files']} · imported {summary['imported']} · "
            f"skipped {summary['skipped']} · rows {summary['total_rows']}"
        )
        if summary.get("errors"):
            msg += f" · errors: {len(summary['errors'])}"
        self.scrape_import_status.configure(text=msg)
        self.log_queue.put(f"CSV import folder: {msg}")
        for err in summary.get("errors") or []:
            self.log_queue.put(f"  import error: {err}")
        self._after_db_data_changed()


    def _import_csv_file(self):
        from scraper.database import Database

        path = filedialog.askopenfilename(
            filetypes=[("CSV", "*.csv"), ("All", "*.*")],
            initialdir=self.scrape_output_var.get() or "data/downloads",
        )
        if not path:
            return
        skip = bool(self.scrape_import_skip.get())
        try:
            db = Database(self.db_path)
            try:
                result = db.import_csv(path, skip_existing_urls=skip)
            finally:
                db.close()
        except Exception as e:
            messagebox.showerror("Import failed", str(e))
            return
        msg = (
            f"{Path(path).name}: imported {result['imported']} · "
            f"skipped {result['skipped']} · rows {result['total_rows']}"
        )
        self.scrape_import_status.configure(text=msg)
        self.log_queue.put(f"CSV import: {msg}")
        self._after_db_data_changed()


    def _after_db_data_changed(self) -> None:
        """Refresh header; schedule Integrity only if that tab is built."""
        # Header count is async; never block the UI here.
        try:
            if hasattr(self, "schedule_header_refresh"):
                self.schedule_header_refresh(0)
            elif hasattr(self, "_refresh_header_db_path"):
                self._refresh_header_db_path()
        except Exception:
            pass
        # Integrity refresh is heavy — only if widgets exist; runs in a thread.
        if hasattr(self, "integrity_summary") and hasattr(self, "_refresh_integrity"):
            try:
                self.after(50, self._refresh_integrity)
            except Exception:
                pass
        note = "DB updated · open Misclassify → Analyze to include new rows"
        if hasattr(self, "misclass_status"):
            try:
                self.misclass_status.configure(text=note)
            except Exception:
                pass
        if hasattr(self, "mcstat_status"):
            try:
                self.mcstat_status.configure(text=note)
            except Exception:
                pass
        try:
            self.log_queue.put(note)
        except Exception:
            pass


