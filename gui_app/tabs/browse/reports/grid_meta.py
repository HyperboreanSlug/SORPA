"""GMeta"""
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


class ReportsGridMetaMixin:
    def _reports_open_record_links(self, mc) -> None:
        """Open archived HTML, else live registry URL, else mugshot (NSOPW-style)."""
        rec = (getattr(mc, "record", None) or {}) if mc is not None else {}
        html_raw = (rec.get("report_html_path") or "").strip()
        photo_raw = (rec.get("photo_path") or "").strip()
        raw_url = (rec.get("source_url") or "").strip()
        try:
            from scraper.public_links import openable_url_for_record

            url = openable_url_for_record(rec) or raw_url
        except Exception:
            url = raw_url

        def _resolve(raw: str) -> Optional[Path]:
            if not raw:
                return None
            for p in (
                Path(raw),
                ROOT / raw,
                ROOT / raw.replace("\\", "/"),
                Path.cwd() / raw,
            ):
                try:
                    if p.is_file():
                        return p.resolve()
                except OSError:
                    continue
            return None

        html_path = _resolve(html_raw)
        if html_path is not None:
            if hasattr(self, "_open_path"):
                self._open_path(html_path)
            return
        if url:
            try:
                webbrowser.open(url)
            except Exception as e:
                messagebox.showerror("Open URL", str(e))
            return
        photo_path = _resolve(photo_raw)
        if photo_path is not None and hasattr(self, "_open_path"):
            self._open_path(photo_path)


    @staticmethod
    def _reports_verdict_label_short(verdict: str) -> str:
        return {
            "confirmed": "● Incorrect",
            "correct": "● Correct",
            "skip": "● Skip",
            "unreviewed": "○ Open",
        }.get(verdict, "○ Open")


    @staticmethod
    def _reports_crime_text(rec: Optional[Dict[str, Any]]) -> str:
        """Best available crime / offense text for report cards and exports."""
        if not rec:
            return ""
        keys = (
            "crime",
            "offense_description",
            "offense_type",
            "offense",
            "offenses",
            "charges",
            "charge",
            "conviction_offense",
            "statute",
            "statute_description",
        )
        for key in keys:
            raw = rec.get(key)
            if raw is None:
                continue
            s = str(raw).strip()
            if s and s.lower() not in ("none", "null", "n/a", "na", "-"):
                return s
        # Nested blobs sometimes hold the only offense text
        for blob_key in ("raw_data_json", "sources_json", "raw_data"):
            blob = rec.get(blob_key)
            if not blob:
                continue
            try:
                if isinstance(blob, str):
                    data = json.loads(blob)
                else:
                    data = blob
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            for key in keys:
                raw = data.get(key)
                if raw is None:
                    continue
                s = str(raw).strip()
                if s and s.lower() not in ("none", "null", "n/a", "na", "-"):
                    return s
        return ""


    @staticmethod
    def _reports_verdict_label(verdict: str) -> str:
        return {
            "confirmed": "● Confirmed incorrect",
            "correct": "● Confirmed correct",
            "skip": "● Skipped",
            "unreviewed": "○ Unconfirmed",
        }.get(verdict, "○ Unconfirmed")


    @staticmethod
    def _reports_verdict_color(verdict: str) -> str:
        return {
            "confirmed": C["danger"],
            "correct": C["success"],
            "skip": C["dim"],
            "unreviewed": C["muted"],
        }.get(verdict, C["muted"])


    def _reports_update_metrics(self) -> None:
        page_items = self._report_items or []
        pool = list(getattr(self, "_report_pool", None) or [])
        # Same race/photo/actual filters as the sheet, but all verdicts
        # (not the raw multi-10k analyze dump, which made chips look broken)
        source = list(getattr(self, "_report_metrics_base", None) or [])
        if not source:
            source = pool

        n_photo = 0
        n_conf = n_ok = n_un = n_skip = 0
        for mc in source:
            rec = mc.record or {}
            p = (rec.get("photo_path") or "").strip()
            if p and Path(p).is_file():
                n_photo += 1
            v = self._verdict_for_mc(mc)
            if v == "confirmed":
                n_conf += 1
            elif v == "correct":
                n_ok += 1
            elif v == "skip":
                n_skip += 1
            else:
                n_un += 1

        pool_n = len(pool)
        base_n = len(source)
        # Compact one-line strip (top of Reports); keep filters free of big chips
        if base_n and base_n != pool_n:
            sheet_bit = f"Sheet {pool_n:,}/{base_n:,}"
        else:
            sheet_bit = f"Sheet {pool_n:,}"
        line = (
            f"{sheet_bit}  ·  page {len(page_items):,}  ·  "
            f"photo {n_photo:,}  ·  ✗{n_conf:,}  ·  ✓{n_ok:,}  ·  ○{n_un:,}"
        )
        bar = getattr(self, "report_stats_bar", None)
        if bar is not None:
            try:
                bar.configure(text=line)
            except Exception:
                pass
        elif hasattr(self, "report_m_total"):
            try:
                self.report_m_total.configure(text=line)
            except Exception:
                pass


