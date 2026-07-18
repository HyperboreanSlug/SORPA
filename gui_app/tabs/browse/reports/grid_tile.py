"""GTile"""
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


class ReportsGridTileMixin:
    @staticmethod
    def _reports_list_display_name(name: str, *, max_len: int = 48) -> str:
        """Single-line list name with ellipsis — never wraps to a second row."""
        s = " ".join(str(name or "").split()).strip() or "—"
        if len(s) <= max_len:
            return s
        return s[: max(1, max_len - 1)].rstrip(" ,;.-") + "…"

    @staticmethod
    def _reports_grid_display_name(
        first: str, middle: str, last: str, full_name: str = "", *, max_len: int = 22
    ) -> str:
        """Single-line grid name: First M. Last; ellipsis — never wraps."""
        first = (first or "").strip()
        middle = (middle or "").strip()
        last = (last or "").strip()
        if not first and not last:
            base = (full_name or "—").strip() or "—"
            return base if len(base) <= max_len else base[: max_len - 1] + "…"

        def _join(mid: str) -> str:
            parts = [p for p in (first, mid, last) if p]
            return " ".join(parts)

        # Prefer full middle name if it fits
        full_mid = _join(middle)
        if len(full_mid) <= max_len:
            return full_mid
        # Middle initial
        if middle:
            initial = middle[0].upper() + "."
            with_init = _join(initial)
            if len(with_init) <= max_len:
                return with_init
        # Drop middle
        no_mid = _join("")
        if len(no_mid) <= max_len:
            return no_mid
        # Truncate last segment carefully — keep first + start of last
        if first and last:
            room = max_len - len(first) - 2  # space + …
            if room > 2:
                return f"{first} {last[:room]}…"
            return (first[: max_len - 1] + "…") if len(first) > max_len else first
        return (no_mid[: max_len - 1] + "…") if len(no_mid) > max_len else no_mid


    @staticmethod
    def _reports_summarize_crime(crime: str, *, max_len: int = 140) -> str:
        """Short human crime labels only — never statute dumps or registry junk."""
        import re

        try:
            from scraper.crime_summary import summarize_crime
            from scraper.crime_summary_junk import is_junk_label

            out = summarize_crime(crime, max_len=max_len)
        except Exception:
            out = ""
            is_junk_label = lambda _s: False  # noqa: E731
        if out:
            s = " ".join(str(out).split())
            # Never show FL docket crumbs (title-case can make 23-CF → 23-Cf)
            if re.search(r"(?i)\b\d{2,4}\s*[-–—]?\s*(?:cf|mm|ct|dr)\b", s):
                s = re.sub(
                    r"(?i)\b\d{2,4}\s*[-–—]?\s*(?:cf|mm|ct|dr)(?:\s*[-–—]?\s*\d+)?\b",
                    " ",
                    s,
                )
                s = " ".join(s.split())
            try:
                if not s or is_junk_label(s):
                    return ""
            except Exception:
                pass
            if not s:
                return ""
            if len(s) <= max_len:
                return s
            cut = s[: max_len - 1]
            if " " in cut:
                cut = cut.rsplit(" ", 1)[0]
            return cut.rstrip(" ,;:·") + "…"
        # No raw fallback — registry dumps embed case numbers / F.S. cites
        return ""


    def _reports_add_grid_tile(
        self,
        parent,
        mc,
        rec,
        *,
        first: str,
        middle: str,
        last: str,
        state: str,
        race: str,
        conf: float,
        crime: str,
        df: dict,
        photo_path: str,
        has_photo: bool,
        verdict: str,
        border: str,
        index: int,
        conf_text: str = "",
    ):
        """Grid tile: max photo fills box; checkbox + Export (single card)."""
        conf_label = (conf_text or "").strip() or f"{float(conf or 0):.2f}"
        # Photo takes most of the tile; chrome stays compact under it
        _W, _H = 180, 340
        _PHOTO_H = 220
        card = ctk.CTkFrame(
            parent,
            fg_color=C["panel"],
            border_color=border,
            border_width=1,
            corner_radius=4,
            width=_W,
            height=_H,
        )
        card.grid_propagate(False)
        card.pack_propagate(False)

        # Photo fills full tile width and allotted height (cover, no letterbox)
        photo_wrap = ctk.CTkFrame(
            card, fg_color=C["tree_bg"], corner_radius=0, height=_PHOTO_H,
        )
        photo_wrap.pack(fill="x", padx=0, pady=0)
        photo_wrap.pack_propagate(False)
        photo_lbl = ctk.CTkLabel(
            photo_wrap, text="—", font=FONT_SM, text_color=C["dim"],
        )
        photo_lbl.place(relx=0, rely=0, relwidth=1, relheight=1)
        if has_photo:
            thumb = self._reports_load_thumb(photo_path, (_W, _PHOTO_H))
            if thumb is not None:
                photo_lbl.configure(image=thumb, text="")

        # Reserve bottom chrome first so name/crime cannot crush buttons
        actions = ctk.CTkFrame(card, fg_color="transparent", height=28)
        actions.pack(fill="x", padx=2, pady=(1, 2), side="bottom")
        actions.pack_propagate(False)

        meta_row = ctk.CTkFrame(card, fg_color="transparent", height=14)
        meta_row.pack(fill="x", padx=3, pady=(0, 0), side="bottom")
        meta_row.pack_propagate(False)

        # Name: fixed single-line row (no wrap) — long names ellipsize
        display_name = self._reports_grid_display_name(
            first,
            middle,
            last,
            str(rec.get("full_name") or ""),
            max_len=20,
        )
        name_row = ctk.CTkFrame(card, fg_color="transparent", height=18)
        name_row.pack(fill="x", padx=3, pady=(1, 0))
        name_row.pack_propagate(False)
        ctk.CTkLabel(
            name_row,
            text=display_name,
            font=("Segoe UI", 11, "bold"),
            text_color=C["text"],
            anchor="w",
            height=16,
        ).pack(side="left", fill="x", expand=True)

        try:
            from gui_app.shared.deported import format_listed_banner

            listed_txt = format_listed_banner(race, rec)
        except Exception:
            listed_txt = f"LISTED  {str(race or '—').strip().upper() or '—'}"
        # Keep listed banner one line on narrow tiles
        if len(listed_txt) > 22:
            listed_txt = listed_txt[:21].rstrip() + "…"
        listed_row = ctk.CTkFrame(card, fg_color="transparent", height=24)
        listed_row.pack(fill="x", padx=2, pady=(2, 1))
        listed_row.pack_propagate(False)
        ctk.CTkLabel(
            listed_row,
            text=listed_txt,
            font=("Segoe UI", 11, "bold"),
            text_color="#ffffff",
            fg_color="#7a1f1f",
            corner_radius=4,
            height=22,
            anchor="center",
        ).pack(fill="both", expand=True)

        # Crime only (summarized, hard-capped) so bottom action row stays full height
        crime_short = self._reports_summarize_crime(crime, max_len=48)
        crime_row = ctk.CTkFrame(card, fg_color="transparent", height=32)
        crime_row.pack(fill="x", padx=2, pady=(1, 0))
        crime_row.pack_propagate(False)
        crime_lbl = ctk.CTkLabel(
            crime_row,
            text=crime_short or "—",
            font=("Segoe UI", 10),
            text_color=C["text"] if crime_short else C["dim"],
            anchor="nw",
            justify="left",
            wraplength=_W - 8,
            height=30,
        )
        crime_lbl.pack(fill="both", expand=True)
        export_badge = ""
        try:
            from gui_app.shared.export_card_release import (
                format_export_badge,
                peek_release_number,
            )

            export_badge = format_export_badge(peek_release_number(rec))
        except Exception:
            export_badge = ""
        left_meta = f"{conf_label} · {state}"
        if export_badge:
            left_meta = f"{export_badge} · {left_meta}"
        meta_lbl = ctk.CTkLabel(
            meta_row,
            text=left_meta,
            font=("Segoe UI", 9),
            text_color=C["accent"] if export_badge else C["muted"],
            anchor="w",
        )
        meta_lbl.pack(side="left")
        status_lbl = ctk.CTkLabel(
            meta_row,
            text=self._reports_verdict_label_short(verdict),
            font=("Segoe UI", 9),
            text_color=self._reports_verdict_color(verdict),
            anchor="e",
        )
        status_lbl.pack(side="right")

        # Bottom: [select] · ✗ · ✓ · Export · Skip · Open (online listing)
        # (actions frame already packed side=bottom above)
        sel_var = ctk.BooleanVar(
            value=bool(
                hasattr(self, "_reports_is_export_selected")
                and self._reports_is_export_selected(mc)
            )
        )
        ctk.CTkCheckBox(
            actions,
            text="",
            width=18,
            variable=sel_var,
            command=lambda m=mc, v=sel_var: self._reports_set_export_selected(
                m, bool(v.get())
            ),
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            border_color=C["border"],
            checkmark_color=C["bg"],
        ).pack(side="left", padx=(0, 2))

        def _set(v: str, m=mc, card_widget=card, status=status_lbl):
            self._set_verdict_for_mc(m, v, save=True)
            self._refresh_stats_from_verdicts()
            want = self._reports_verdict_filter_key()
            if not self._reports_verdict_passes_filter(v, want):
                self._reports_drop_card(card_widget, m)
                return
            b = {
                "confirmed": C["danger"],
                "correct": C["success"],
                "skip": C["dim"],
                "unreviewed": C["border"],
            }.get(v, C["border"])
            try:
                card_widget.configure(border_color=b)
            except Exception:
                pass
            try:
                status.configure(
                    text=self._reports_verdict_label_short(v),
                    text_color=self._reports_verdict_color(v),
                )
            except Exception:
                pass
            self._reports_update_metrics()

        ctk.CTkButton(
            actions, text="✗", width=28, height=24,
            command=lambda: _set("confirmed"),
            fg_color="#5c3030", hover_color="#7a4040", text_color=C["text"],
            font=("Segoe UI", 11),
        ).pack(side="left", padx=(0, 2))
        ctk.CTkButton(
            actions, text="✓", width=28, height=24,
            command=lambda: _set("correct"),
            fg_color="#2a4a38", hover_color="#356348", text_color=C["text"],
            font=("Segoe UI", 11),
        ).pack(side="left", padx=(0, 2))
        # Immediate single name-card export to Desktop
        export_btn = ctk.CTkButton(
            actions,
            text="Export",
            width=52,
            height=24,
            font=("Segoe UI", 9),
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color=C["bg"],
            command=lambda: None,
        )
        export_btn.configure(
            command=lambda m=mc, b=export_btn: self._reports_export_single_card(m, b)
        )
        export_btn.pack(side="left", padx=(0, 2))
        ctk.CTkButton(
            actions, text="Skip", width=36, height=24,
            command=lambda: _set("skip"),
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["muted"],
            border_width=1, border_color=C["border"], font=("Segoe UI", 9),
        ).pack(side="left", padx=(0, 2))
        ctk.CTkButton(
            actions, text="Open", width=40, height=24,
            command=lambda m=mc: self._reports_open_online_listing(m),
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"], font=("Segoe UI", 9),
        ).pack(side="left")

        def _bind_open(widget, m=mc):
            try:
                widget.bind(
                    "<Double-Button-1>",
                    lambda _e, hit=m: self._reports_open_record_links(hit),
                )
            except Exception:
                pass

        _bind_open(card)
        try:
            for child in card.winfo_children():
                _bind_open(child)
                for grand in child.winfo_children():
                    _bind_open(grand)
        except Exception:
            pass
        try:
            self._reports_register_card_ui(
                mc,
                meta_lbl=meta_lbl,
                conf_label=conf_label,
                state=state,
                card=card,
            )
        except Exception:
            pass
        return card


