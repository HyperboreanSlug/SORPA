"""Browse → Reports sub-tab (verdicts + export)."""
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



class ReportsTabMixin:
    # Manual ethnicity overrides on report cards (display labels)
    _ETHNICITY_OPTIONS = [
        "Asian",
        "Asian (vietnamese)",
        "Asian (chinese)",
        "Asian (korean)",
        "Asian (japanese)",
        "Asian (filipino)",
        "Indian",
        "Indian (india)",
        "Hispanic",
        "African American",
        "Arabic",
        "Jewish",
        "Portuguese",
        "European",
        "Native American",
        "Unknown",
    ]

    def _build_reports(self, tab):
        """Scrollable photo gallery for verifying mismatches and exporting."""
        tab.configure(fg_color=C["surface"])
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        # ---- Toolbar ----
        top = ctk.CTkFrame(tab, fg_color=C["surface"])
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))

        bar = ctk.CTkFrame(top, fg_color="transparent")
        bar.pack(fill="x", padx=4, pady=(0, 4))

        ctk.CTkButton(
            bar, text="Analyze & build", width=130,
            command=self._reports_build_list,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(side="left", padx=(0, 6))

        self.report_photos_only = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            bar, text="Photos only", variable=self.report_photos_only,
            font=FONT_SM, text_color=C["text"],
            fg_color=C["accent"], hover_color=C["accent_hover"],
            border_color=C["border"], checkmark_color=C["bg"],
            command=lambda: self._reports_on_filter_change(),
        ).pack(side="left", padx=(0, 8))

        # Include stored DeepFace mugshot hits (from DeepFace → Scan) — off by default
        self.report_include_deepface = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            bar, text="DeepFace hits", variable=self.report_include_deepface,
            font=FONT_SM, text_color=C["text"],
            fg_color=C["accent"], hover_color=C["accent_hover"],
            border_color=C["border"], checkmark_color=C["bg"],
            command=lambda: self._reports_on_filter_change(),
        ).pack(side="left", padx=(0, 8))

        # Layout: list rows vs multi-column mugshot grid (also used by Open HTML)
        self.report_grid_view = ctk.BooleanVar(value=True)
        self.report_layout_mode = ctk.StringVar(value="Grid")
        ctk.CTkLabel(bar, text="Layout", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(4, 4)
        )
        self.report_layout_seg = ctk.CTkSegmentedButton(
            bar,
            values=["Grid", "List"],
            variable=self.report_layout_mode,
            command=self._reports_on_layout_change,
            font=FONT_SM,
            fg_color=C["elevated"],
            selected_color=C["accent_dim"],
            selected_hover_color=C["select"],
            unselected_color=C["elevated"],
            unselected_hover_color=C["panel"],
            text_color=C["text"],
            height=28,
        )
        self.report_layout_seg.pack(side="left", padx=(0, 10))
        try:
            self.report_layout_seg.set("Grid")
        except Exception:
            pass

        # Race toggles (misclassified-as buckets) — White only by default
        self.report_race_white = ctk.BooleanVar(value=True)
        self.report_race_black = ctk.BooleanVar(value=False)
        self.report_race_other = ctk.BooleanVar(value=False)
        for label, var in (
            ("White", self.report_race_white),
            ("Black", self.report_race_black),
            ("Other", self.report_race_other),
        ):
            ctk.CTkCheckBox(
                bar, text=label, variable=var,
                font=FONT_SM, text_color=C["text"],
                fg_color=C["accent"], hover_color=C["accent_hover"],
                border_color=C["border"], checkmark_color=C["bg"],
                command=lambda: self._reports_on_filter_change(),
            ).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(bar, text="Page size", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(8, 4)
        )
        self.report_max_var = ctk.IntVar(value=48)
        page_size_entry = ctk.CTkEntry(
            bar, textvariable=self.report_max_var, width=48,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        )
        page_size_entry.pack(side="left", padx=(0, 8))
        # Enter reapplies page size (Prev/Next also re-read it each click)
        page_size_entry.bind(
            "<Return>", lambda _e: self._reports_on_filter_change()
        )

        ctk.CTkLabel(bar, text="Show", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(4, 4)
        )
        # Work queue default: unconfirmed only
        self.report_verdict_filter = ctk.StringVar(value="Unconfirmed")
        ctk.CTkComboBox(
            bar, variable=self.report_verdict_filter, width=170,
            values=[
                "Unconfirmed",
                "Confirmed incorrect",
                "Confirmed correct",
                "All",
            ],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
            # Pass selection explicitly — StringVar can lag one tick behind command
            command=lambda v: self._reports_on_filter_change(show_value=v),
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            bar, text="Confirm unchecked", width=130,
            command=self._reports_confirm_unchecked,
            fg_color="#5c3030", hover_color="#7a4040", text_color=C["text"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            bar, text="Open HTML", width=100,
            command=self._reports_open_html,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            bar, text="Export CSV", width=90,
            command=self._reports_export_csv,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left")

        # Pagination row
        page_row = ctk.CTkFrame(top, fg_color="transparent")
        page_row.pack(fill="x", padx=4, pady=(0, 2))
        self._report_page = 0
        self._report_pool: list = []  # full filtered list
        ctk.CTkButton(
            page_row, text="◀ Prev", width=80,
            command=self._reports_prev_page,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=(0, 6))
        self.report_page_label = ctk.CTkLabel(
            page_row, text="Page —", font=FONT_SM, text_color=C["muted"],
        )
        self.report_page_label.pack(side="left", padx=6)
        ctk.CTkButton(
            page_row, text="Next ▶", width=90,
            command=self._reports_next_page,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=(6, 0))
        self.report_layout_hint = ctk.CTkLabel(
            page_row,
            text="Grid · multi-column mugshots  ·  Open HTML uses same layout",
            font=FONT_SM,
            text_color=C["dim"],
        )
        self.report_layout_hint.pack(side="left", padx=(14, 0))

        # ---- Summary metrics ----
        sum_row = ctk.CTkFrame(top, fg_color="transparent")
        sum_row.pack(fill="x", padx=4, pady=(0, 4))

        def _chip(key: str) -> ctk.CTkLabel:
            chip = ctk.CTkFrame(
                sum_row, fg_color=C["elevated"], corner_radius=6,
                border_width=1, border_color=C["border"],
            )
            chip.pack(side="left", padx=3, pady=1, fill="x", expand=True)
            lb = ctk.CTkLabel(
                chip, text="—", font=FONT_SM, text_color=C["text"], anchor="center",
            )
            lb.pack(padx=8, pady=5)
            setattr(self, key, lb)
            return lb

        _chip("report_m_total")
        _chip("report_m_photo")
        _chip("report_m_confirmed")
        _chip("report_m_correct")
        _chip("report_m_unreviewed")

        self.report_status = ctk.CTkLabel(
            top,
            text=(
                "Click Analyze & build (uses Misclassify ethnicity / min conf). "
                "Show: Unconfirmed (default) · Confirmed correct drops off this sheet."
            ),
            font=FONT_SM, text_color=C["dim"], anchor="w",
        )
        self.report_status.pack(fill="x", padx=8, pady=(0, 4))

        # ---- Scrollable card list (fast wheel binding after paint) ----
        scroll = ctk.CTkScrollableFrame(
            tab, fg_color=C["surface"], corner_radius=0, border_width=0,
        )
        scroll.grid(row=2, column=0, sticky="nsew", padx=4, pady=(0, 6))
        scroll.grid_columnconfigure(0, weight=1)
        self._report_scroll = scroll
        self._report_tab = tab
        self.after(30, lambda: _wire_wide_scroll(tab, scroll))
        self.after(80, lambda: self._reports_bind_fast_scroll(tab, scroll))

        # Empty-state placeholder
        self._report_empty = ctk.CTkLabel(
            scroll,
            text=(
                "No report list yet.\n\n"
                "1. Set ethnicity / min conf (shared with Misclassify)\n"
                "2. Click Analyze & build\n"
                "3. Review Unconfirmed — mark Confirmed incorrect or Confirmed correct\n"
                "4. Confirmed cards leave Unconfirmed (use Show → Confirmed / All)\n"
                "5. Show: Unconfirmed · Confirmed incorrect · Confirmed correct · All"
            ),
            font=FONT_SM, text_color=C["dim"], justify="left",
        )
        self._report_empty.pack(anchor="w", padx=16, pady=24)

    def _load_report_verdicts(self) -> None:
        path = getattr(self, "_report_verdicts_path", None) or (
            ROOT / "data" / "report_verdicts.json"
        )
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._report_verdicts = {
                        str(k): str(v)
                        for k, v in data.items()
                        if v in ("confirmed", "correct", "skip", "unreviewed")
                    }
        except Exception:
            self._report_verdicts = {}

    def _save_report_verdicts(self) -> None:
        path = getattr(self, "_report_verdicts_path", None) or (
            ROOT / "data" / "report_verdicts.json"
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._report_verdicts, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception:
            pass

    @staticmethod
    def _report_item_key(mc) -> str:
        rec = mc.record or {}
        rid = rec.get("id")
        if rid is not None and str(rid).strip() != "":
            return f"id:{rid}"
        name = (
            f"{rec.get('first_name', '') or ''} {rec.get('last_name', '') or ''}"
        ).strip() or (rec.get("full_name") or "")
        return f"n:{name}|{mc.expected_race}|{mc.likely_ethnicity}|{mc.confidence}"

    def _report_verdict_lookup_keys(self, mc) -> List[str]:
        """All keys that may hold a saved verdict for this row (stable + legacy)."""
        keys: List[str] = []
        primary = self._report_item_key(mc)
        keys.append(primary)
        rec = mc.record or {}
        rid = rec.get("id")
        if rid is not None and str(rid).strip() != "":
            keys.append(f"id:{rid}")
        try:
            keys.append(self._report_person_key(mc))
        except Exception:
            pass
        # de-dupe preserve order
        seen = set()
        out: List[str] = []
        for k in keys:
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        return out

    @staticmethod
    def _report_person_key(mc) -> str:
        """Collapse near-duplicate listings of the same person for the report list.

        Includes middle name + DOB so NIRAJ V PATEL and NIRAJ RASHMIBABU PATEL
        do not collapse together.
        """
        from scraper.database import Database
        from scraper.database.identity import person_identity_key

        rec = mc.record or {}
        try:
            stable = Database.stable_external_key(rec)
            if stable:
                return f"p:{stable}"
        except Exception:
            pass
        try:
            norm = Database.normalize_identity_url(rec.get("source_url") or "")
            if norm:
                return f"u:{norm}"
        except Exception:
            pass
        try:
            return f"idk:{person_identity_key(rec)}"
        except Exception:
            pass
        fn = (rec.get("first_name") or "").strip().casefold()
        mn = (rec.get("middle_name") or "").strip().casefold()
        ln = (rec.get("last_name") or "").strip().casefold()
        st = (
            rec.get("state") or rec.get("source_state") or ""
        ).strip().upper()
        dob = (rec.get("date_of_birth") or "").strip().casefold()
        if fn and ln:
            return f"n:{fn}|{mn}|{ln}|{st}|{dob}"
        return ReportsTabMixin._report_item_key(mc)

    def _verdict_for_mc(self, mc) -> str:
        """Resolve verdict; prefer non-unreviewed if any alias key has a decision."""
        found = "unreviewed"
        for k in self._report_verdict_lookup_keys(mc):
            v = (self._report_verdicts.get(k) or "").strip()
            if v in ("confirmed", "correct", "skip"):
                return v
            if v == "unreviewed":
                found = "unreviewed"
        return found

    def _set_verdict_for_mc(self, mc, verdict: str, *, save: bool = True) -> None:
        keys = self._report_verdict_lookup_keys(mc)
        if verdict == "unreviewed":
            for key in keys:
                self._report_verdicts.pop(key, None)
        else:
            # Write primary + id alias so later key shape changes still resolve
            for key in keys:
                self._report_verdicts[key] = verdict
        if save:
            self._save_report_verdicts()

    def _set_ethnicity_for_mc(self, mc, ethnicity: str) -> None:
        """Persist a manual ethnicity correction on the misclass row + DB."""
        eth = (ethnicity or "").strip() or "Unknown"
        mc.likely_ethnicity = eth
        names = list(mc.matching_names or [])
        if "manual_override" not in names:
            names = ["manual_override"] + names
        mc.matching_names = names
        rec = mc.record if isinstance(mc.record, dict) else {}
        rec["likely_ethnicity"] = eth
        mc.record = rec
        rid = rec.get("id")
        if rid is not None:
            try:
                from scraper.database import Database

                db = Database(self.db_path)
                try:
                    db.update_offender(int(rid), {"likely_ethnicity": eth})
                finally:
                    db.close()
            except Exception:
                pass

    def _ethnicity_compatible_with_record(self, mc) -> bool:
        """True if name-based ethnicity now matches recorded race (not a mismatch)."""
        try:
            from scraper.searcher import _is_compatible

            rec = mc.record or {}
            return bool(
                _is_compatible(
                    mc.likely_ethnicity or "",
                    (rec.get("race") or mc.expected_race or "").strip(),
                    recorded_ethnicity=(rec.get("ethnicity") or "").strip() or None,
                )
            )
        except Exception:
            return False

    # Display label → internal verdict key for Reports "Show" filter
    _REPORT_SHOW_TO_VERDICT = {
        "unconfirmed": "unreviewed",
        "unreviewed": "unreviewed",
        "confirmed incorrect": "confirmed",
        "confirmed": "confirmed",
        "confirmed misclass": "confirmed",
        "confirmed correct": "correct",
        "correct": "correct",
        "correct label": "correct",
        "skip": "skip",
        "skipped": "skip",
        "all": "all",
    }

    def _reports_verdict_filter_key(self, show_value: Optional[str] = None) -> str:
        """Normalize Show dropdown → unreviewed|confirmed|correct|skip|all."""
        raw = (
            show_value
            if show_value is not None
            else (self.report_verdict_filter.get() or "Unconfirmed")
        )
        raw = str(raw or "Unconfirmed").strip().lower()
        # Tolerate partial / truncated combo text
        if raw in self._REPORT_SHOW_TO_VERDICT:
            return self._REPORT_SHOW_TO_VERDICT[raw]
        if "unconfirm" in raw or raw == "pending":
            return "unreviewed"
        if "incorrect" in raw or raw == "misclass":
            return "confirmed"
        if "correct" in raw:
            return "correct"
        if raw == "all" or raw.startswith("all "):
            return "all"
        return "unreviewed"

    @staticmethod
    def _reports_verdict_passes_filter(verdict: str, vfilter: str) -> bool:
        """Strict Show filter: Unconfirmed never includes confirmed/correct/skip."""
        v = (verdict or "unreviewed").strip() or "unreviewed"
        f = (vfilter or "unreviewed").strip() or "unreviewed"
        if f == "all":
            return True
        if f == "unreviewed":
            # Only never-reviewed cards — not confirmed incorrect/correct/skip
            return v == "unreviewed"
        return v == f

    def _results_excluding_correct(self, results: Optional[list] = None) -> list:
        """Misclass results with Correct-label verdicts removed (for Statistics)."""
        src = list(results if results is not None else (self._misclass_results or []))
        out = []
        for mc in src:
            if self._verdict_for_mc(mc) == "correct":
                continue
            out.append(mc)
        return out

    def _refresh_stats_from_verdicts(self) -> None:
        """Recompute Statistics after Correct labels change."""
        meta = getattr(self, "_misclass_meta", None) or {}
        if not meta and not self._misclass_results:
            return
        filtered = self._results_excluding_correct()
        # Correct rows no longer count as mismatches; eth_base unchanged
        try:
            self._update_misclass_stats(
                filtered,
                db_total=int(meta.get("db_total") or 0),
                scanned_cap=int(meta.get("scanned_cap") or 0),
                min_conf=float(meta.get("min_conf") or 0.5),
                eth_filter=str(meta.get("eth_filter") or "all"),
                eth_base_count=meta.get("eth_base_count"),
            )
        except Exception:
            pass
        # Rebuild misclass tree without correct rows
        if hasattr(self, "misclass_tree"):
            try:
                self._populate_misclass_tree(filtered)
            except Exception:
                pass

    def _populate_misclass_tree(self, results: list) -> None:
        if not hasattr(self, "misclass_tree"):
            return
        self.misclass_tree.delete(*self.misclass_tree.get_children())
        self._misclass_records_by_iid = {}
        for mc in results[:500]:
            rec = dict(mc.record or {})
            name = (
                " ".join(
                    p for p in (
                        rec.get("first_name") or "",
                        rec.get("middle_name") or "",
                        rec.get("last_name") or "",
                    ) if str(p).strip()
                )
                or (rec.get("full_name") or "—")
            )
            rec["_misclass_expected_race"] = mc.expected_race
            rec["_misclass_likely"] = mc.likely_ethnicity
            rec["_misclass_conf"] = mc.confidence
            iid = self.misclass_tree.insert(
                "",
                "end",
                values=(
                    name,
                    (mc.expected_race or "—")[:14],
                    (mc.likely_ethnicity or "")[:18],
                    f"{mc.confidence:.3f}",
                    "; ".join(mc.matching_names[:3]),
                ),
            )
            self._misclass_records_by_iid[iid] = rec

    def _reports_race_buckets_allowed(self) -> set:
        """Which misclassified-as race buckets are enabled (White/Black/Other)."""
        allow: set = set()
        if bool(getattr(self, "report_race_white", None) and self.report_race_white.get()):
            allow.add("White")
        if bool(getattr(self, "report_race_black", None) and self.report_race_black.get()):
            allow.add("Black")
        if bool(getattr(self, "report_race_other", None) and self.report_race_other.get()):
            allow.add("Other")
        # If none selected, treat as all (avoid empty list by accident)
        if not allow:
            allow = {"White", "Black", "Other"}
        return allow

    def _reports_on_layout_change(self, value: Optional[str] = None) -> None:
        """Toggle Grid vs List cards; keep BooleanVar in sync for Open HTML."""
        raw = (
            value
            if value is not None
            else (getattr(self, "report_layout_mode", None) and self.report_layout_mode.get())
            or "Grid"
        )
        is_grid = str(raw or "Grid").strip().lower().startswith("g")
        if not hasattr(self, "report_grid_view") or self.report_grid_view is None:
            self.report_grid_view = ctk.BooleanVar(value=is_grid)
        else:
            try:
                self.report_grid_view.set(is_grid)
            except Exception:
                self.report_grid_view = ctk.BooleanVar(value=is_grid)
        if hasattr(self, "report_layout_hint"):
            try:
                self.report_layout_hint.configure(
                    text=(
                        "Grid · multi-column mugshots  ·  Open HTML uses same layout"
                        if is_grid
                        else "List · full-width rows  ·  Open HTML uses same layout"
                    )
                )
            except Exception:
                pass
        try:
            self._reports_rebuild_cards(refilter=False)
        except Exception:
            pass

    def _reports_is_grid(self) -> bool:
        """True when layout is Grid (segment or legacy checkbox)."""
        if hasattr(self, "report_layout_mode") and self.report_layout_mode is not None:
            try:
                return str(self.report_layout_mode.get() or "").strip().lower().startswith("g")
            except Exception:
                pass
        return bool(
            getattr(self, "report_grid_view", None) and self.report_grid_view.get()
        )

    def _reports_page_size(self) -> int:
        try:
            n = int(self.report_max_var.get())
        except (TypeError, ValueError):
            n = 40
        return max(1, min(n if n > 0 else 40, 500))

    def _reports_on_filter_change(self, show_value: Optional[str] = None) -> None:
        """Race/verdict/photos filter changed — rebuild pool from page 0."""
        if show_value is not None:
            try:
                self.report_verdict_filter.set(str(show_value))
            except Exception:
                pass
        self._report_page = 0
        self._reports_rebuild_cards(refilter=True)

    def _reports_apply_page(self) -> list:
        """Slice _report_pool into current page; update page label."""
        pool = list(getattr(self, "_report_pool", None) or self._report_items or [])
        page_size = self._reports_page_size()
        total = len(pool)
        n_pages = max(1, (total + page_size - 1) // page_size) if total else 1
        page = int(getattr(self, "_report_page", 0) or 0)
        page = max(0, min(page, n_pages - 1))
        self._report_page = page
        start = page * page_size
        end = min(start + page_size, total)
        slice_ = pool[start:end]
        self._report_items = slice_
        if hasattr(self, "report_page_label"):
            if total == 0:
                self.report_page_label.configure(text="Page — · 0 people")
            else:
                self.report_page_label.configure(
                    text=(
                        f"Page {page + 1} / {n_pages}  ·  "
                        f"showing {start + 1}–{end} of {total:,}"
                    )
                )
        return slice_

    def _reports_next_page(self) -> None:
        pool = getattr(self, "_report_pool", None) or []
        if not pool and self._misclass_results:
            self._report_pool = self._reports_filtered_source()
            pool = self._report_pool
        page_size = self._reports_page_size()
        n_pages = max(1, (len(pool) + page_size - 1) // page_size) if pool else 1
        cur = int(getattr(self, "_report_page", 0) or 0)
        if cur + 1 >= n_pages:
            if hasattr(self, "report_status"):
                self.report_status.configure(text="Already on last page")
            return
        self._report_page = cur + 1
        self._reports_rebuild_cards(refilter=False)

    def _reports_prev_page(self) -> None:
        cur = int(getattr(self, "_report_page", 0) or 0)
        if cur <= 0:
            if hasattr(self, "report_status"):
                self.report_status.configure(text="Already on first page")
            return
        self._report_page = cur - 1
        self._reports_rebuild_cards(refilter=False)

    def _reports_load_deepface_hits(self) -> list:
        """Load stored DeepFace gross-misclass hits as Misclassification rows."""
        try:
            from scraper.mugshot_ethnicity.scanner import load_deepface_hits_as_misclass

            return load_deepface_hits_as_misclass(
                db_path=str(getattr(self, "db_path", None) or "data/offenders.db"),
                min_confidence=0.0,
            )
        except Exception:
            return []

    def _reports_merge_sources(self, surname_results: list, deepface_results: list) -> list:
        """Merge surname misclass + DeepFace hits; attach face data onto surname rows."""
        from scraper.database import Database

        by_id: Dict[Any, Any] = {}
        # Index deepface by offender id
        df_by_id: Dict[int, Any] = {}
        for mc in deepface_results or []:
            rec = mc.record or {}
            try:
                oid = int(rec["id"]) if rec.get("id") is not None else None
            except (TypeError, ValueError):
                oid = None
            if oid is not None:
                df_by_id[oid] = mc

        for mc in surname_results or []:
            rec = dict(mc.record or {})
            try:
                oid = int(rec["id"]) if rec.get("id") is not None else None
            except (TypeError, ValueError):
                oid = None
            # Attach DeepFace face info when available for the same person
            if oid is not None and oid in df_by_id:
                df_mc = df_by_id[oid]
                df_payload = (df_mc.record or {}).get("_deepface") or {}
                rec["_deepface"] = df_payload
                rec["_deepface_is_hit"] = True
                names = list(mc.matching_names or [])
                for n in (df_mc.matching_names or []):
                    if n not in names:
                        names.append(n)
                mc.matching_names = names
                # Prefer higher of name-conf vs face-conf for sort; keep name ethnicity
                try:
                    face_c = float(df_payload.get("top_confidence") or 0)
                    if face_c > float(mc.confidence or 0):
                        # Keep surname ethnicity as primary label; conf reflects stronger signal
                        mc.confidence = max(float(mc.confidence or 0), face_c)
                except (TypeError, ValueError):
                    pass
                mc.record = rec
                del df_by_id[oid]
            key = oid if oid is not None else id(mc)
            by_id[key] = mc

        # Remaining pure DeepFace hits (no surname misclass row)
        for oid, mc in df_by_id.items():
            by_id[oid] = mc

        return list(by_id.values())

    def _reports_filtered_source(self) -> list:
        """Apply report filters to surname + DeepFace results (full pool)."""
        surname = list(self._misclass_results or [])
        deepface: list = []
        if bool(getattr(self, "report_include_deepface", None) and self.report_include_deepface.get()):
            deepface = self._reports_load_deepface_hits()
        results = self._reports_merge_sources(surname, deepface)
        if not results:
            return []

        photos_only = bool(self.report_photos_only.get())
        vfilter = self._reports_verdict_filter_key()
        race_allow = self._reports_race_buckets_allowed()
        # Ensure verdicts file is loaded (first open / new session)
        if not getattr(self, "_report_verdicts_loaded", False):
            if not hasattr(self, "_report_verdicts") or self._report_verdicts is None:
                self._report_verdicts = {}
            self._load_report_verdicts()
            self._report_verdicts_loaded = True

        # Prefetch photo paths when missing
        need_ids: List[int] = []
        for mc in results:
            rec = mc.record or {}
            if not (rec.get("photo_path") or "").strip() and rec.get("id") is not None:
                try:
                    need_ids.append(int(rec["id"]))
                except (TypeError, ValueError):
                    pass
        photo_by_id: Dict[int, Dict[str, Any]] = {}
        if need_ids:
            try:
                from scraper.database import Database

                db = Database(self.db_path)
                try:
                    for oid in need_ids[:2000]:
                        full = db.get_offender_by_id(oid)
                        if full:
                            photo_by_id[oid] = full
                finally:
                    db.close()
            except Exception:
                photo_by_id = {}

        # Enrich records with DB fields (photo / HTML / URL)
        if photo_by_id:
            for mc in results:
                rec = mc.record or {}
                try:
                    oid = int(rec["id"]) if rec.get("id") is not None else None
                except (TypeError, ValueError):
                    oid = None
                if oid is None or oid not in photo_by_id:
                    continue
                full = photo_by_id[oid]
                merged = dict(full)
                for k, v in rec.items():
                    if str(k).startswith("_"):
                        merged[k] = v
                mc.record = merged

        # Collapse same-person duplicates (session-url variants etc.)
        from scraper.database import Database

        best_by_person: Dict[str, Any] = {}
        for mc in results:
            rec = mc.record or {}
            bucket = _misclass_race_bucket(mc.expected_race)
            if bucket not in race_allow:
                continue
            photo = (rec.get("photo_path") or "").strip()
            has_photo = bool(photo and Path(photo).is_file())
            if photos_only and not has_photo:
                continue
            person = self._report_person_key(mc)
            prev = best_by_person.get(person)
            if prev is None:
                best_by_person[person] = mc
                continue
            # Prefer richer record / higher confidence / deepface attachment
            prev_rec = prev.record or {}
            score_new = (
                1 if (rec.get("_deepface") or {}).get("is_hit") else 0,
                Database._row_richness(rec),
                float(mc.confidence or 0),
                1 if has_photo else 0,
            )
            score_old = (
                1 if (prev_rec.get("_deepface") or {}).get("is_hit") else 0,
                Database._row_richness(prev_rec),
                float(prev.confidence or 0),
                1 if (prev_rec.get("photo_path") or "").strip() else 0,
            )
            if score_new >= score_old:
                best_by_person[person] = mc

        out = []
        for mc in best_by_person.values():
            verdict = self._verdict_for_mc(mc)
            if not self._reports_verdict_passes_filter(verdict, vfilter):
                continue
            out.append(mc)

        # Stable order: confidence desc within the selected verdict bucket
        out.sort(key=lambda m: float(m.confidence or 0), reverse=True)
        return out

    def _reports_confirm_unchecked(self) -> None:
        """Mark only unconfirmed visible cards as Confirmed incorrect."""
        items = list(self._report_items or [])
        if not items:
            messagebox.showinfo("Reports", "Run Analyze & build first.")
            return
        unchecked = [
            mc for mc in items if self._verdict_for_mc(mc) == "unreviewed"
        ]
        if not unchecked:
            messagebox.showinfo(
                "Confirm unchecked",
                "No unconfirmed cards on this page.\n"
                "Already Confirmed incorrect / correct / skip are left alone.",
            )
            return
        ok = messagebox.askyesno(
            "Confirm unchecked?",
            (
                f"Mark {len(unchecked):,} unconfirmed _card(s) on this page "
                f"as Confirmed incorrect?\n\n"
                "They leave the Unconfirmed sheet (switch Show to see them).\n"
                "Already marked cards are not changed."
            ),
        )
        if not ok:
            return
        for mc in unchecked:
            self._set_verdict_for_mc(mc, "confirmed", save=False)
        self._save_report_verdicts()
        self._reports_rebuild_cards()
        self._refresh_stats_from_verdicts()
        if hasattr(self, "report_status"):
            self.report_status.configure(
                text=(
                    f"Marked {len(unchecked):,} as Confirmed incorrect "
                    f"(left Unconfirmed sheet)"
                )
            )

    def _reports_confirm_others(self, keep_mc) -> None:
        """Confirm other visible unreviewed cards; leave *keep_mc* unchanged."""
        keep_key = self._report_item_key(keep_mc)
        n = 0
        for mc in list(self._report_items or []):
            if self._report_item_key(mc) == keep_key:
                continue
            if self._verdict_for_mc(mc) != "unreviewed":
                continue  # only unchecked; never overwrite Correct/Confirmed/Skip
            self._set_verdict_for_mc(mc, "confirmed", save=False)
            n += 1
        self._save_report_verdicts()
        self._reports_rebuild_cards()
        self._refresh_stats_from_verdicts()
        if hasattr(self, "report_status"):
            self.report_status.configure(
                text=f"Confirmed {n:,} other unchecked visible cards"
            )

    def _reports_build_list(self):
        """Run Analyze (shared filters), merge DeepFace hits, render photo cards."""
        try:
            # Misclassify tab is lazy — ensure filter vars exist before Analyze
            if hasattr(self, "_ensure_misclass_filter_vars"):
                self._ensure_misclass_filter_vars()
            self._run_misclassification()
        except Exception as e:
            messagebox.showerror("Analyze & build", str(e))
            return
        self._report_page = 0
        self._report_pool = self._reports_filtered_source()
        if not self._report_pool:
            messagebox.showinfo(
                "Reports",
                "No mismatches for the current filters.\n"
                "• Run surname Analyze with lower min conf., or\n"
                "• Run DeepFace → Scan first (hits appear when “DeepFace hits” is checked).",
            )
            self._report_items = []
            self._reports_rebuild_cards(refilter=False)
            self._reports_update_metrics()
            return
        self._reports_rebuild_cards(refilter=False)
        self._reports_update_metrics()

    def _reports_rebuild_cards(self, *, refilter: bool = True):
        """Destroy and recreate card widgets for the current page of results."""
        scroll = getattr(self, "_report_scroll", None)
        if scroll is None:
            return

        if refilter and (
            self._misclass_results
            or bool(
                getattr(self, "report_include_deepface", None)
                and self.report_include_deepface.get()
            )
        ):
            self._report_pool = self._reports_filtered_source()
            # Keep page in range after refilter
            page_size = self._reports_page_size()
            n_pages = max(
                1,
                (len(self._report_pool) + page_size - 1) // page_size,
            ) if self._report_pool else 1
            self._report_page = min(int(getattr(self, "_report_page", 0) or 0), n_pages - 1)

        items = self._reports_apply_page()

        for child in list(scroll.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        self._report_image_refs = []

        if not items:
            empty = ctk.CTkLabel(
                scroll,
                text=(
                    "No people match the current Show / race filters.\n"
                    "Try Show → Unconfirmed, Confirmed incorrect, or Confirmed correct · "
                    "or enable White/Black/Other · re-run Analyze."
                ),
                font=FONT_SM, text_color=C["dim"], justify="left",
            )
            empty.pack(anchor="w", padx=16, pady=24)
            self._reports_update_metrics()
            return

        pool_n = len(getattr(self, "_report_pool", None) or items)
        page_size = self._reports_page_size()
        page = int(getattr(self, "_report_page", 0) or 0)
        offset = page * page_size
        grid = self._reports_is_grid()
        if grid:
            host = ctk.CTkFrame(scroll, fg_color="transparent")
            host.pack(fill="both", expand=True, padx=4, pady=4)
            # ~3–6 columns depending on width (larger tiles for mugshots)
            try:
                w = int(scroll.winfo_width() or 0)
            except Exception:
                w = 0
            if w < 200:
                w = 1000
            n_cols = max(2, min(6, w // 200))
            for c in range(n_cols):
                host.grid_columnconfigure(c, weight=1, uniform="rg")
            for i, mc in enumerate(items):
                card = self._reports_add_card(
                    host, mc, index=offset + i + 1, total=pool_n, grid=True
                )
                if card is not None:
                    card.grid(
                        row=i // n_cols,
                        column=i % n_cols,
                        padx=5,
                        pady=5,
                        sticky="nsew",
                    )
            # Re-flow columns once the scroll area has a real width
            try:
                self.after(
                    120,
                    lambda h=host, it=items, off=offset, pn=pool_n: (
                        self._reports_reflow_grid(h, it, offset=off, pool_n=pn)
                    ),
                )
            except Exception:
                pass
        else:
            for i, mc in enumerate(items):
                self._reports_add_card(
                    scroll, mc, index=offset + i + 1, total=pool_n, grid=False
                )

        # Re-bind fast scroll after widgets change
        try:
            tab = getattr(self, "_report_tab", None)
            if tab is not None:
                self.after(40, lambda: self._reports_bind_fast_scroll(tab, scroll))
        except Exception:
            pass

        self._reports_update_metrics()
        if hasattr(self, "report_status"):
            conf = sum(
                1 for mc in (getattr(self, "_report_pool", None) or items)
                if self._verdict_for_mc(mc) == "confirmed"
            )
            show = (self.report_verdict_filter.get() or "Unconfirmed").strip()
            self.report_status.configure(
                text=(
                    f"Show: {show} · pool {pool_n:,} · page {page + 1} · "
                    f"{conf:,} confirmed incorrect in pool · "
                    "Confirmed correct leaves Unconfirmed"
                )
            )

    def _reports_reflow_grid(
        self, host, items: list, *, offset: int, pool_n: int
    ) -> None:
        """Recompute grid columns after the scroll frame gets a real width."""
        if not self._reports_is_grid():
            return
        scroll = getattr(self, "_report_scroll", None)
        if scroll is None or host is None:
            return
        try:
            if not host.winfo_exists():
                return
        except Exception:
            return
        try:
            w = int(scroll.winfo_width() or 0)
        except Exception:
            w = 0
        if w < 200:
            return
        n_cols = max(2, min(6, w // 200))
        try:
            kids = [c for c in host.winfo_children() if c.winfo_exists()]
        except Exception:
            return
        if not kids:
            return
        for c in range(n_cols):
            try:
                host.grid_columnconfigure(c, weight=1, uniform="rg")
            except Exception:
                pass
        for i, child in enumerate(kids):
            try:
                child.grid(
                    row=i // n_cols,
                    column=i % n_cols,
                    padx=5,
                    pady=5,
                    sticky="nsew",
                )
            except Exception:
                pass

    def _reports_drop_card(self, card_widget, mc) -> None:
        """Remove one card from the UI and pools without rebuilding the page."""
        try:
            card_widget.destroy()
        except Exception:
            pass

        def _same(a, b) -> bool:
            try:
                return self._report_item_key(a) == self._report_item_key(b)
            except Exception:
                return a is b

        if getattr(self, "_report_items", None):
            self._report_items = [x for x in self._report_items if not _same(x, mc)]
        if getattr(self, "_report_pool", None):
            self._report_pool = [x for x in self._report_pool if not _same(x, mc)]

        self._reports_update_metrics()
        if hasattr(self, "report_status"):
            page_n = len(getattr(self, "_report_items", []) or [])
            pool_n = len(getattr(self, "_report_pool", []) or [])
            self.report_status.configure(
                text=f"Dropped · remaining on page {page_n:,} · pool {pool_n:,}"
            )

    def _reports_bind_fast_scroll(self, tab, scroll_frame) -> None:
        """Snappy wheel scrolling over report cards (fraction of viewport)."""
        try:
            canvas = scroll_frame._parent_canvas  # type: ignore[attr-defined]
        except Exception:
            return
        PAGE_FRAC = 0.22

        def _scroll(notches: int) -> None:
            if notches == 0:
                return
            try:
                first, last = canvas.yview()
                page = max(last - first, 0.05)
                step = notches * max(PAGE_FRAC * page, 0.08)
                canvas.yview_moveto(max(0.0, min(1.0, first + step)))
            except Exception:
                canvas.yview_scroll(notches * 10, "units")

        def _wheel(event):
            delta = getattr(event, "delta", 0) or 0
            if delta:
                notches = int(-delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
                if notches == 0:
                    notches = -1 if delta > 0 else 1
                _scroll(notches)
            else:
                num = getattr(event, "num", 0)
                if num == 4:
                    _scroll(-1)
                elif num == 5:
                    _scroll(1)
            return "break"

        def _walk(w):
            try:
                w.bind("<MouseWheel>", _wheel)
                w.bind("<Button-4>", _wheel)
                w.bind("<Button-5>", _wheel)
            except Exception:
                pass
            try:
                for ch in w.winfo_children():
                    _walk(ch)
            except Exception:
                pass

        try:
            _walk(tab)
            _walk(scroll_frame)
            for w in (
                tab,
                getattr(scroll_frame, "_parent_frame", None),
                canvas,
                scroll_frame,
            ):
                if w is None:
                    continue
                try:
                    w.bind("<MouseWheel>", _wheel)
                    w.bind("<Button-4>", _wheel)
                    w.bind("<Button-5>", _wheel)
                except Exception:
                    pass
        except Exception:
            pass

    def _reports_load_thumb(self, photo_path: str, max_size: tuple) -> Optional[Any]:
        """Load a small CTkImage; keep refs for GC. Returns None on failure."""
        try:
            from PIL import Image

            img = Image.open(photo_path)
            # Downsample aggressively for scroll performance
            try:
                resample = Image.Resampling.BILINEAR
            except AttributeError:
                resample = Image.BILINEAR  # type: ignore[attr-defined]
            img.thumbnail(max_size, resample)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            if not hasattr(self, "_report_image_refs") or self._report_image_refs is None:
                self._report_image_refs = []
            self._report_image_refs.append(ctk_img)
            if len(self._report_image_refs) > 120:
                self._report_image_refs = self._report_image_refs[-80:]
            return ctk_img
        except Exception:
            return None

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
        copy_blob = (
            f"{name}\nLISTED AS: {race}\nSurname ethnicity: {eth}"
            f"{crime_line}\nConf {conf:.3f} · {state}\n"
            f"URL: {rec.get('source_url') or '—'}"
        )
        ctk.CTkButton(
            actions, text="Copy", width=50, height=26,
            command=lambda t=copy_blob, n=name: self._copy_to_clipboard(
                t, toast=f"Copied {n}"
            ),
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"], font=FONT_SM,
        ).pack(side="right")
        return card

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
        """Dense tile for grid view — name, listed race, full crime summary (no dates)."""
        # Grow with content so the full crime summary is never clipped
        card = ctk.CTkFrame(
            parent,
            fg_color=C["panel"],
            border_color=border,
            border_width=1,
            corner_radius=8,
            width=210,
        )
        card.grid_propagate(True)

        photo_wrap = ctk.CTkFrame(
            card, fg_color=C["tree_bg"], corner_radius=6, height=158,
        )
        photo_wrap.pack(fill="x", padx=6, pady=(6, 4))
        photo_wrap.pack_propagate(False)
        photo_lbl = ctk.CTkLabel(
            photo_wrap, text="No photo", font=FONT_SM, text_color=C["dim"],
        )
        photo_lbl.place(relx=0.5, rely=0.5, anchor="center")
        if has_photo:
            thumb = self._reports_load_thumb(photo_path, (196, 154))
            if thumb is not None:
                photo_lbl.configure(image=thumb, text="")

        # Full name (middle abbreviated as needed) — wrap up to 2 lines
        display_name = self._reports_grid_display_name(
            first,
            middle,
            last,
            str(rec.get("full_name") or ""),
            max_len=36,
        )
        ctk.CTkLabel(
            card,
            text=display_name,
            font=FONT_BOLD,
            text_color=C["text"],
            anchor="w",
            justify="left",
            wraplength=190,
        ).pack(fill="x", padx=8)
        # High-contrast LISTED race badge (WHITE etc. must pop)
        race_u = str(race or "—").strip().upper() or "—"
        listed_badge = ctk.CTkFrame(
            card,
            fg_color="#6b1a1a",
            corner_radius=8,
            border_width=2,
            border_color="#e07a7a",
        )
        listed_badge.pack(fill="x", padx=6, pady=(4, 2))
        ctk.CTkLabel(
            listed_badge,
            text="LISTED AS",
            font=("Segoe UI", 10, "bold"),
            text_color="#f0b0b0",
            anchor="center",
        ).pack(fill="x", padx=6, pady=(5, 0))
        ctk.CTkLabel(
            listed_badge,
            text=race_u,
            font=("Segoe UI", 18, "bold"),
            text_color="#ffffff",
            anchor="center",
        ).pack(fill="x", padx=6, pady=(0, 6))

        # Proper multi-offense summary (not the full statute dump)
        crime_short = self._reports_summarize_crime(crime, max_len=140)
        crime_line = f"Crime: {crime_short}" if crime_short else "Crime: —"
        crime_box = ctk.CTkFrame(card, fg_color="transparent")
        crime_box.pack(fill="x", padx=8, pady=(2, 0))
        ctk.CTkLabel(
            crime_box,
            text=crime_line,
            font=FONT_SM,
            text_color=C["text"] if crime_short else C["dim"],
            anchor="nw",
            justify="left",
            wraplength=190,
        ).pack(fill="x", anchor="w")

        # Conf / state / face only — no dates, no surname ethnicity
        face_bit = ""
        if df:
            flab = df.get("predicted_label") or df.get("top_label") or ""
            fconf = df.get("top_confidence")
            if flab:
                try:
                    face_bit = f" · face {flab}@{float(fconf):.0%}"
                except (TypeError, ValueError):
                    face_bit = f" · face {flab}"
        ctk.CTkLabel(
            card,
            text=f"{conf:.2f} · {state}{face_bit}",
            font=FONT_SM,
            text_color=C["muted"],
            anchor="w",
        ).pack(fill="x", padx=8, pady=(2, 0))

        status_lbl = ctk.CTkLabel(
            card,
            text=self._reports_verdict_label_short(verdict),
            font=FONT_SM,
            text_color=self._reports_verdict_color(verdict),
            anchor="w",
        )
        status_lbl.pack(fill="x", padx=8, pady=(2, 0))

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill="x", padx=6, pady=(4, 8))

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
            actions, text="✗", width=40, height=26,
            command=lambda: _set("confirmed"),
            fg_color="#5c3030", hover_color="#7a4040", text_color=C["text"],
            font=FONT_SM,
        ).pack(side="left", padx=(0, 3))
        ctk.CTkButton(
            actions, text="✓", width=40, height=26,
            command=lambda: _set("correct"),
            fg_color="#2a4a38", hover_color="#356348", text_color=C["text"],
            font=FONT_SM,
        ).pack(side="left", padx=(0, 3))
        ctk.CTkButton(
            actions, text="Skip", width=48, height=26,
            command=lambda: _set("skip"),
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["muted"],
            border_width=1, border_color=C["border"], font=FONT_SM,
        ).pack(side="left")
        return card

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
        # Verdict chips count full analyze set (not just current Show slice)
        source = list(self._misclass_results or [])

        n_photo = 0
        n_conf = n_ok = n_un = 0
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
            elif v == "unreviewed":
                n_un += 1

        if hasattr(self, "report_m_total"):
            pool_n = len(pool)
            self.report_m_total.configure(
                text=f"This sheet: {pool_n:,} · page: {len(page_items):,}"
            )
            self.report_m_photo.configure(text=f"With photo: {n_photo:,}")
            self.report_m_confirmed.configure(text=f"Incorrect: {n_conf:,}")
            self.report_m_correct.configure(text=f"Correct: {n_ok:,}")
            self.report_m_unreviewed.configure(text=f"Unconfirmed: {n_un:,}")

    def _reports_export_source(self) -> list:
        """Full filtered pool for export (race toggles apply; not just current page)."""
        pool = list(getattr(self, "_report_pool", None) or [])
        if pool:
            return pool
        if self._misclass_results:
            return self._reports_filtered_source()
        return list(self._report_items or [])

    def _reports_iter_export_rows(self, *, verdicts: Optional[set] = None):
        """Yield (mc, verdict, rec) for export from full race-filtered pool."""
        for mc in self._reports_export_source():
            verdict = self._verdict_for_mc(mc)
            if verdicts is not None and verdict not in verdicts:
                continue
            yield mc, verdict, dict(mc.record or {})

    def _reports_export_csv(self):
        source = self._reports_export_source()
        if not source:
            messagebox.showinfo("Export", "Build a report list first.")
            return
        races = ", ".join(sorted(self._reports_race_buckets_allowed())) or "all"
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=f"misclass_report_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        )
        if not path:
            return
        n = 0
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "verdict", "first_name", "middle_name", "last_name", "name",
                "recorded_race", "likely_ethnicity", "confidence",
                "crime", "state", "matching_names", "photo_path", "source_url", "id",
            ])
            for mc, verdict, rec in self._reports_iter_export_rows():
                first = (rec.get("first_name") or "").strip()
                middle = (rec.get("middle_name") or "").strip()
                last = (rec.get("last_name") or "").strip()
                name = (
                    " ".join(p for p in (first, middle, last) if p)
                    or (rec.get("full_name") or "")
                )
                w.writerow([
                    verdict,
                    first,
                    middle,
                    last,
                    name,
                    mc.expected_race,
                    mc.likely_ethnicity,
                    f"{mc.confidence:.4f}",
                    self._reports_summarize_crime(self._reports_crime_text(rec), max_len=200),
                    _format_state_display(rec),
                    "; ".join(mc.matching_names or []),
                    rec.get("photo_path") or "",
                    rec.get("source_url") or "",
                    rec.get("id") or "",
                ])
                n += 1
        messagebox.showinfo(
            "Exported",
            f"{n} rows (race: {races}) → {path}",
        )
        self.log_queue.put(f"Reports CSV: {n} rows (race: {races}) → {path}")

    def _reports_open_html(self):
        """Build HTML gallery (grid or list) for the current report pool."""
        source = self._reports_export_source()
        if not source:
            messagebox.showinfo("Open HTML", "Build a report list first.")
            return

        races = ", ".join(sorted(self._reports_race_buckets_allowed())) or "all"
        only = messagebox.askyesnocancel(
            "Open HTML",
            "Include only Confirmed incorrect rows?\n\n"
            f"Race filter: {races} · Show filter: "
            f"{(self.report_verdict_filter.get() or 'Unconfirmed').strip()}\n"
            "(full pool for that Show/race filter, not just this page)\n\n"
            "Yes = confirmed incorrect only\n"
            "No = everyone in the current Show pool\n"
            "Cancel = abort",
        )
        if only is None:
            return
        verdict_filter = {"confirmed"} if only else None

        # Layout: follow Reports Grid/List control; still confirm so HTML can differ
        prefer_grid = self._reports_is_grid()
        compact = messagebox.askyesno(
            "HTML layout",
            "Use multi-column photo grid?\n\n"
            f"(Reports view is currently {'Grid' if prefer_grid else 'List'})\n\n"
            "Yes = compact mugshot grid (recommended)\n"
            "No = full-width list cards",
        )
        # askyesno: Yes=True → grid; No=False → list

        rows = list(self._reports_iter_export_rows(verdicts=verdict_filter))
        if not rows:
            messagebox.showinfo("Open HTML", "No rows for that selection.")
            return

        out_dir = Path("data") / "reports"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            out_dir = Path(".")
        path = out_dir / (
            f"misclass_report_{'grid' if compact else 'list'}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        )

        meta = getattr(self, "_misclass_meta", {}) or {}
        eth_f = meta.get("eth_filter", "all")
        min_c = meta.get("min_conf", "")
        generated = datetime.now().strftime("%Y-%m-%d %H:%M")
        layout = "grid" if compact else "list"

        def _esc(s: Any) -> str:
            t = str(s if s is not None else "")
            return (
                t.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )

        def _file_uri(p: str) -> str:
            try:
                return Path(p).resolve().as_uri()
            except Exception:
                return ""

        cards_html: List[str] = []
        for i, (mc, verdict, rec) in enumerate(rows, 1):
            first = (rec.get("first_name") or "").strip()
            middle = (rec.get("middle_name") or "").strip()
            last = (rec.get("last_name") or "").strip()
            full = str(rec.get("full_name") or "")
            if compact:
                name = self._reports_grid_display_name(
                    first, middle, last, full, max_len=40
                )
            else:
                name = (
                    " ".join(p for p in (first, middle, last) if p)
                    or full
                    or "—"
                )
            state = _format_state_display(rec)
            photo = (rec.get("photo_path") or "").strip()
            has_photo = photo and Path(photo).is_file()
            img_html = (
                f'<img src="{_esc(_file_uri(photo))}" alt="{_esc(name)}" loading="lazy">'
                if has_photo
                else '<div class="nophoto">No photo</div>'
            )
            url = (rec.get("source_url") or "").strip()
            link = (
                f'<a class="ext" href="{_esc(url)}" target="_blank" rel="noopener">Source</a>'
                if url else ""
            )
            vclass = _esc(verdict)
            race_disp = _format_race_display(mc.expected_race) or (mc.expected_race or "—")
            race = _esc(str(race_disp).upper())
            eth = _esc(mc.likely_ethnicity)
            conf = f"{float(mc.confidence or 0):.2f}"
            crime = self._reports_crime_text(rec)
            # Reports HTML always uses summarized crime; full text only in Misclassify
            crime_short = self._reports_summarize_crime(
                crime, max_len=140 if compact else 180
            )
            crime_html = (
                f'<p class="crime" title="{_esc(crime)}">'
                f'<span class="crime-label">Crime</span> {_esc(crime_short)}</p>'
                if crime_short
                else '<p class="crime"><span class="crime-label">Crime</span> —</p>'
            )
            df = rec.get("_deepface") or {}
            face_bit = ""
            if df:
                flab = df.get("predicted_label") or df.get("top_label") or ""
                fconf = df.get("top_confidence")
                if flab:
                    try:
                        face_bit = f" · face {flab}@{float(fconf):.0%}"
                    except (TypeError, ValueError):
                        face_bit = f" · face {flab}"
            badge = _esc(self._reports_verdict_label_short(verdict))
            if compact:
                cards_html.append(
                    f"""
<article class="card v-{vclass}">
  <div class="photo">{img_html}</div>
  <div class="body">
    <h2 title="{_esc(name)}">{_esc(name)}</h2>
    <div class="listed-as" title="Registry-listed race">
      <span class="listed-label">LISTED AS</span>
      <span class="listed-race">{race}</span>
    </div>
    {crime_html}
    <p class="meta">{_esc(state)} · {conf}{ _esc(face_bit) } · #{i}</p>
    <p class="badge-line">{badge}</p>
    {link}
  </div>
</article>"""
                )
            else:
                cards_html.append(
                    f"""
<article class="card v-{vclass}">
  <div class="photo">{img_html}</div>
  <div class="body">
    <header>
      <h2>{_esc(name)}</h2>
      <span class="idx">#{i} / {len(rows)}</span>
      <span class="badge">{badge}</span>
    </header>
    <div class="listed-as" title="Registry-listed race">
      <span class="listed-label">LISTED AS</span>
      <span class="listed-race">{race}</span>
    </div>
    {crime_html}
    <p class="vs-eth">vs surname ethnicity: <strong>{eth}</strong></p>
    <p class="meta">Confidence {conf} · State {_esc(state)}{(' · Middle: ' + _esc(middle)) if middle else ''}{_esc(face_bit)}</p>
    <p class="names">Matched: {_esc('; '.join(mc.matching_names[:5]) if mc.matching_names else '—')}</p>
    {link}
  </div>
</article>"""
                )

        n_conf = sum(1 for _, v, _ in rows if v == "confirmed")
        layout_css = (
            """
  main {
    max-width: 1500px; margin: 0 auto; padding: .85rem 1rem 2.5rem;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: .75rem;
  }
  .card {
    display: flex; flex-direction: column; gap: .4rem;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 12px; padding: .55rem; align-items: stretch;
    min-width: 0;
  }
  .card.v-confirmed { border-color: #8a4040; box-shadow: inset 0 0 0 1px #8a404055; }
  .card.v-correct { border-color: #3a6a50; }
  .card.v-skip { border-color: #4a4a55; opacity: .9; }
  .photo { width: 100%; }
  .photo img {
    width: 100%; aspect-ratio: 4/5; height: auto; object-fit: cover;
    border-radius: 8px; background: #101014; display: block;
  }
  .nophoto {
    width: 100%; aspect-ratio: 4/5; border-radius: 8px; background: #101014;
    display: flex; align-items: center; justify-content: center;
    color: var(--dim); font-size: .75rem;
  }
  .body { min-width: 0; }
  .body h2 {
    margin: 0; font-size: .9rem; font-weight: 650; line-height: 1.25;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .listed-as {
    margin: .3rem 0 .15rem; padding: .4rem .5rem .45rem;
    background: #5c1f1f; border: 2px solid var(--danger);
    border-radius: 8px; text-align: center;
  }
  .listed-label {
    display: block; font-size: .62rem; font-weight: 700;
    letter-spacing: .08em; color: #f0b0b0; margin-bottom: .1rem;
  }
  .listed-race {
    display: block; font-size: 1.05rem; font-weight: 800;
    line-height: 1.15; color: #fff; letter-spacing: .02em;
    word-break: break-word;
  }
  .crime {
    margin: .2rem 0 0; color: var(--text); font-size: .72rem;
    line-height: 1.3; white-space: normal; overflow: visible;
    word-break: break-word;
  }
  .crime-label {
    display: block; font-size: .62rem; font-weight: 700;
    letter-spacing: .06em; text-transform: uppercase; color: var(--muted);
    margin-bottom: .08rem;
  }
  .meta { margin: .15rem 0 0; color: var(--dim); font-size: .72rem; }
  .badge-line { margin: .1rem 0 0; color: var(--muted); font-size: .72rem; }
  .v-confirmed .badge-line { color: var(--danger); }
  .v-correct .badge-line { color: var(--success); }
  a.ext { color: var(--accent); font-size: .72rem; }
  @media (min-width: 1200px) {
    main { grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }
  }
  @media (max-width: 520px) {
    main { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: .5rem; }
  }
  @media print {
    header.page { position: static; }
    main { grid-template-columns: repeat(4, 1fr); gap: .4rem; }
    .card { break-inside: avoid; }
    .listed-as { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  }
"""
            if compact
            else """
  main {
    max-width: 920px; margin: 0 auto; padding: 1.25rem 1rem 3rem;
    display: flex; flex-direction: column; gap: .85rem;
  }
  .card {
    display: grid; grid-template-columns: 120px 1fr; gap: 1rem;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 14px; padding: 1rem; align-items: start;
  }
  .card.v-confirmed { border-color: #8a4040; }
  .card.v-correct { border-color: #3a6a50; }
  .photo img {
    width: 112px; height: 140px; object-fit: cover; border-radius: 10px;
    background: #101014; display: block;
  }
  .nophoto {
    width: 112px; height: 140px; border-radius: 10px; background: #101014;
    display: flex; align-items: center; justify-content: center;
    color: var(--dim); font-size: .85rem;
  }
  .body header { display: flex; flex-wrap: wrap; align-items: baseline; gap: .5rem .75rem; }
  .body h2 { margin: 0; font-size: 1.2rem; font-weight: 650; }
  .idx { color: var(--dim); font-size: .85rem; }
  .badge {
    margin-left: auto; font-size: .75rem; text-transform: uppercase;
    letter-spacing: .04em; color: var(--muted); border: 1px solid var(--border);
    border-radius: 999px; padding: .15rem .55rem;
  }
  .v-confirmed .badge { color: var(--danger); border-color: #8a4040; }
  .v-correct .badge { color: var(--success); border-color: #3a6a50; }
  .listed-as {
    margin: .85rem 0 .45rem; padding: .65rem 1rem .75rem;
    background: #5c1f1f; border: 2px solid var(--danger);
    border-radius: 12px;
  }
  .listed-label {
    display: block; font-size: .78rem; font-weight: 700;
    letter-spacing: .1em; color: #f0b0b0; margin-bottom: .15rem;
  }
  .listed-race {
    display: block; font-size: 2rem; font-weight: 800;
    line-height: 1.1; color: #fff; letter-spacing: .03em;
  }
  .vs-eth {
    margin: .15rem 0 .35rem; color: var(--muted); font-size: .95rem;
  }
  .vs-eth strong { color: var(--text); font-weight: 650; }
  .crime {
    margin: .35rem 0 .45rem; padding: .5rem .75rem;
    background: #1a1a20; border-radius: 8px; border: 1px solid var(--border);
    color: var(--text); font-size: .92rem; line-height: 1.35;
  }
  .crime-label {
    display: block; font-size: .72rem; font-weight: 700;
    letter-spacing: .08em; text-transform: uppercase; color: var(--muted);
    margin-bottom: .2rem;
  }
  .meta, .names { margin: .2rem 0; color: var(--muted); font-size: .9rem; }
  a.ext { color: var(--accent); font-size: .88rem; }
  @media print {
    header.page { position: static; }
    .card { break-inside: avoid; }
    .listed-as { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  }
"""
        )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Misclassification report · {_esc(generated)}</title>
<style>
  :root {{
    --bg: #0c0c0e; --panel: #1a1a20; --elev: #22222a; --border: #2e2e38;
    --text: #ececf1; --muted: #9b9ba8; --dim: #6b6b78; --accent: #e8a87c;
    --danger: #e07a7a; --success: #7dcea0;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: "Segoe UI", system-ui, sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.45;
  }}
  header.page {{
    position: sticky; top: 0; z-index: 10;
    background: rgba(12,12,14,.92); backdrop-filter: blur(10px);
    border-bottom: 1px solid var(--border);
    padding: 1rem 1.5rem 1.1rem;
  }}
  header.page h1 {{ margin: 0 0 .35rem; font-size: 1.35rem; font-weight: 650; }}
  header.page p {{ margin: 0; color: var(--muted); font-size: .92rem; }}
{layout_css}
</style>
</head>
<body class="layout-{layout}">
<header class="page">
  <h1>Misclassification review</h1>
  <p>
    Generated {_esc(generated)} · filter {_esc(eth_f)} · min conf {_esc(min_c)}
    · race {_esc(races)} · {len(rows)} people · {n_conf} confirmed
    · layout: {_esc(layout)}
  </p>
</header>
<main>
{"".join(cards_html)}
</main>
</body>
</html>
"""
        try:
            Path(path).write_text(html, encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Open HTML", f"Could not write report:\n{e}")
            return

        self.log_queue.put(
            f"Reports HTML open ({layout}): {len(rows)} cards (race: {races}) → {path}"
        )
        if hasattr(self, "report_status"):
            try:
                self.report_status.configure(
                    text=f"Opened HTML ({layout}) · {len(rows):,} cards · {path}"
                )
            except Exception:
                pass

        opened = False
        try:
            if hasattr(self, "_open_path"):
                self._open_path(Path(path))
                opened = True
        except Exception:
            opened = False
        if not opened:
            try:
                webbrowser.open(Path(path).resolve().as_uri())
                opened = True
            except Exception:
                pass
        if not opened:
            try:
                os.startfile(str(Path(path).resolve()))  # type: ignore[attr-defined]
                opened = True
            except Exception as e:
                messagebox.showerror(
                    "Open HTML",
                    f"Wrote {path} but could not open the browser:\n{e}",
                )

    # -----------------------------------------------------------------------
    # NSOPW
    # -----------------------------------------------------------------------
