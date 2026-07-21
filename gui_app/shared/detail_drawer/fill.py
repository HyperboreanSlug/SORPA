"""DetailFillMixin."""
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


class DetailFillMixin:
    def _fill_detail_drawer(self, drawer: ctk.CTkFrame, record: Optional[Dict[str, Any]]) -> None:
        """Populate a detail drawer from an offender record dict."""
        photo_lbl = drawer._detail_photo  # type: ignore[attr-defined]
        body = drawer._detail_body  # type: ignore[attr-defined]
        btn_html = drawer._detail_open_html  # type: ignore[attr-defined]
        btn_url = drawer._detail_open_url  # type: ignore[attr-defined]
        btn_photo = drawer._detail_open_photo  # type: ignore[attr-defined]
        btn_copy = getattr(drawer, "_detail_copy", None)
        btn_export = getattr(drawer, "_detail_export", None)
        drawer._detail_record = record  # type: ignore[attr-defined]

        def _clear_photo(placeholder: str = "No photo") -> None:
            self._clear_label_image(photo_lbl, drawer)
            try:
                photo_lbl.configure(text=placeholder)
            except Exception:
                pass

        if not record:
            _clear_photo("Select a row")
            self._detail_set_body_visible(drawer, False)
            empty = getattr(drawer, "_detail_empty", None)
            if empty is not None:
                try:
                    empty.configure(text="Select a result to view details.")
                except Exception:
                    pass
            try:
                body.configure(state="normal")
                body.delete("1.0", "end")
            except Exception:
                pass
            try:
                btn_html.configure(state="disabled", command=None)
                btn_url.configure(state="disabled", command=None)
                btn_photo.configure(state="disabled", command=None)
                if btn_copy is not None:
                    btn_copy.configure(state="disabled", command=None)
                if btn_export is not None:
                    btn_export.configure(state="disabled")
            except Exception:
                pass
            return

        mid = (record.get("middle_name") or "").strip()
        name = (
            " ".join(
                p for p in (
                    record.get("first_name") or "",
                    mid,
                    record.get("last_name") or "",
                ) if str(p).strip()
            ).strip()
            or (record.get("full_name") or "").strip()
            or "—"
        )
        if name and name != "—":
            name = name.upper()
        crime = (
            record.get("crime")
            or record.get("offense_description")
            or record.get("offense_type")
            or "—"
        )
        race_line = _format_race_display(record.get("race"))
        try:
            from scraper.database.sources import (
                format_sources_detail,
                multi_source_display,
                parse_sources,
            )

            srcs = parse_sources(record.get("sources_json"))
            if srcs:
                multi_race = multi_source_display(srcs, "race")
                if multi_race:
                    race_line = multi_race
        except Exception:
            srcs = []

        lines = [
            f"Name: {name}",
            f"Middle: {mid or '—'}",
            f"Race: {race_line}",
            f"Ethnicity: {record.get('ethnicity') or '—'}",
            f"Gender: {record.get('gender') or '—'}",
            f"Age / DOB: {record.get('age') or '—'} / {record.get('date_of_birth') or '—'}",
            f"State: {_format_state_display(record)}",
            f"County / City: {record.get('county') or '—'} / {record.get('city') or '—'}",
            f"Address: {record.get('address') or '—'}",
            f"Crime: {crime}",
            f"Risk: {record.get('risk_level') or '—'}",
            f"Likely ethnicity (name): {record.get('likely_ethnicity') or '—'}",
            f"Photo: {record.get('photo_path') or record.get('photo_url') or '—'}",
            f"HTML: {record.get('report_html_path') or '—'}",
            f"URL: {record.get('source_url') or '—'}",
        ]
        try:
            from scraper.database.sources import format_sources_detail, parse_sources

            lines.extend(format_sources_detail(parse_sources(record.get("sources_json"))))
        except Exception:
            pass
        detail_text = "\n".join(lines)
        self._detail_set_body_visible(drawer, True)
        # Keep normal (not disabled) so text can be selected and copied
        body.configure(state="normal")
        body.delete("1.0", "end")
        body.insert("1.0", detail_text)
        self.after(30, lambda b=body: self._detail_hide_unneeded_scrollbars(b))
        if btn_copy is not None:
            btn_copy.configure(
                state="normal",
                command=lambda t=detail_text: self._copy_to_clipboard(
                    t, toast="Detail text copied"
                ),
            )

        photo_path = (record.get("photo_path") or "").strip()
        if photo_path and Path(photo_path).is_file():
            try:
                from PIL import Image

                # Clear previous image before assigning a new one
                self._clear_label_image(photo_lbl, drawer)
                img = Image.open(photo_path)
                img.thumbnail((200, 240))
                ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
                drawer._detail_image_ref = ctk_img  # type: ignore[attr-defined]
                photo_lbl.configure(image=ctk_img, text="")
            except Exception:
                _clear_photo("Photo error")
        else:
            _clear_photo()

        html_path = (record.get("report_html_path") or "").strip()
        raw_url = (record.get("source_url") or "").strip()
        try:
            from scraper.public_links import multi_state_urls, openable_url_for_record

            _ms_entries = multi_state_urls(record)
            url = openable_url_for_record(record) or raw_url
        except Exception:
            _ms_entries = []
            url = raw_url

        def _open_html():
            if html_path and Path(html_path).exists():
                self._open_path(Path(html_path))

        def _open_url():
            if len(_ms_entries) > 1:
                menu = tk.Menu(drawer, tearoff=0)
                for state_label, u in _ms_entries:
                    menu.add_command(
                        label=f"{state_label} registry",
                        command=lambda _u=u: webbrowser.open(_u),
                    )
                try:
                    x = btn_url.winfo_rootx()
                    y = btn_url.winfo_rooty() + btn_url.winfo_height()
                    menu.tk_popup(x, y)
                finally:
                    menu.grab_release()
                return
            target = url
            if not target:
                return
            try:
                webbrowser.open(target)
            except Exception as e:
                messagebox.showerror("Open URL", str(e))

        def _open_photo():
            if photo_path and Path(photo_path).is_file():
                self._open_path(Path(photo_path))

        btn_html.configure(
            state="normal" if html_path and Path(html_path).exists() else "disabled",
            command=_open_html,
        )
        btn_url.configure(state="normal" if url else "disabled", command=_open_url)
        btn_photo.configure(
            state="normal" if photo_path and Path(photo_path).is_file() else "disabled",
            command=_open_photo,
        )
        if btn_export is not None:
            try:
                btn_export.configure(state="normal", text="Export card")
            except Exception:
                pass


