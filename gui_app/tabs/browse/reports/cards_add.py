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
from gui_app.widgets_flow import FlowRow


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
        # List: first + last (UPPERCASE); grid builds display name with middle initial
        name = (
            " ".join(p for p in (first, last) if p)
            or (rec.get("full_name") or "—")
        )
        if name and name != "—":
            name = str(name).upper()
        state = _format_state_display(rec)
        race_raw = (mc.expected_race or rec.get("race") or "—")
        race = _format_race_display(race_raw) or str(race_raw)
        eth = mc.likely_ethnicity or "—"
        try:
            from scraper.confidence_display import (
                combine_name_face_confidence,
                format_display_confidence,
            )

            name_c = float(
                rec.get("_misclass_name_conf")
                if rec.get("_misclass_name_conf") is not None
                else mc.confidence
                or 0.0
            )
            df_pre = rec.get("_deepface") if isinstance(rec.get("_deepface"), dict) else None
            conf, conf_combined = combine_name_face_confidence(
                name_c,
                name_ethnicity=str(mc.likely_ethnicity or ""),
                deepface=df_pre,
            )
            conf_text = format_display_confidence(conf, conf_combined, digits=2)
        except Exception:
            conf = float(mc.confidence or 0.0)
            conf_combined = False
            conf_text = f"{conf:.2f}"
        photo_path = (rec.get("photo_path") or "").strip()
        try:
            if hasattr(self, "_reports_photo_exists"):
                has_photo = bool(self._reports_photo_exists(photo_path))
            else:
                from gui_app.shared.export_card_photo import is_usable_mugshot_path

                has_photo = bool(is_usable_mugshot_path(photo_path))
        except Exception:
            has_photo = bool(photo_path and Path(photo_path).is_file())
        crime = self._reports_crime_text(rec)
        df = rec.get("_deepface") or {}
        v_key = (
            "confirmed"
            if str(verdict or "").lower() in ("confirmed", "incorrect")
            else str(verdict or "unreviewed")
        )
        border = {
            "confirmed": C["danger"],
            "correct": C["success"],
            "skip": C["dim"],
            "unreviewed": C["border"],
        }.get(v_key, C["border"])

        if grid:
            return self._reports_add_grid_tile(
                parent, mc, rec,
                first=first, middle=middle, last=last,
                state=state, race=race, conf=conf,
                conf_text=conf_text,
                crime=crime, df=df, photo_path=photo_path, has_photo=has_photo,
                verdict=verdict, border=border, index=index,
            )

        # ---- Compact list row (larger mugshot) ----
        # Height follows content so long crime lines never crush the action buttons.
        card = ctk.CTkFrame(
            parent,
            fg_color=C["panel"],
            border_color=border,
            border_width=1,
            corner_radius=8,
        )
        card.pack(fill="x", padx=6, pady=3)
        card.grid_columnconfigure(1, weight=1)
        card.grid_rowconfigure(0, weight=1)

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

        # Name row: fixed single-line height so long names never crush buttons
        line1 = ctk.CTkFrame(body, fg_color="transparent", height=26)
        line1.pack(fill="x")
        line1.pack_propagate(False)
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
        # Right chrome first so name cannot push status/badge off-row
        status_lbl = ctk.CTkLabel(
            line1,
            text=self._reports_verdict_label_short(verdict),
            font=FONT_SM,
            text_color=self._reports_verdict_color(verdict),
        )
        status_lbl.pack(side="right", padx=(4, 0))
        export_badge = ""
        try:
            from gui_app.shared.export_card_release import (
                format_export_badge,
                peek_release_number,
            )

            export_badge = format_export_badge(peek_release_number(rec))
        except Exception:
            export_badge = ""
        # Always create badge label so export can update in place (no page rebuild)
        export_badge_lbl = ctk.CTkLabel(
            line1,
            text=f"  {export_badge}" if export_badge else "",
            font=FONT_SM,
            text_color=C["accent"] if export_badge else C["dim"],
        )
        export_badge_lbl.pack(side="right")
        ctk.CTkLabel(
            line1, text=f"#{index}", font=FONT_SM, text_color=C["dim"],
        ).pack(side="right", padx=(0, 4))
        # Expanding name host clips overflow; single-line ellipsis text
        name_host = ctk.CTkFrame(line1, fg_color="transparent")
        name_host.pack(side="left", fill="both", expand=True)
        name_host.pack_propagate(False)
        list_name = self._reports_list_display_name(name, max_len=48)
        name_lbl = ctk.CTkLabel(
            name_host,
            text=list_name,
            font=FONT_BOLD,
            text_color=C["text"],
            anchor="w",
            height=22,
        )
        name_lbl.pack(side="left", fill="x", expand=True, padx=(0, 4))

        # Listed race banner (+ DEPORTED in block letters when registry says so)
        try:
            from gui_app.shared.deported import format_listed_banner

            listed_txt = format_listed_banner(race, rec)
        except Exception:
            listed_txt = f"LISTED {str(race).upper()}"
        listed_row = ctk.CTkFrame(body, fg_color="transparent", height=24)
        listed_row.pack(fill="x", pady=(2, 2))
        listed_row.pack_propagate(False)
        ctk.CTkLabel(
            listed_row,
            text=listed_txt,
            font=FONT_BOLD,
            text_color="#ffffff",
            fg_color="#7a1f1f",
            corner_radius=4,
            anchor="center",
            height=22,
        ).pack(fill="both", expand=True)

        # Dead / 404 registry URL (e.g. Jacinto Calderon FDLE flyer)
        try:
            from scraper.online_listing import online_status_label

            offline_txt = online_status_label(rec)
        except Exception:
            offline_txt = ""
        if offline_txt:
            off_row = ctk.CTkFrame(body, fg_color="transparent", height=22)
            off_row.pack(fill="x", pady=(0, 2))
            off_row.pack_propagate(False)
            ctk.CTkLabel(
                off_row,
                text=offline_txt,
                font=FONT_SM,
                text_color="#f0d080",
                fg_color="#3a3420",
                corner_radius=4,
                anchor="center",
                height=20,
            ).pack(fill="both", expand=True)

        # Crime: hard-cap length + 2-line wrap so action buttons never get crushed
        crime_sum = self._reports_summarize_crime(crime, max_len=110)
        crime_lbl = ctk.CTkLabel(
            body,
            text=crime_sum or "",
            font=FONT_SM,
            text_color=C["text"] if crime_sum else C["dim"],
            anchor="nw",
            justify="left",
            wraplength=480,
            height=36,
        )
        crime_lbl.pack(fill="x", pady=(0, 2))

        def _fit_crime_wrap(_event=None, lbl=crime_lbl, host=body):
            try:
                w = max(int(host.winfo_width()) - 8, 200)
                # Keep ~2 lines; label height stays fixed so chrome below is stable
                lbl.configure(wraplength=w, height=36)
            except Exception:
                pass

        body.bind("<Configure>", _fit_crime_wrap, add="+")
        try:
            body.after_idle(_fit_crime_wrap)
        except Exception:
            pass

        # Confidence · state (combined when DeepFace present)
        ctk.CTkLabel(
            body,
            text=f"{conf_text} · {state}",
            font=FONT_SM,
            text_color=C["muted"],
            anchor="w",
        ).pack(fill="x")

        # Flowing action bar — wraps fully visible on narrow widths
        flow = FlowRow(body, padx=3, pady=3)
        actions = flow.host

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

        # Widths sized for full label text (never shrink past readable full words)
        flow.add(
            ctk.CTkButton(
                actions, text="Incorrect", width=88, height=28,
                command=lambda: _set("confirmed"),
                fg_color="#5c3030", hover_color="#7a4040", text_color=C["text"],
                font=FONT_SM,
            )
        )
        flow.add(
            ctk.CTkButton(
                actions, text="Correct", width=78, height=28,
                command=lambda: _set("correct"),
                fg_color="#2a4a38", hover_color="#356348", text_color=C["text"],
                font=FONT_SM,
            )
        )
        list_export_btn = ctk.CTkButton(
            actions,
            text="Export",
            width=72,
            height=28,
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
        flow.add(list_export_btn)
        flow.add(
            ctk.CTkButton(
                actions, text="Skip", width=56, height=28,
                command=lambda: _set("skip"),
                fg_color=C["elevated"], hover_color=C["border"], text_color=C["muted"],
                border_width=1, border_color=C["border"], font=FONT_SM,
            )
        )
        open_lbl = "Offline" if offline_txt else "Open"
        flow.add(
            ctk.CTkButton(
                actions, text=open_lbl, width=64 if offline_txt else 58, height=28,
                command=lambda m=mc: self._reports_open_online_listing(m),
                fg_color=C["elevated"], hover_color=C["border"],
                text_color="#f0d080" if offline_txt else C["text"],
                border_width=1, border_color=C["border"], font=FONT_SM,
            )
        )

        # Compact ethnicity override — size for longest ethnicity label
        eth_opts = list(self._ETHNICITY_OPTIONS)
        eth_cur = str(eth or "Unknown").strip() or "Unknown"
        if eth_cur not in eth_opts:
            eth_opts = [eth_cur] + eth_opts
        eth_var = ctk.StringVar(value=eth_cur)
        eth_combo = ctk.CTkComboBox(
            actions,
            variable=eth_var,
            values=eth_opts,
            width=148,
            height=28,
            fg_color=C["bg"],
            border_color=C["border"],
            button_color=C["elevated"],
            text_color=C["text"],
            dropdown_fg_color=C["panel"],
            state="readonly",
            font=FONT_SM,
        )
        flow.add(eth_combo)

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
        try:
            from gui_app.shared.deported import format_listed_banner

            listed_copy = format_listed_banner(race, rec)
        except Exception:
            listed_copy = f"LISTED AS: {race}"
        offline_copy = f"\n{offline_txt}" if offline_txt else ""
        copy_blob = (
            f"{name}\n{listed_copy}{offline_copy}\nSurname ethnicity: {eth}"
            f"{crime_line}\nConf {conf_text} · {state}\n"
            f"HTML: {html_raw or '—'}\n"
            f"URL: {url_disp if not offline_txt else '(not available online)'}"
        )
        flow.add(
            ctk.CTkButton(
                actions, text="Copy", width=56, height=28,
                command=lambda t=copy_blob, n=name: self._copy_to_clipboard(
                    t, toast=f"Copied {n}"
                ),
                fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
                border_width=1, border_color=C["border"], font=FONT_SM,
            )
        )
        try:
            flow.reflow()
        except Exception:
            pass
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
        try:
            self._reports_register_card_ui(
                mc,
                export_badge_lbl=export_badge_lbl,
                status_lbl=status_lbl,
                card=card,
            )
        except Exception:
            pass
        return card


