"""Shared detail drawer (photo + fields)."""
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



class DetailDrawerMixin:
    def _make_detail_drawer(self, parent) -> ctk.CTkFrame:
        """Right-side detail card used by Search and (optionally) other tables."""
        card = _card(parent)
        _section_label(card, "Detail").pack(anchor="w", padx=12, pady=(12, 4))
        photo = ctk.CTkLabel(
            card,
            text="Select a row",
            font=FONT_SM,
            text_color=C["dim"],
            width=180,
            height=180,
            fg_color=C["tree_bg"],
            corner_radius=8,
        )
        photo.pack(padx=12, pady=(0, 6))
        # Stable host: empty label (no scrollbar) OR textbox when a row is selected
        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        empty = ctk.CTkLabel(
            content,
            text="Select a result to view photo, crime, race, and links.",
            font=FONT_SM,
            text_color=C["dim"],
            anchor="nw",
            justify="left",
            wraplength=220,
        )
        empty.pack(fill="x", anchor="nw")
        body = ctk.CTkTextbox(
            content,
            height=200,
            font=FONT_SM,
            fg_color=C["bg"],
            text_color=C["text"],
            border_color=C["border"],
            border_width=1,
            corner_radius=8,
            activate_scrollbars=True,
            wrap="word",
        )
        # Not packed until a row is selected (avoids empty scrollbar chrome)
        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(0, 12))
        open_html = ctk.CTkButton(
            btns, text="Open HTML", width=90, state="disabled",
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        )
        open_html.pack(side="left", padx=(0, 6))
        open_url = ctk.CTkButton(
            btns, text="Open URL", width=90, state="disabled",
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        )
        open_url.pack(side="left", padx=(0, 6))
        open_photo = ctk.CTkButton(
            btns, text="Open photo", width=90, state="disabled",
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        )
        open_photo.pack(side="left", padx=(0, 6))
        copy_btn = ctk.CTkButton(
            btns, text="Copy text", width=90, state="disabled",
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        )
        copy_btn.pack(side="left")
        self._make_textbox_selectable(body)
        card._detail_photo = photo  # type: ignore[attr-defined]
        card._detail_content = content  # type: ignore[attr-defined]
        card._detail_empty = empty  # type: ignore[attr-defined]
        card._detail_body = body  # type: ignore[attr-defined]
        card._detail_open_html = open_html  # type: ignore[attr-defined]
        card._detail_open_url = open_url  # type: ignore[attr-defined]
        card._detail_open_photo = open_photo  # type: ignore[attr-defined]
        card._detail_copy = copy_btn  # type: ignore[attr-defined]
        card._detail_image_ref = None  # type: ignore[attr-defined]
        card._detail_record = None  # type: ignore[attr-defined]
        card._detail_body_packed = False  # type: ignore[attr-defined]
        return card

    @staticmethod
    def _detail_set_body_visible(drawer: ctk.CTkFrame, show_body: bool) -> None:
        """Show textbox (with content) or empty label (no scrollbar)."""
        empty = getattr(drawer, "_detail_empty", None)
        body = getattr(drawer, "_detail_body", None)
        if empty is None or body is None:
            return
        packed = bool(getattr(drawer, "_detail_body_packed", False))
        if show_body and not packed:
            try:
                empty.pack_forget()
            except Exception:
                pass
            body.pack(fill="both", expand=True)
            drawer._detail_body_packed = True  # type: ignore[attr-defined]
        elif not show_body and packed:
            try:
                body.pack_forget()
            except Exception:
                pass
            empty.pack(fill="x", anchor="nw")
            drawer._detail_body_packed = False  # type: ignore[attr-defined]
        elif not show_body and not packed:
            try:
                empty.pack(fill="x", anchor="nw")
            except Exception:
                pass

    @staticmethod
    def _detail_hide_unneeded_scrollbars(body: ctk.CTkTextbox) -> None:
        """Force-hide CTkTextbox scrollbars when content fully fits."""
        try:
            body.update_idletasks()
            tb = getattr(body, "_textbox", None)
            if tb is None:
                return
            y0, y1 = tb.yview()
            x0, x1 = tb.xview()
            hide_y = (y1 - y0) >= 0.999 or (y0, y1) == (0.0, 1.0)
            hide_x = (x1 - x0) >= 0.999 or (x0, x1) == (0.0, 1.0)
            body._hide_y_scrollbar = hide_y  # type: ignore[attr-defined]
            body._hide_x_scrollbar = hide_x  # type: ignore[attr-defined]
            body._create_grid_for_text_and_scrollbars(  # type: ignore[attr-defined]
                re_grid_x_scrollbar=True, re_grid_y_scrollbar=True
            )
        except Exception:
            pass

    @staticmethod
    def _clear_label_image(photo_lbl, drawer: Optional[ctk.CTkFrame] = None) -> None:
        """Detach a CTk/Tk image from a label without leaving a dangling image name.

        CustomTkinter + Tk can raise ``TclError: image "pyimageN" doesn't exist``
        on a later configure() if the PhotoImage is GC'd while the label still
        references it. Clear the image *before* dropping the Python ref.
        """
        # Keep local ref so GC cannot race mid-clear
        old_ref = None
        if drawer is not None:
            old_ref = getattr(drawer, "_detail_image_ref", None)
        try:
            # Empty string is the reliable Tk way to clear -image
            photo_lbl.configure(image="")
        except Exception:
            try:
                inner = getattr(photo_lbl, "_label", None)
                if inner is not None:
                    inner.configure(image="")
            except Exception:
                pass
        if drawer is not None:
            try:
                drawer._detail_image_ref = None  # type: ignore[attr-defined]
            except Exception:
                pass
        # Drop after Tk no longer names it
        del old_ref

    def _make_textbox_selectable(self, body: ctk.CTkTextbox) -> None:
        """Allow select + copy (Ctrl+C / right-click) without editing content."""
        try:
            tb = getattr(body, "_textbox", None) or body
        except Exception:
            return

        def _block_edit(event):
            if event.state & 0x4:  # Control
                if event.keysym.lower() in ("c", "a", "insert"):
                    return None
            if event.keysym in (
                "Left", "Right", "Up", "Down", "Home", "End",
                "Prior", "Next", "Shift_L", "Shift_R", "Control_L", "Control_R",
            ):
                return None
            return "break"

        def _copy_sel(_event=None):
            try:
                if tb.tag_ranges("sel"):
                    text = tb.get("sel.first", "sel.last")
                else:
                    text = tb.get("1.0", "end-1c")
                if text:
                    self.clipboard_clear()
                    self.clipboard_append(text)
            except Exception:
                pass
            return "break"

        def _select_all(_event=None):
            try:
                tb.tag_add("sel", "1.0", "end-1c")
                tb.mark_set("insert", "1.0")
            except Exception:
                pass
            return "break"

        try:
            tb.bind("<Key>", _block_edit, add="+")
            tb.bind("<Control-c>", _copy_sel, add="+")
            tb.bind("<Control-C>", _copy_sel, add="+")
            tb.bind("<Control-a>", _select_all, add="+")
            tb.bind("<Control-A>", _select_all, add="+")
            # Right-click copies selection or full text
            tb.bind("<Button-3>", lambda _e: _copy_sel(), add="+")
        except Exception:
            pass

    def _copy_to_clipboard(self, text: str, *, toast: Optional[str] = None) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(text or "")
            if toast:
                if hasattr(self, "report_status"):
                    self.report_status.configure(text=toast)
                elif hasattr(self, "misclass_status"):
                    self.misclass_status.configure(text=toast)
        except Exception as e:
            messagebox.showerror("Copy", str(e))

    def _bind_global_copy_shortcuts(self) -> None:
        """Ctrl+C on treeviews copies selected row values as TSV."""
        def _tree_copy(event):
            w = event.widget
            try:
                if not isinstance(w, ttk.Treeview):
                    return
                sel = w.selection()
                if not sel:
                    return
                lines = []
                for iid in sel:
                    vals = w.item(iid, "values") or ()
                    lines.append("\t".join(str(v) for v in vals))
                if lines:
                    self._copy_to_clipboard("\n".join(lines))
                return "break"
            except Exception:
                return

        try:
            self.bind_all("<Control-c>", _tree_copy, add="+")
            self.bind_all("<Control-C>", _tree_copy, add="+")
        except Exception:
            pass

    def _fill_detail_drawer(self, drawer: ctk.CTkFrame, record: Optional[Dict[str, Any]]) -> None:
        """Populate a detail drawer from an offender record dict."""
        photo_lbl = drawer._detail_photo  # type: ignore[attr-defined]
        body = drawer._detail_body  # type: ignore[attr-defined]
        btn_html = drawer._detail_open_html  # type: ignore[attr-defined]
        btn_url = drawer._detail_open_url  # type: ignore[attr-defined]
        btn_photo = drawer._detail_open_photo  # type: ignore[attr-defined]
        btn_copy = getattr(drawer, "_detail_copy", None)
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
            from scraper.public_links import openable_url_for_record

            url = openable_url_for_record(record) or raw_url
        except Exception:
            url = raw_url

        def _open_html():
            if html_path and Path(html_path).exists():
                self._open_path(Path(html_path))

        def _open_url():
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

    # -----------------------------------------------------------------------
    # Scrape
    # -----------------------------------------------------------------------
