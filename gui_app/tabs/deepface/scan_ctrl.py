"""ScanCtrl"""
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


class DeepfaceScanCtrlMixin:
    def _deepface_scan_refresh_db_stats(self) -> None:
        if not hasattr(self, "df_scan_db_stats"):
            return
        db_path = str(getattr(self, "db_path", None) or "data/offenders.db")

        def work():
            from scraper.database import Database

            db = Database(db_path)
            try:
                return db.count_deepface_scans()
            finally:
                db.close()

        def done(result=None, error=None):
            try:
                if error or not result:
                    self.df_scan_db_stats.configure(text="Stored: —")
                    return
                st = result
                self.df_scan_db_stats.configure(
                    text=(
                        f"Stored: {st.get('total', 0):,} scanned · "
                        f"{st.get('hits', 0):,} hits"
                    )
                )
            except Exception:
                pass

        if hasattr(self, "run_bg"):
            self.run_bg(work, done, name="df-scan-stats")
        else:
            try:
                done(result=work(), error=None)
            except Exception as e:
                done(result=None, error=e)


    def _deepface_scan_save_options(self) -> None:
        try:
            from scraper.app_settings import load_settings, save_settings, normalize_settings

            opts = self._deepface_scan_collect_options()
            raw = load_settings()
            raw["deepface_scan_state"] = opts["state"] or ""
            raw["deepface_scan_min_conf"] = str(opts["min_confidence"])
            raw["deepface_scan_limit"] = str(opts["limit"])
            raw["deepface_scan_recorded"] = ",".join(opts["recorded_races"])
            raw["deepface_scan_faces"] = ",".join(opts["face_labels"])
            raw["deepface_scan_force_rescan"] = bool(opts.get("force_rescan"))
            save_settings(raw)
            self.app_settings = normalize_settings(raw)
        except Exception:
            pass


    def _deepface_scan_set_busy(self, busy: bool) -> None:
        self._df_scan_running = busy
        try:
            self.df_scan_start_btn.configure(state="disabled" if busy else "normal")
            self.df_scan_stop_btn.configure(state="normal" if busy else "disabled")
        except Exception:
            pass


    def _deepface_scan_stop(self) -> None:
        self._df_scan_cancel = True
        self._deepface_scan_log_msg("Stop requested — finishing current photo…")
        try:
            self.df_scan_status.configure(
                text="Stopping…", text_color=C["accent"]
            )
        except Exception:
            pass


    def _deepface_scan_clear(self) -> None:
        self._df_scan_hits = []
        self._df_scan_hit_ids = set()
        self._df_scan_hits_by_iid = {}
        self._df_scan_selected_iid = None
        self._df_scan_image_refs = []
        try:
            self.df_scan_tree.delete(*self.df_scan_tree.get_children())
            self.df_scan_progress.set(0)
            self.df_scan_status.configure(
                text="Results cleared", text_color=C["dim"]
            )
            self._deepface_scan_clear_review()
        except Exception:
            pass


