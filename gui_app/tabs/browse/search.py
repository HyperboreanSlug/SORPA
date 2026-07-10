"""Browse → Search sub-tab."""
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



class SearchTabMixin:
    def _build_search(self, tab):
        tab.configure(fg_color=C["surface"])
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))

        self.search_name_var = ctk.StringVar()
        ctk.CTkEntry(
            bar, textvariable=self.search_name_var, placeholder_text="Name…",
            width=200, fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left", padx=(0, 8))

        self.search_state_var = ctk.StringVar(value="")
        _US_STATES = [
            "", "ALL",
            "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL", "GA",
            "HI", "IA", "ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME",
            "MI", "MN", "MO", "MS", "MT", "NC", "ND", "NE", "NH", "NJ", "NM",
            "NV", "NY", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX",
            "UT", "VA", "VT", "WA", "WI", "WV", "WY",
        ]
        ctk.CTkComboBox(
            bar, variable=self.search_state_var, width=90,
            values=_US_STATES,
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            button_hover_color=C["border"], dropdown_fg_color=C["panel"],
            dropdown_hover_color=C["elevated"], text_color=C["text"],
        ).pack(side="left", padx=4)

        self.search_race_var = ctk.StringVar(value="")
        ctk.CTkComboBox(
            bar, variable=self.search_race_var, width=120,
            values=[
                "", "WHITE", "BLACK", "HISPANIC", "ASIAN", "INDIAN",
                "NATIVE AMERICAN", "OTHER",
            ],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            button_hover_color=C["border"], dropdown_fg_color=C["panel"],
            text_color=C["text"],
        ).pack(side="left", padx=4)

        # Surname-ethnicity lists (name-based; includes indian + high-confidence)
        self.search_ethnicity_var = ctk.StringVar(value="")
        ctk.CTkComboBox(
            bar, variable=self.search_ethnicity_var, width=170,
            values=[
                "",
                "indian",
                "indian_high_confidence",
                "hispanic",
                "asian",
                "african_american",
                "arabic",
                "jewish",
                "portuguese",
                "native_american",
            ],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            button_hover_color=C["border"], dropdown_fg_color=C["panel"],
            text_color=C["text"],
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            bar, text="Search", width=100, command=lambda: self._do_search(),
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            bar, text="Show all", width=100,
            command=self._search_show_all,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left")

        mid = _hpaned(tab)
        mid.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 4))
        left = ctk.CTkFrame(mid, fg_color="transparent")
        mid.add(left, minsize=360, stretch="always")
        self.search_detail = self._make_detail_drawer(mid)
        mid.add(self.search_detail, minsize=220, stretch="never")
        self.after(160, lambda: self._set_sash(mid, 0, 0.72))

        wrap, self.search_tree = _tree_frame(left)
        wrap.pack(fill="both", expand=True)
        cols = ["name", "race", "state", "county", "age", "crime", "address"]
        self.search_tree.configure(columns=cols, show="headings")
        _stretch_columns(self.search_tree, cols, [140, 90, 50, 90, 45, 180, 160])
        _enable_tree_column_sort(
            self.search_tree, cols, labels={c: c.upper() for c in cols}
        )
        _bind_tree_scroll_isolation(self.search_tree, wrap)
        self.search_tree.bind("<<TreeviewSelect>>", self._search_on_select)
        self._search_records_by_iid: Dict[str, Dict[str, Any]] = {}

        self.search_status = ctk.CTkLabel(
            tab,
            text="Loading names…",
            font=FONT_SM, text_color=C["muted"],
        )
        self.search_status.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 10))
        # Default view: list of names (not race distribution stats)
        self.after(100, self._search_show_all)

    def _search_show_all(self) -> None:
        """Clear filters in the UI, then list all names."""
        try:
            self.search_name_var.set("")
            self.search_state_var.set("")
            self.search_race_var.set("")
            if hasattr(self, "search_ethnicity_var"):
                self.search_ethnicity_var.set("")
        except Exception:
            pass
        self._do_search(name="", state="", race="", ethnicity="")

    def _do_search(
        self, name=None, state=None, race=None, ethnicity=None, *_args, **_kwargs
    ):
        from scraper.searcher import SexOffenderSearcher

        # Always re-read widgets unless explicit override (avoids stale second-run
        # blanks from leftover kwargs / partial clear).
        try:
            name_ui = (self.search_name_var.get() or "").strip()
            state_ui = (self.search_state_var.get() or "").strip().upper()
            race_ui = (self.search_race_var.get() or "").strip()
            eth_ui = (
                (self.search_ethnicity_var.get() or "").strip()
                if hasattr(self, "search_ethnicity_var")
                else ""
            )
        except Exception:
            name_ui, state_ui, race_ui, eth_ui = "", "", "", ""

        name = name_ui if name is None else (name or "").strip()
        state = state_ui if state is None else (state or "").strip().upper()
        race = race_ui if race is None else (race or "").strip()
        eth = eth_ui if ethnicity is None else (ethnicity or "").strip()
        # Treat blank / ALL as no filter
        state_f = state if state and state != "ALL" else None
        race_f = race or None
        eth_f = eth or None

        searcher = SexOffenderSearcher(db_path=self.db_path)
        try:
            try:
                if name:
                    results = searcher.search_by_name(
                        name=name,
                        state=state_f,
                        race=race_f if race_f and race_f.upper() != "INDIAN" else None,
                        limit=500,
                    )
                    records = list(results.records)
                    # Optional post-filters for Indian race + surname ethnicity
                    if race_f and race_f.upper() == "INDIAN":
                        records = [
                            r for r in records
                            if "indian" in (r.get("race") or "").lower()
                            or "indian" in (r.get("ethnicity") or "").lower()
                            or "indian" in (r.get("likely_ethnicity") or "").lower()
                            or "south asian" in (r.get("race") or "").lower()
                        ]
                    if eth_f:
                        eth_res = searcher.search_by_surname_ethnicity(
                            eth_f, state=state_f, limit=5000
                        )
                        allowed = {
                            (
                                (r.get("last_name") or "").strip().lower(),
                                (r.get("full_name") or "").strip().lower(),
                            )
                            for r in eth_res.records
                        }
                        records = [
                            r for r in records
                            if (
                                (r.get("last_name") or "").strip().lower(),
                                (r.get("full_name") or "").strip().lower(),
                            ) in allowed
                            or (r.get("last_name") or "").strip().lower()
                            in {a[0] for a in allowed if a[0]}
                        ]
                    self._populate_search_tree(records)
                    filt = []
                    if state_f:
                        filt.append(state_f)
                    if race_f:
                        filt.append(race_f)
                    if eth_f:
                        filt.append(eth_f)
                    extra = f" · {', '.join(filt)}" if filt else ""
                    self.search_status.configure(
                        text=(
                            f"{len(records)} name matches{extra} · "
                            f"{results.query_time_ms:.0f} ms"
                        )
                    )
                elif eth_f:
                    results = searcher.search_by_surname_ethnicity(
                        eth_f, state=state_f, limit=500
                    )
                    records = list(results.records)
                    if race_f:
                        if race_f.upper() == "INDIAN":
                            records = [
                                r for r in records
                                if "indian" in (r.get("race") or "").lower()
                                or "indian" in (r.get("ethnicity") or "").lower()
                                or "indian" in (r.get("likely_ethnicity") or "").lower()
                                or "south asian" in (r.get("race") or "").lower()
                                or not (r.get("race") or "").strip()
                            ]
                        else:
                            records = [
                                r for r in records
                                if (r.get("race") or "").strip().upper() == race_f.upper()
                            ]
                    self._populate_search_tree(records)
                    where = f" · {state_f}" if state_f else ""
                    self.search_status.configure(
                        text=(
                            f"{len(records)} with surname ethnicity {eth_f}{where}"
                            + (f" · race {race_f}" if race_f else "")
                            + f" · {results.query_time_ms:.0f} ms"
                        )
                    )
                elif race_f:
                    results = searcher.search_by_race(
                        race=race_f,
                        state=state_f,
                        limit=500,
                    )
                    self._populate_search_tree(results.records)
                    where = f" · {state_f}" if state_f else ""
                    self.search_status.configure(
                        text=f"{len(results.records)} with race {race_f}{where}"
                    )
                elif state_f:
                    results = searcher.search_by_state(state=state_f, limit=500)
                    self._populate_search_tree(results.records)
                    self.search_status.configure(
                        text=f"{len(results.records)} in {state_f}"
                    )
                else:
                    # Default / Show all: list of offenders by name, not race stats
                    results = searcher.search_by_state(state="ALL", limit=500)
                    self._populate_search_tree(results.records)
                    total = searcher.get_total_count()
                    shown = len(results.records)
                    self.search_status.configure(
                        text=(
                            f"{shown} names"
                            + (
                                f" (of {total:,} total)"
                                if total > shown
                                else f" · {total:,} total"
                            )
                            + " · select a row for detail"
                        )
                    )
            except Exception as e:
                try:
                    self._populate_search_tree([])
                except Exception:
                    pass
                try:
                    self.search_status.configure(text=f"Search error: {e}")
                except Exception:
                    pass
                try:
                    self.log_queue.put(f"Search error: {e}")
                except Exception:
                    pass
        finally:
            searcher.close()

    def _populate_search_tree(self, records):
        # Reset sort so a prior column sort cannot leave the tree looking empty
        try:
            st = getattr(self.search_tree, "_sort_state", None)
            if isinstance(st, dict):
                st["col"] = None
                st["reverse"] = False
        except Exception:
            pass
        # Detach selection/bindings side-effects before delete (avoids select storms)
        try:
            self.search_tree.selection_remove(*self.search_tree.selection())
        except Exception:
            pass
        self.search_tree.delete(*self.search_tree.get_children())
        self._search_records_by_iid = {}
        # Insert rows first so a detail-drawer photo glitch cannot blank results
        for r in records[:500] if records else []:
            name = (
                " ".join(
                    p for p in (
                        r.get("first_name") or "",
                        r.get("middle_name") or "",
                        r.get("last_name") or "",
                    ) if str(p).strip()
                ).strip()
                or (r.get("full_name") or "—")
            )
            crime = (
                (r.get("crime") or r.get("offense_description") or r.get("offense_type") or "")
                or "—"
            )
            st = _format_state_display(r)
            iid = self.search_tree.insert(
                "",
                "end",
                values=(
                    name,  # full name — not truncated
                    _format_race_display(r.get("race")),
                    st,
                    r.get("county") or "—",
                    str(r.get("age") or ""),
                    crime,  # full crime text
                    r.get("address") or "—",
                ),
            )
            self._search_records_by_iid[iid] = dict(r)
        try:
            self.search_tree.yview_moveto(0)
        except Exception:
            pass
        if getattr(self, "search_detail", None) is not None:
            try:
                self._fill_detail_drawer(self.search_detail, None)
            except Exception as e:
                try:
                    self.log_queue.put(f"Detail drawer: {e}")
                except Exception:
                    pass

    def _search_on_select(self, _event=None):
        sel = self.search_tree.selection()
        if not sel:
            return
        rec = self._search_records_by_iid.get(sel[0])
        if rec and rec.get("id") and not rec.get("photo_path"):
            # Refresh full row from DB for photo/html
            try:
                from scraper.database import Database

                db = Database(self.db_path)
                try:
                    full = db.get_offender_by_id(int(rec["id"]))
                    if full:
                        rec = full
                        self._search_records_by_iid[sel[0]] = full
                finally:
                    db.close()
            except Exception:
                pass
        self._fill_detail_drawer(self.search_detail, rec)

    def _show_race_distribution(self, dist):
        self.search_tree.delete(*self.search_tree.get_children())
        self._search_records_by_iid = {}
        self._fill_detail_drawer(self.search_detail, None)
        total = sum(d.get("count", 0) for d in dist) or 1
        for d in dist:
            race = d.get("race") or "—"
            count = d.get("count", 0)
            pct = count / total * 100
            bar = "▮" * max(1, int(pct / 4))
            self.search_tree.insert(
                "", "end", values=(race, str(count), f"{pct:.1f}%", bar, "", "", "")
            )

    # -----------------------------------------------------------------------
    # Integrity dashboard + requeue
    # -----------------------------------------------------------------------
