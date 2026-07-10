"""Settings main tab."""
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
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from gui_app.theme import (
    C,
    FONT_BOLD,
    FONT_MONO,
    FONT_SECTION,
    FONT_SM,
    FONT_TITLE,
    FONT_UI,
    _style_treeview,
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
from gui_app.paths import ROOT



class SettingsTabMixin:
    def _build_settings(self, tab):
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
            "Automated CAPTCHA solving is not supported. When a state site shows a CAPTCHA "
            "or bot wall, the URL is queued. Open it in your browser, complete the challenge, "
            "export cookies for that site, paste them below, then requeue incomplete reports. "
            "Disclaimers/terms gates are still auto-accepted when possible.",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        self.settings_captcha_status = ctk.CTkLabel(
            cap_card, text="", font=FONT_SM, text_color=C["text"], anchor="w",
        )
        self.settings_captcha_status.pack(fill="x", padx=14, pady=(0, 6))

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
            cap_btns, text="Clear queue", height=32, width=100,
            command=self._settings_clear_captcha_queue,
            fg_color=C["elevated"], hover_color=C["danger"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left")

        ctk.CTkLabel(
            cap_card,
            text="Import cookies (JSON list, Netscape cookies.txt, or Cookie: header)",
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

    def _settings_refresh_captcha_queue(self) -> None:
        try:
            from scraper.cookie_jar import CaptchaQueue, CookieJarStore

            q = CaptchaQueue()
            items = q.list_items()
            jar = CookieJarStore()
            hosts = jar.summary()
            host_txt = ", ".join(f"{h}({n})" for h, n in list(hosts.items())[:6]) or "none"
            if not items:
                self.settings_captcha_status.configure(
                    text=f"Queue empty · saved cookie hosts: {host_txt}"
                )
            else:
                last = items[-1]
                self.settings_captcha_status.configure(
                    text=(
                        f"Queued: {len(items)} · latest [{last.get('jurisdiction') or '?'}] "
                        f"{last.get('reason')}: {(last.get('url') or '')[:70]}… · "
                        f"cookies: {host_txt}"
                    )
                )
        except Exception as e:
            if hasattr(self, "settings_captcha_status"):
                self.settings_captcha_status.configure(text=f"Queue error: {e}")

    def _settings_open_next_captcha(self) -> None:
        try:
            from scraper.cookie_jar import CaptchaQueue

            items = CaptchaQueue().list_items()
            if not items:
                messagebox.showinfo("CAPTCHA queue", "No blocked URLs queued.")
                return
            url = items[-1].get("url") or ""
            if url:
                webbrowser.open(url)
                self.settings_captcha_status.configure(
                    text=f"Opened in browser — complete challenge, then import cookies. {url[:60]}…"
                )
        except Exception as e:
            messagebox.showerror("Open URL", str(e))

    def _settings_open_captcha_batch(self) -> None:
        try:
            from scraper.cookie_jar import CaptchaQueue

            items = CaptchaQueue().list_items()
            if not items:
                messagebox.showinfo("CAPTCHA queue", "No blocked URLs queued.")
                return
            opened = 0
            for item in reversed(items[-5:]):
                url = item.get("url") or ""
                if url:
                    webbrowser.open(url)
                    opened += 1
            self.settings_captcha_status.configure(
                text=f"Opened {opened} blocked URL(s) in browser."
            )
        except Exception as e:
            messagebox.showerror("Open URLs", str(e))

    def _settings_clear_captcha_queue(self) -> None:
        try:
            from scraper.cookie_jar import CaptchaQueue

            CaptchaQueue().clear()
            self._settings_refresh_captcha_queue()
        except Exception as e:
            messagebox.showerror("Clear queue", str(e))

    def _settings_import_cookies(self) -> None:
        try:
            from scraper.cookie_jar import CookieJarStore

            raw = self.settings_cookie_text.get("1.0", "end")
            domain = (self.settings_cookie_domain.get() or "").strip()
            n = CookieJarStore().import_cookies(raw, default_domain=domain)
            self.settings_cookie_status.configure(
                text=f"Imported {n} cookie(s). Requeue incomplete reports to retry."
            )
            self._settings_refresh_captcha_queue()
            if n == 0:
                messagebox.showwarning(
                    "No cookies imported",
                    "Paste JSON cookies, Netscape cookies.txt lines, or a Cookie: header.\n"
                    "Set default domain if the paste has no domain field.",
                )
        except Exception as e:
            messagebox.showerror("Import cookies", str(e))

    def _settings_load_cookie_file(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Cookie file",
            filetypes=[
                ("Cookie / JSON / text", "*.txt *.json *.cookies"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            self.settings_cookie_text.delete("1.0", "end")
            self.settings_cookie_text.insert("1.0", text)
            self._settings_import_cookies()
        except Exception as e:
            messagebox.showerror("Load cookies", str(e))

    def _settings_clear_cookies(self) -> None:
        try:
            from scraper.cookie_jar import CookieJarStore

            CookieJarStore().clear()
            self.settings_cookie_status.configure(text="Saved cookies cleared.")
            self._settings_refresh_captcha_queue()
        except Exception as e:
            messagebox.showerror("Clear cookies", str(e))

    def _settings_collect(self) -> Dict[str, Any]:
        try:
            max_b = int(str(self.settings_max_backups.get()).strip() or "10")
        except ValueError:
            max_b = 10
        try:
            mcl = int(str(self.settings_min_combined.get()).strip() or "3")
        except ValueError:
            mcl = 3
        return {
            "db_path": (self.settings_db_path.get() or "data/offenders.db").strip(),
            "backup_on_close": bool(self.settings_backup_on_close.get()),
            "backup_dir": (self.settings_backup_dir.get() or "data/backups").strip(),
            "max_backups": max_b,
            "nsopw_compact_prefixes": bool(self.settings_compact_prefixes.get()),
            "nsopw_min_combined_len": mcl,
        }

    def _settings_apply_to_app(self, settings: Dict[str, Any]) -> None:
        from scraper.app_settings import normalize_settings

        self.app_settings = normalize_settings(settings)
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

    def _settings_browse_db(self) -> None:
        from tkinter import filedialog

        path = filedialog.asksaveasfilename(
            title="Database file",
            defaultextension=".db",
            filetypes=[("SQLite database", "*.db"), ("All files", "*.*")],
            initialfile=Path(self.settings_db_path.get() or "offenders.db").name,
        )
        if path:
            self.settings_db_path.set(path)

    def _settings_browse_backup_dir(self) -> None:
        from tkinter import filedialog

        path = filedialog.askdirectory(
            title="Backup folder",
            initialdir=self.settings_backup_dir.get() or "data",
        )
        if path:
            self.settings_backup_dir.set(path)

    def _settings_open_backup_dir(self) -> None:
        path = Path(self.settings_backup_dir.get() or "data/backups")
        path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)

    def _settings_on_compact_toggle(self) -> None:
        if hasattr(self, "_nsopw_update_surname_count"):
            try:
                self._nsopw_update_surname_count()
            except Exception:
                pass

    def _settings_refresh_status(self) -> None:
        bdir = Path(self.settings_backup_dir.get() or "data/backups")
        n = 0
        latest = "—"
        if bdir.is_dir():
            files = sorted(bdir.glob("offenders_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
            n = len(files)
            if files:
                latest = files[0].name
        dbp = Path(self.settings_db_path.get() or self.db_path)
        db_info = f"{dbp} ({dbp.stat().st_size // 1024} KB)" if dbp.is_file() else f"{dbp} (not created yet)"
        if hasattr(self, "settings_backup_status"):
            self.settings_backup_status.configure(
                text=f"DB: {db_info}  ·  {n} backup(s)  ·  latest: {latest}"
            )

    def _settings_backup_now(self) -> None:
        """Manual backup using current Settings fields (does not require Save first)."""
        try:
            dest, note = self._run_db_backup(
                db_path=self.settings_db_path.get() or self.db_path,
                backup_dir=self.settings_backup_dir.get() or "data/backups",
                max_backups=self.settings_max_backups.get(),
            )
            msg = f"Backed up → {dest}"
            if note:
                msg += f" ({note})"
            self.settings_backup_status.configure(text=msg)
            self.settings_status.configure(text=msg)
            self.log_queue.put(msg)
        except Exception as e:
            self.settings_backup_status.configure(text=f"Backup failed: {e}")
            messagebox.showerror("Backup failed", str(e))

    def _run_db_backup(
        self,
        db_path: Optional[str] = None,
        backup_dir: Optional[str] = None,
        max_backups: Any = None,
    ):
        from scraper.database import backup_database_file

        src = Path(db_path or self.db_path)
        if not src.exists():
            raise FileNotFoundError(f"Database not found: {src}")
        bdir = Path(backup_dir or self.app_settings.get("backup_dir") or "data/backups")
        try:
            keep = int(
                max_backups
                if max_backups is not None
                else self.app_settings.get("max_backups", 10)
            )
        except (TypeError, ValueError):
            keep = 10

        # backup_database_file opens its own connection + verifies integrity
        return backup_database_file(
            src, bdir, keep=keep, prefix="offenders", verify=True
        )

