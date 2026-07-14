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
    def _reports_grid_display_name(
        first: str, middle: str, last: str, full_name: str = "", *, max_len: int = 28
    ) -> str:
        """Full legal name for grid: First M. Last; shorten middle to fit."""
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
        """Short human summary for Reports cards/HTML (never the full statute dump)."""
        try:
            from scraper.crime_summary import summarize_crime

            return summarize_crime(crime, max_len=max_len)
        except Exception:
            s = " ".join((crime or "").split())
            if len(s) <= max_len:
                return s
            cut = s[: max_len - 1]
            if " " in cut:
                cut = cut.rsplit(" ", 1)[0]
            return cut.rstrip(" ,;:") + "…"


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
    ):
        """Grid tile: max photo, min chrome; 2 rows still fit on 1080p (~332px)."""
        # Fixed height: room for photo + chrome + export strip
        # Non-photo chrome ~150px → photo ~200px
        _W, _H = 180, 352
        _PHOTO_H = 200
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

        # Photo first — almost full width, minimal padding
        photo_wrap = ctk.CTkFrame(
            card, fg_color=C["tree_bg"], corner_radius=0, height=_PHOTO_H,
        )
        photo_wrap.pack(fill="x", padx=1, pady=(1, 0))
        photo_wrap.pack_propagate(False)
        photo_lbl = ctk.CTkLabel(
            photo_wrap, text="—", font=FONT_SM, text_color=C["dim"],
        )
        photo_lbl.place(relx=0.5, rely=0.5, anchor="center")
        if has_photo:
            thumb = self._reports_load_thumb(photo_path, (_W - 4, _PHOTO_H - 2))
            if thumb is not None:
                photo_lbl.configure(image=thumb, text="")

        # Text chrome packed tight under photo
        display_name = self._reports_grid_display_name(
            first,
            middle,
            last,
            str(rec.get("full_name") or ""),
            max_len=28,
        )
        ctk.CTkLabel(
            card,
            text=display_name,
            font=("Segoe UI", 11, "bold"),
            text_color=C["text"],
            anchor="w",
            justify="left",
            wraplength=_W - 10,
            height=16,
        ).pack(fill="x", padx=3, pady=(1, 0))

        # Single solid label — nested Frame+place often draws blank/clipped in CTk
        race_u = str(race or "—").strip().upper() or "—"
        ctk.CTkLabel(
            card,
            text=f"LISTED  {race_u}",
            font=("Segoe UI", 12, "bold"),
            text_color="#ffffff",
            fg_color="#7a1f1f",
            corner_radius=4,
            height=28,
            anchor="center",
        ).pack(fill="x", padx=2, pady=(2, 1))

        crime_short = self._reports_summarize_crime(crime, max_len=78)
        crime_line = f"Crime: {crime_short}" if crime_short else "Crime: —"
        ctk.CTkLabel(
            card,
            text=crime_line,
            font=("Segoe UI", 10),
            text_color=C["text"] if crime_short else C["dim"],
            anchor="nw",
            justify="left",
            wraplength=_W - 10,
            height=28,
        ).pack(fill="x", padx=3, pady=(1, 0))

        face_bit = ""
        if df:
            flab = df.get("predicted_label") or df.get("top_label") or ""
            fconf = df.get("top_confidence")
            if flab:
                try:
                    face_bit = f" · {flab}@{float(fconf):.0%}"
                except (TypeError, ValueError):
                    face_bit = f" · {flab}"
        meta_row = ctk.CTkFrame(card, fg_color="transparent", height=14)
        meta_row.pack(fill="x", padx=3, pady=(0, 0))
        meta_row.pack_propagate(False)
        ctk.CTkLabel(
            meta_row,
            text=f"{conf:.2f} · {state}{face_bit}",
            font=("Segoe UI", 9),
            text_color=C["muted"],
            anchor="w",
        ).pack(side="left")
        status_lbl = ctk.CTkLabel(
            meta_row,
            text=self._reports_verdict_label_short(verdict),
            font=("Segoe UI", 9),
            text_color=self._reports_verdict_color(verdict),
            anchor="e",
        )
        status_lbl.pack(side="right")

        # Bottom action strip: verdict + export (fixed height for grid tiles)
        actions = ctk.CTkFrame(card, fg_color="transparent", height=44)
        actions.pack(fill="x", padx=2, pady=(1, 2), side="bottom")
        actions.pack_propagate(False)

        row1 = ctk.CTkFrame(actions, fg_color="transparent", height=22)
        row1.pack(fill="x")
        row1.pack_propagate(False)

        sel_var = ctk.BooleanVar(
            value=bool(
                hasattr(self, "_reports_is_export_selected")
                and self._reports_is_export_selected(mc)
            )
        )
        ctk.CTkCheckBox(
            row1,
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
            row1, text="✗", width=30, height=20,
            command=lambda: _set("confirmed"),
            fg_color="#5c3030", hover_color="#7a4040", text_color=C["text"],
            font=("Segoe UI", 11),
        ).pack(side="left", padx=(0, 2))
        ctk.CTkButton(
            row1, text="✓", width=30, height=20,
            command=lambda: _set("correct"),
            fg_color="#2a4a38", hover_color="#356348", text_color=C["text"],
            font=("Segoe UI", 11),
        ).pack(side="left", padx=(0, 2))
        ctk.CTkButton(
            row1, text="Skip", width=36, height=20,
            command=lambda: _set("skip"),
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["muted"],
            border_width=1, border_color=C["border"], font=("Segoe UI", 9),
        ).pack(side="left")

        # Single-card export pinned to bottom of cell
        export_btn = ctk.CTkButton(
            actions,
            text="Export",
            height=18,
            font=("Segoe UI", 10),
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color=C["bg"],
            command=lambda m=mc: None,  # set after btn created
        )
        export_btn.configure(
            command=lambda m=mc, b=export_btn: self._reports_export_single_card(m, b)
        )
        export_btn.pack(fill="x", padx=1, pady=(2, 0))

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
        return card


