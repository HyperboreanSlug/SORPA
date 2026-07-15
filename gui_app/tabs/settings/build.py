"""Build"""
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


class SettingsBuildMixin:
    def _build_settings_general(self, tab):
        """General prefs (DB, sync, cookies, CAPTCHA) — nested under Settings."""
        tab.configure(fg_color=C["surface"])
        scroll = ctk.CTkScrollableFrame(tab, fg_color=C["surface"])
        scroll.pack(fill="both", expand=True, padx=8, pady=8)
        _wire_wide_scroll(tab, scroll)

        # --- Database ---
        db_card = _card(scroll)
        db_card.pack(fill="x", padx=4, pady=(4, 8))
        _section_label(db_card, "Database").pack(anchor="w", padx=14, pady=(12, 4))
        _muted(
            db_card,
            "Primary SQLite file used by Browse, Integrity, and NSOPW inserts.",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        self.settings_db_path = ctk.StringVar(value=str(self.db_path))
        db_row = ctk.CTkFrame(db_card, fg_color="transparent")
        db_row.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkEntry(
            db_row,
            textvariable=self.settings_db_path,
            fg_color=C["bg"],
            border_color=C["border"],
            text_color=C["text"],
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            db_row,
            text="Browse…",
            width=88,
            height=32,
            command=self._settings_browse_db,
            fg_color=C["elevated"],
            hover_color=C["border"],
            text_color=C["text"],
            border_width=1,
            border_color=C["border"],
        ).pack(side="left")

        # --- App code auto-update (git) ---
        upd_card = _card(scroll)
        upd_card.pack(fill="x", padx=4, pady=(0, 8))
        _section_label(upd_card, "App updates (GitHub)").pack(
            anchor="w", padx=14, pady=(12, 4)
        )
        _muted(
            upd_card,
            "On every open, check the git remote for new commits. When behind, "
            "fast-forward pull and reopen automatically. Requires git and a clone "
            "of the repo. Local data/ is never modified.",
        ).pack(anchor="w", padx=14, pady=(0, 8))
        self.settings_auto_update = ctk.BooleanVar(
            value=bool(self.app_settings.get("auto_update_enabled", True))
        )
        ctk.CTkCheckBox(
            upd_card,
            text="Auto-update app from GitHub on every open",
            variable=self.settings_auto_update,
            font=FONT_SM,
            text_color=C["text"],
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            checkmark_color=C["bg"],
            border_color=C["border"],
        ).pack(anchor="w", padx=14, pady=(0, 12))

        # --- Public database sync (GitHub Releases) ---
        sync_card = _card(scroll)
        sync_card.pack(fill="x", padx=4, pady=(0, 8))
        _section_label(sync_card, "Public database (GitHub)").pack(
            anchor="w", padx=14, pady=(12, 4)
        )
        _muted(
            sync_card,
            "Optional: download the shared public offenders archive from GitHub Releases "
            "(base SQLite + small delta packs + mugshots under data/report_pages/*/photos/). "
            "When enabled, the app checks for updates on every open and applies only new "
            "deltas when possible. This app never uploads — only the publisher machine does.",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        self.settings_db_sync_enabled = ctk.BooleanVar(
            value=bool(self.app_settings.get("db_sync_enabled", False))
        )
        # Always true when sync is enabled (kept for settings schema compatibility).
        self.settings_db_sync_on_startup = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            sync_card,
            text="Download / update database from GitHub (check every open)",
            variable=self.settings_db_sync_enabled,
            font=FONT_SM,
            text_color=C["text"],
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            checkmark_color=C["bg"],
            border_color=C["border"],
            command=self._settings_on_db_sync_toggle,
        ).pack(anchor="w", padx=14, pady=(0, 8))

        sync_act = ctk.CTkFrame(sync_card, fg_color="transparent")
        sync_act.pack(fill="x", padx=14, pady=(0, 8))
        self.settings_db_sync_btn = ctk.CTkButton(
            sync_act,
            text="Refresh database & photos",
            width=190,
            height=34,
            command=self._settings_db_sync_now,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color=C["bg"],
        )
        self.settings_db_sync_btn.pack(side="left", padx=(0, 8))
        self.settings_db_sync_status = ctk.CTkLabel(
            sync_act, text="", font=FONT_SM, text_color=C["muted"], anchor="w"
        )
        self.settings_db_sync_status.pack(side="left", fill="x", expand=True)

        self.settings_db_sync_repo = ctk.StringVar(
            value=str(
                self.app_settings.get("db_sync_repo")
                or "HyperboreanSlug/SORPA"
            )
        )
        repo_row = ctk.CTkFrame(sync_card, fg_color="transparent")
        repo_row.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkLabel(
            repo_row, text="GitHub repo", font=FONT_SM, text_color=C["muted"]
        ).pack(side="left", padx=(0, 8))
        ctk.CTkEntry(
            repo_row,
            textvariable=self.settings_db_sync_repo,
            fg_color=C["bg"],
            border_color=C["border"],
            text_color=C["text"],
            width=320,
        ).pack(side="left", fill="x", expand=True)

        # --- Backups ---
        bak_card = _card(scroll)
        bak_card.pack(fill="x", padx=4, pady=(0, 8))
        _section_label(bak_card, "Database backups").pack(anchor="w", padx=14, pady=(12, 4))
        _muted(
            bak_card,
            "Optional timestamped SQLite copies. Off by default — use Backup now, or enable "
            "auto-backup on close below.",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        self.settings_backup_on_close = ctk.BooleanVar(
            value=bool(self.app_settings.get("backup_on_close", False))
        )
        self.settings_backup_dir = ctk.StringVar(
            value=str(self.app_settings.get("backup_dir") or "data/backups")
        )
        self.settings_max_backups = ctk.StringVar(
            value=str(int(self.app_settings.get("max_backups", 10)))
        )

        ctk.CTkCheckBox(
            bak_card,
            text="Backup database when closing the app (optional)",
            variable=self.settings_backup_on_close,
            font=FONT_SM,
            text_color=C["text"],
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            checkmark_color=C["bg"],
            border_color=C["border"],
        ).pack(anchor="w", padx=14, pady=(0, 8))

        dir_row = ctk.CTkFrame(bak_card, fg_color="transparent")
        dir_row.pack(fill="x", padx=14, pady=(0, 8))
        ctk.CTkLabel(dir_row, text="Backup folder", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkEntry(
            dir_row,
            textvariable=self.settings_backup_dir,
            fg_color=C["bg"],
            border_color=C["border"],
            text_color=C["text"],
            width=320,
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            dir_row,
            text="Browse…",
            width=88,
            height=32,
            command=self._settings_browse_backup_dir,
            fg_color=C["elevated"],
            hover_color=C["border"],
            text_color=C["text"],
            border_width=1,
            border_color=C["border"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            dir_row,
            text="Open folder",
            width=100,
            height=32,
            command=self._settings_open_backup_dir,
            fg_color=C["elevated"],
            hover_color=C["border"],
            text_color=C["text"],
            border_width=1,
            border_color=C["border"],
        ).pack(side="left")

        keep_row = ctk.CTkFrame(bak_card, fg_color="transparent")
        keep_row.pack(fill="x", padx=14, pady=(0, 8))
        ctk.CTkLabel(
            keep_row, text="Keep last N backups (0 = unlimited)", font=FONT_SM, text_color=C["muted"]
        ).pack(side="left", padx=(0, 8))
        ctk.CTkEntry(
            keep_row,
            textvariable=self.settings_max_backups,
            width=72,
            fg_color=C["bg"],
            border_color=C["border"],
            text_color=C["text"],
        ).pack(side="left")

        act = ctk.CTkFrame(bak_card, fg_color="transparent")
        act.pack(fill="x", padx=14, pady=(4, 12))
        ctk.CTkButton(
            act,
            text="Backup now",
            height=36,
            font=FONT_BOLD,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color=C["bg"],
            command=self._settings_backup_now,
        ).pack(side="left", padx=(0, 8))
        self.settings_backup_status = ctk.CTkLabel(
            act, text="", font=FONT_SM, text_color=C["muted"], anchor="w"
        )
        self.settings_backup_status.pack(side="left", fill="x", expand=True)

        # --- NSOPW search strategy ---
        ns_card = _card(scroll)
        ns_card.pack(fill="x", padx=4, pady=(0, 8))
        _section_label(ns_card, "NSOPW search strategy").pack(anchor="w", padx=14, pady=(12, 4))
        _muted(
            ns_card,
            "NSOPW accepts partial first and last names. Combined length must be at least 3 "
            "letters (e.g. first=M, last=AH matches Mohamed Ahmed). Compact mode collapses "
            "surnames that share a short prefix so one query covers many list names. "
            "Last prefixes always come from the selected surname list (never brute-force "
            "AA–ZZ). Optional abbreviated mode (NSOPW tab → indian / indian_wide) shortens "
            "BOTH first letters (Indian A/S/R/P/M/K/V/N/B/D) AND surname digraphs (top "
            "Indian-likely combos like RA/CH/KA/PA/SH…). Default is full A–Z + all list digraphs.",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        self.settings_compact_prefixes = ctk.BooleanVar(
            value=bool(self.app_settings.get("nsopw_compact_prefixes", True))
        )
        ctk.CTkCheckBox(
            ns_card,
            text="Use short 3-letter partial prefixes (recommended — far fewer searches)",
            variable=self.settings_compact_prefixes,
            font=FONT_SM,
            text_color=C["text"],
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            checkmark_color=C["bg"],
            border_color=C["border"],
            command=self._settings_on_compact_toggle,
        ).pack(anchor="w", padx=14, pady=(0, 8))

        mcl_row = ctk.CTkFrame(ns_card, fg_color="transparent")
        mcl_row.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkLabel(
            mcl_row, text="Min combined first+last length", font=FONT_SM, text_color=C["muted"]
        ).pack(side="left", padx=(0, 8))
        self.settings_min_combined = ctk.StringVar(
            value=str(int(self.app_settings.get("nsopw_min_combined_len", 3)))
        )
        ctk.CTkEntry(
            mcl_row,
            textvariable=self.settings_min_combined,
            width=56,
            fg_color=C["bg"],
            border_color=C["border"],
            text_color=C["text"],
        ).pack(side="left")
        ctk.CTkLabel(
            mcl_row, text="(NSOPW API minimum is 3)", font=FONT_SM, text_color=C["dim"]
        ).pack(side="left", padx=(8, 0))

        # --- Access assistance (CAPTCHA / WAF — manual, not automated solvers) ---
        cap_card = _card(scroll)
        cap_card.pack(fill="x", padx=4, pady=(0, 8))
        _section_label(cap_card, "Access assistance (CAPTCHA / WAF)").pack(
            anchor="w", padx=14, pady=(12, 4)
        )
        _muted(
            cap_card,
            "Automated CAPTCHA solving is not supported. When a state site blocks the scraper, "
            "the URL is queued. Click Open next blocked URL → complete the challenge in Chrome/Edge "
            "→ cookies are pulled automatically from your browser for that host. "
            "You can also paste/export cookies manually below. Then requeue incomplete reports. "
            "Disclaimers/terms gates are still auto-accepted when possible.",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        self.settings_captcha_status = ctk.CTkLabel(
            cap_card, text="", font=FONT_SM, text_color=C["text"], anchor="w",
        )
        self.settings_captcha_status.pack(fill="x", padx=14, pady=(0, 6))
        self.settings_cookie_pull_status = ctk.CTkLabel(
            cap_card, text="", font=FONT_SM, text_color=C["dim"], anchor="w",
        )
        self.settings_cookie_pull_status.pack(fill="x", padx=14, pady=(0, 6))

        self._cookie_autopull_stop = False
        self._cookie_autopull_url = ""
        self._cookie_autopull_thread = None

        cap_btns = ctk.CTkFrame(cap_card, fg_color="transparent")
        cap_btns.pack(fill="x", padx=14, pady=(0, 6))
        ctk.CTkButton(
            cap_btns, text="Refresh queue", height=32, width=120,
            command=self._settings_refresh_captcha_queue,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            cap_btns, text="Open next blocked URL", height=32, width=160,
            command=self._settings_open_next_captcha,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            cap_btns, text="Open all blocked (max 5)", height=32, width=160,
            command=self._settings_open_captcha_batch,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            cap_btns, text="Pull cookies from browser", height=32, width=170,
            command=self._settings_pull_browser_cookies_now,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            cap_btns, text="Clear queue", height=32, width=100,
            command=self._settings_clear_captcha_queue,
            fg_color=C["elevated"], hover_color=C["danger"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left")

        ctk.CTkLabel(
            cap_card,
            text="Manual import (optional) — JSON list, Netscape cookies.txt, or Cookie: header",
            font=FONT_SM, text_color=C["muted"], anchor="w",
        ).pack(fill="x", padx=14, pady=(8, 4))
        self.settings_cookie_domain = ctk.StringVar(value="")
        dom_row = ctk.CTkFrame(cap_card, fg_color="transparent")
        dom_row.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(
            dom_row, text="Default domain (if paste has no domain)", font=FONT_SM,
            text_color=C["muted"],
        ).pack(side="left", padx=(0, 8))
        ctk.CTkEntry(
            dom_row, textvariable=self.settings_cookie_domain, width=220,
            placeholder_text="e.g. offender.fdle.state.fl.us",
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left")
        self.settings_cookie_text = ctk.CTkTextbox(
            cap_card, height=100, font=FONT_MONO,
            fg_color=C["bg"], text_color=C["text"],
            border_color=C["border"], border_width=1, corner_radius=8,
        )
        self.settings_cookie_text.pack(fill="x", padx=14, pady=(0, 6))
        cookie_btns = ctk.CTkFrame(cap_card, fg_color="transparent")
        cookie_btns.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkButton(
            cookie_btns, text="Import cookies", height=32, width=130,
            command=self._settings_import_cookies,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            cookie_btns, text="Load cookies file…", height=32, width=140,
            command=self._settings_load_cookie_file,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            cookie_btns, text="Clear saved cookies", height=32, width=140,
            command=self._settings_clear_cookies,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left")
        self.settings_cookie_status = ctk.CTkLabel(
            cookie_btns, text="", font=FONT_SM, text_color=C["muted"], anchor="w",
        )
        self.settings_cookie_status.pack(side="left", fill="x", expand=True, padx=(10, 0))

        # --- Save ---
        save_card = _card(scroll)
        save_card.pack(fill="x", padx=4, pady=(0, 8))
        save_row = ctk.CTkFrame(save_card, fg_color="transparent")
        save_row.pack(fill="x", padx=14, pady=12)
        ctk.CTkButton(
            save_row,
            text="Save settings",
            height=36,
            font=FONT_BOLD,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color=C["bg"],
            command=self._settings_save,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            save_row,
            text="Reset to defaults",
            height=36,
            command=self._settings_reset_defaults,
            fg_color=C["elevated"],
            hover_color=C["border"],
            text_color=C["text"],
            border_width=1,
            border_color=C["border"],
        ).pack(side="left", padx=(0, 8))
        self.settings_status = ctk.CTkLabel(
            save_row, text="", font=FONT_SM, text_color=C["muted"], anchor="w"
        )
        self.settings_status.pack(side="left", fill="x", expand=True)

        self.after(100, self._settings_refresh_status)
        self.after(120, self._settings_refresh_captcha_queue)


