"""DbSync"""
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


class SettingsDbSyncMixin:
    def _settings_on_db_sync_toggle(self) -> None:
        """Persist enable flag immediately when checkbox changes."""
        try:
            from scraper.app_settings import load_settings, save_settings, normalize_settings

            raw = load_settings()
            raw["db_sync_enabled"] = bool(self.settings_db_sync_enabled.get())
            raw["db_sync_on_startup"] = bool(raw["db_sync_enabled"])
            if raw["db_sync_enabled"]:
                raw["db_sync_prompted"] = True
            save_settings(raw)
            self.app_settings = normalize_settings(raw)
        except Exception:
            pass


    def _settings_db_sync_now(self) -> None:
        """Manual Refresh database now (GitHub Releases)."""
        if getattr(self, "_db_sync_running", False):
            return
        self._db_sync_running = True
        try:
            self.settings_db_sync_btn.configure(state="disabled")
            self.settings_db_sync_status.configure(text="Downloading…")
        except Exception:
            pass

        repo = (
            (self.settings_db_sync_repo.get() if hasattr(self, "settings_db_sync_repo") else "")
            or (self.app_settings or {}).get("db_sync_repo")
            or "HyperboreanSlug/SORPA"
        ).strip()
        tag = str((self.app_settings or {}).get("db_sync_tag") or "database-latest")
        try:
            from scraper.paths import resolve_under_root

            db_path = resolve_under_root(self.db_path)
        except Exception:
            db_path = Path(self.db_path)

        def worker():
            from scraper.db_sync import download_and_install_db

            result = download_and_install_db(
                db_path,
                repo=repo,
                tag=tag,
                force=True,
                log=lambda m: self.log_queue.put(f"DB sync: {m}"),
            )

            def done():
                self._db_sync_running = False
                try:
                    self.settings_db_sync_btn.configure(state="normal")
                except Exception:
                    pass
                try:
                    col = C["success"] if result.ok else C["danger"]
                    self.settings_db_sync_status.configure(
                        text=result.message, text_color=col
                    )
                except Exception:
                    pass
                if result.ok:
                    try:
                        from scraper.app_settings import (
                            load_settings,
                            save_settings,
                            normalize_settings,
                        )

                        raw = load_settings()
                        raw["db_sync_enabled"] = True
                        raw["db_sync_prompted"] = True
                        raw["db_sync_on_startup"] = True
                        raw["db_sync_repo"] = repo
                        save_settings(raw)
                        self.app_settings = normalize_settings(raw)
                        self.settings_db_sync_enabled.set(True)
                    except Exception:
                        pass
                    try:
                        self._after_db_data_changed()
                    except Exception:
                        try:
                            self._refresh_header_db_path()
                        except Exception:
                            pass
                else:
                    try:
                        messagebox.showerror("Database refresh", result.message)
                    except Exception:
                        pass

            try:
                self.after(0, done)
            except Exception:
                pass

        threading.Thread(target=worker, name="db-sync", daemon=True).start()


