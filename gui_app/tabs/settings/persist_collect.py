"""Collect"""
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


class SettingsCollectMixin:
    def _settings_collect(self) -> Dict[str, Any]:
        try:
            max_b = int(str(self.settings_max_backups.get()).strip() or "10")
        except ValueError:
            max_b = 10
        try:
            mcl = int(str(self.settings_min_combined.get()).strip() or "3")
        except ValueError:
            mcl = 3
        out: Dict[str, Any] = {
            "db_path": (self.settings_db_path.get() or "data/offenders.db").strip(),
            "backup_on_close": bool(self.settings_backup_on_close.get()),
            "backup_dir": (self.settings_backup_dir.get() or "data/backups").strip(),
            "max_backups": max_b,
            "nsopw_compact_prefixes": bool(self.settings_compact_prefixes.get()),
            "nsopw_min_combined_len": mcl,
        }
        # Preserve deepface + sync keys if widgets not built
        sett = getattr(self, "app_settings", {}) or {}
        for k in (
            "deepface_auto_setup",
            "deepface_auto_warm",
            "deepface_detector",
            "deepface_weight_models",
            "db_sync_prompted",
            "db_sync_tag",
            "db_publish_change_threshold",
            "db_auto_publish_enabled",
            "auto_update_enabled",
        ):
            if k in sett:
                out[k] = sett[k]
        if hasattr(self, "settings_auto_update"):
            out["auto_update_enabled"] = bool(self.settings_auto_update.get())
        else:
            out["auto_update_enabled"] = bool(sett.get("auto_update_enabled", True))
        if hasattr(self, "df_auto_setup"):
            out["deepface_auto_setup"] = bool(self.df_auto_setup.get())
        if hasattr(self, "df_auto_warm"):
            out["deepface_auto_warm"] = bool(self.df_auto_warm.get())
        if hasattr(self, "_deepface_selected_detector_id"):
            try:
                out["deepface_detector"] = self._deepface_selected_detector_id()
                out["deepface_weight_models"] = ",".join(
                    self._deepface_selected_weight_ids()
                )
            except Exception:
                pass
        if hasattr(self, "settings_db_sync_enabled"):
            out["db_sync_enabled"] = bool(self.settings_db_sync_enabled.get())
            # Always check on open when sync is enabled
            out["db_sync_on_startup"] = bool(out["db_sync_enabled"])
            out["db_sync_repo"] = (
                self.settings_db_sync_repo.get() or sett.get("db_sync_repo") or ""
            ).strip()
            # Enabling sync implies the user was prompted / chose
            if out["db_sync_enabled"]:
                out["db_sync_prompted"] = True
        else:
            out["db_sync_enabled"] = bool(sett.get("db_sync_enabled", False))
            out["db_sync_on_startup"] = bool(out["db_sync_enabled"])
            out["db_sync_repo"] = str(
                sett.get("db_sync_repo") or "HyperboreanSlug/SORPA"
            )
        if hasattr(self, "settings_db_auto_publish"):
            out["db_auto_publish_enabled"] = bool(self.settings_db_auto_publish.get())
        else:
            out["db_auto_publish_enabled"] = bool(
                sett.get("db_auto_publish_enabled", True)
            )
        if hasattr(self, "settings_db_publish_threshold"):
            try:
                out["db_publish_change_threshold"] = int(
                    str(self.settings_db_publish_threshold.get()).strip() or "2500"
                )
            except ValueError:
                out["db_publish_change_threshold"] = int(
                    sett.get("db_publish_change_threshold", 2500)
                )
        else:
            out["db_publish_change_threshold"] = int(
                sett.get("db_publish_change_threshold", 2500)
            )
        return out


    def _settings_apply_to_app(self, settings: Dict[str, Any]) -> None:
        from scraper.app_settings import normalize_settings, resolved_db_path

        self.app_settings = normalize_settings(settings)
        try:
            self.db_path = str(resolved_db_path(self.app_settings))
        except Exception:
            self.db_path = str(self.app_settings["db_path"])
        self.nsopw_db_path = self.db_path
        self._refresh_header_db_path()
        # Refresh NSOPW estimate if built
        if hasattr(self, "_nsopw_update_surname_count"):
            try:
                self._nsopw_update_surname_count()
            except Exception:
                pass


    def _settings_save(self) -> None:
        from scraper.app_settings import save_settings

        raw = self._settings_collect()
        path = save_settings(raw)
        self._settings_apply_to_app(raw)
        # Reflect normalized values back into widgets
        s = self.app_settings
        self.settings_db_path.set(str(s["db_path"]))
        self.settings_backup_dir.set(str(s["backup_dir"]))
        self.settings_max_backups.set(str(s["max_backups"]))
        self.settings_min_combined.set(str(s["nsopw_min_combined_len"]))
        self.settings_backup_on_close.set(bool(s["backup_on_close"]))
        self.settings_compact_prefixes.set(bool(s["nsopw_compact_prefixes"]))
        self.settings_status.configure(text=f"Saved → {path}")
        self._settings_refresh_status()


    def _settings_reset_defaults(self) -> None:
        from scraper.app_settings import DEFAULTS

        self.settings_db_path.set(str(DEFAULTS["db_path"]))
        self.settings_backup_on_close.set(bool(DEFAULTS["backup_on_close"]))
        self.settings_backup_dir.set(str(DEFAULTS["backup_dir"]))
        self.settings_max_backups.set(str(DEFAULTS["max_backups"]))
        self.settings_compact_prefixes.set(bool(DEFAULTS["nsopw_compact_prefixes"]))
        self.settings_min_combined.set(str(DEFAULTS["nsopw_min_combined_len"]))
        self.settings_status.configure(text="Defaults loaded — click Save settings to keep.")


