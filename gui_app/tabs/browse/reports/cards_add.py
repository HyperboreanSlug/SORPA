"""CAdd"""
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


class ReportsCardsAddMixin:
    def _reports_add_card(
        self, parent, mc, *, index: int, total: int, grid: bool = False
    ):
        """Compact list row or grid tile with mugshot + quick verdicts."""
        rec = dict(mc.record or {})
        verdict = self._verdict_for_mc(mc)
        first = (rec.get("first_name") or "").strip()
        middle = (rec.get("middle_name") or "").strip()
        last = (rec.get("last_name") or "").strip()
        # List: first + last; grid builds display name with middle initial
        name = (
            " ".join(p for p in (first, last) if p)
            or (rec.get("full_name") or "—")
        )
        state = _format_state_display(rec)
        race_raw = (mc.expected_race or rec.get("race") or "—")
        race = _format_race_display(race_raw) or str(race_raw)
        eth = mc.likely_ethnicity or "—"
        conf = float(mc.confidence or 0.0)
        photo_path = (rec.get("photo_path") or "").strip()
        has_photo = bool(photo_path and Path(photo_path).is_file())
        crime = self._reports_crime_text(rec)
        df = rec.get("_deepface") or {}
        border = {
            "confirmed": C["danger"],
            "correct": C["success"],
            "skip": C["dim"],
            "unreviewed": C["border"],
        }.get(verdict, C["border"])

        if grid:
            return self._reports_add_grid_tile(
                parent, mc, rec,
                first=first, middle=middle, last=last,
                state=state, race=race, conf=conf,
                crime=crime, df=df, photo_path=photo_path, has_photo=has_photo,
                verdict=verdict, border=border, index=index,
            )

        # ---- Compact list row (larger mugshot) ----
        card = ctk.CTkFrame(
            parent,
            fg_color=C["panel"],
            border_color=border,
            border_width=1,
            corner_radius=8,
            height=148,
        )
        card.pack(fill="x", padx=6, pady=3)
        card.pack_propagate(False)
        card.grid_columnconfigure(1, weight=1)

        # Mugshot
        photo_wrap = ctk.CTkFrame(
            card, fg_color=C["tree_bg"], corner_radius=6, width=112, height=136,
        )
        photo_wrap.grid(row=0, column=0, padx=(8, 10), pady=6, sticky="nw")
        photo_wrap.grid_propagate(False)
        photo_lbl = ctk.CTkLabel(
            photo_wrap, text="—", font=FONT_SM, text_color=C["dim"],
        )
        photo_lbl.place(relx=0.5, rely=0.5, anchor="center")
        if has_photo:
            thumb = self._reports_load_thumb(photo_path, (110, 134))
            if thumb is not None:
                photo_lbl.configure(image=thumb, text="")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=0, column=1, sticky="nsew", padx=(0, 8), pady=6)

        line1 = ctk.CTkFrame(body, fg_color="transparent")
        line1.pack(fill="x")
        sel_var = ctk.BooleanVar(
            value=bool(
                hasattr(self, "_reports_is_export_selected")
                and self._reports_is_export_selected(mc)
            )
        )
        ctk.CTkCheckBox(
            line1,
            text="",
            width=22,
            variable=sel_var,
            command=lambda m=mc, v=sel_var: self._reports_set_export_selected(
                m, bool(v.get())
            ),
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            border_color=C["border"],
            checkmark_color=C["bg"],
        ).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(
            line1, text=name, font=FONT_BOLD, text_color=C["text"], anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            line1, text=f"  #{index}", font=FONT_SM, text_color=C["dim"],
        ).pack(side="left")
        status_lbl = ctk.CTkLabel(
            line1,
            text=self._reports_verdict_label_short(verdict),
            font=FONT_SM,
            text_color=self._reports_verdict_color(verdict),
        )
        status_lbl.pack(side="right")

        # LISTED AS + eth + conf + state (one line)
        listed = f"LISTED {str(race).upper()}"
        face_bit = ""
        if df:
            flab = df.get("predicted_label") or df.get("top_label") or ""
            fconf = df.get("top_confidence")
            try:
                face_bit = f"  ·  face {flab}@{float(fconf):.0%}" if flab else ""
            except (TypeError, ValueError):
                face_bit = f"  ·  face {flab}" if flab else ""
        ctk.CTkLabel(
            body,
            text=f"{listed}  ·  vs {eth}  ·  {conf:.2f}  ·  {state}{face_bit}",
            font=FONT_SM,
            text_color=C["muted"],
            anchor="w",
        ).pack(fill="x")

        # Reports: summarized crime only (full text lives in Misclassify detail)
        crime_sum = self._reports_summarize_crime(crime, max_len=120)
        ctk.CTkLabel(
            body,
            text=f"Crime: {crime_sum}" if crime_sum else "Crime: —",
            font=FONT_SM,
            text_color=C["text"] if crime_sum else C["dim"],
            anchor="w",
            justify="left",
            wraplength=520,
        ).pack(fill="x")

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.pack(fill="x", pady=(4, 0))

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
                card_widget.configure(border_color=b, border_width=1)
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
            actions, text="Incorrect", width=78, height=26,
            command=lambda: _set("confirmed"),
            fg_color="#5c3030", hover_color="#7a4040", text_color=C["text"],
            font=FONT_SM,
        ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(
            actions, text="Correct", width=70, height=26,
            command=lambda: _set("correct"),
            fg_color="#2a4a38", hover_color="#356348", text_color=C["text"],
            font=FONT_SM,
        ).pack(side="left", padx=(0, 4))
        # Immediate single name-card export to Desktop
        list_export_btn = ctk.CTkButton(
            actions,
            text="Export",
            width=64,
            height=26,
            font=FONT_SM,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color=C["bg"],
            command=lambda: None,
        )
        list_export_btn.configure(
            command=lambda m=mc, b=list_export_btn: self._reports_export_single_card(
                m, b
            )
        )
        list_export_btn.pack(side="left", padx=(0, 4))
        ctk.CTkButton(
            actions, text="Skip", width=50, height=26,
            command=lambda: _set("skip"),
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["muted"],
            border_width=1, border_color=C["border"], font=FONT_SM,
        ).pack(side="left", padx=(0, 4))

        # Compact ethnicity override
        eth_opts = list(self._ETHNICITY_OPTIONS)
        eth_cur = str(eth or "Unknown").strip() or "Unknown"
        if eth_cur not in eth_opts:
            eth_opts = [eth_cur] + eth_opts
        eth_var = ctk.StringVar(value=eth_cur)
        eth_combo = ctk.CTkComboBox(
            actions,
            variable=eth_var,
            values=eth_opts,
            width=130,
            height=26,
            fg_color=C["bg"],
            border_color=C["border"],
            button_color=C["elevated"],
            text_color=C["text"],
            dropdown_fg_color=C["panel"],
            state="readonly",
            font=FONT_SM,
        )
        eth_combo.pack(side="left", padx=(8, 0))

        def _on_eth(choice: str, m=mc, card_widget=card):
            new_eth = (choice or eth_var.get() or "").strip() or "Unknown"
            self._set_ethnicity_for_mc(m, new_eth)
            if self._ethnicity_compatible_with_record(m):
                self._refresh_stats_from_verdicts()
                self._reports_drop_card(card_widget, m)
                return
            self._refresh_stats_from_verdicts()
            self._reports_update_metrics()

        eth_combo.configure(command=_on_eth)

        crime_sum_copy = self._reports_summarize_crime(crime, max_len=200)
        crime_line = f"\nCrime: {crime_sum_copy}" if crime_sum_copy else ""
        html_raw = (rec.get("report_html_path") or "").strip()
        url_raw = (rec.get("source_url") or "").strip()
        try:
            from scraper.public_links import openable_url_for_record

            url_disp = openable_url_for_record(rec) or url_raw or "—"
        except Exception:
            url_disp = url_raw or "—"
        copy_blob = (
            f"{name}\nLISTED AS: {race}\nSurname ethnicity: {eth}"
            f"{crime_line}\nConf {conf:.3f} · {state}\n"
            f"HTML: {html_raw or '—'}\n"
            f"URL: {url_disp}"
        )
        ctk.CTkButton(
            actions, text="Copy", width=50, height=26,
            command=lambda t=copy_blob, n=name: self._copy_to_clipboard(
                t, toast=f"Copied {n}"
            ),
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"], font=FONT_SM,
        ).pack(side="right", padx=(0, 4))
        # Double-click card → archived HTML, else live URL, else photo
        try:
            card.bind(
                "<Double-Button-1>",
                lambda _e, m=mc: self._reports_open_record_links(m),
            )
            for child in card.winfo_children():
                child.bind(
                    "<Double-Button-1>",
                    lambda _e, m=mc: self._reports_open_record_links(m),
                )
        except Exception:
            pass
        return card


