"""ECsv"""
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


class ReportsExportCsvMixin:
    def _reports_export_source(self) -> list:
        """Full filtered pool for export (race toggles apply; not just current page)."""
        pool = list(getattr(self, "_report_pool", None) or [])
        if pool:
            return pool
        if getattr(self, "_report_analyze_results", None):
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
        races = (
            f"listed={self._reports_listed_filter_value()} "
            f"actual={self._reports_actual_filter_value()}"
        )
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
                "confidence_combined",
                "crime", "state", "matching_names", "photo_path", "source_url", "id",
            ])
            from scraper.confidence_display import (
                combine_name_face_confidence,
                format_display_confidence,
            )

            for mc, verdict, rec in self._reports_iter_export_rows():
                first = (rec.get("first_name") or "").strip()
                middle = (rec.get("middle_name") or "").strip()
                last = (rec.get("last_name") or "").strip()
                name = (
                    " ".join(p for p in (first, middle, last) if p)
                    or (rec.get("full_name") or "")
                )
                try:
                    name_c = float(
                        rec.get("_misclass_name_conf")
                        if rec.get("_misclass_name_conf") is not None
                        else mc.confidence
                        or 0
                    )
                except (TypeError, ValueError):
                    name_c = 0.0
                disp, is_comb = combine_name_face_confidence(
                    name_c,
                    name_ethnicity=str(mc.likely_ethnicity or ""),
                    deepface=rec.get("_deepface") if isinstance(rec.get("_deepface"), dict) else None,
                )
                w.writerow([
                    verdict,
                    first,
                    middle,
                    last,
                    name,
                    mc.expected_race,
                    mc.likely_ethnicity,
                    format_display_confidence(disp, is_comb, digits=4),
                    "yes" if is_comb else "no",
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


