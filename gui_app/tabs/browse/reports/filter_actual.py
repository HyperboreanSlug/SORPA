"""FActual"""
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


class ReportsFilterActualMixin:
    # Actual ethnicity buckets treated as white / white-coded for Non-white filter
    _ACTUAL_WHITE_BUCKETS = frozenset({"European", "Jewish", "Portuguese"})

    def _reports_listed_filter_value(self) -> str:
        """Selected Listed-as dropdown: All | White | Black | Other."""
        if hasattr(self, "report_listed_filter") and self.report_listed_filter is not None:
            try:
                v = (self.report_listed_filter.get() or "All").strip()
                if v in ("All", "White", "Black", "Other"):
                    return v
            except Exception:
                pass
        # Legacy checkboxes
        allow = set()
        if bool(getattr(self, "report_race_white", None) and self.report_race_white.get()):
            allow.add("White")
        if bool(getattr(self, "report_race_black", None) and self.report_race_black.get()):
            allow.add("Black")
        if bool(getattr(self, "report_race_other", None) and self.report_race_other.get()):
            allow.add("Other")
        if len(allow) == 1:
            return next(iter(allow))
        return "All"


    def _reports_actual_filter_value(self) -> str:
        """Selected Actual (likely ethnicity) dropdown. Default: Non-white."""
        if hasattr(self, "report_actual_filter") and self.report_actual_filter is not None:
            try:
                return (
                    (self.report_actual_filter.get() or "Non-white").strip()
                    or "Non-white"
                )
            except Exception:
                pass
        return "Non-white"


    def _reports_race_buckets_allowed(self) -> set:
        """Which registry-listed race buckets pass the Listed-as filter."""
        listed = self._reports_listed_filter_value()
        if listed == "All":
            return {"White", "Black", "Other"}
        if listed in ("White", "Black", "Other"):
            return {listed}
        return {"White", "Black", "Other"}


    @staticmethod
    def _reports_actual_bucket(label: str) -> str:
        """Map surname / face ethnicity to an Actual filter bucket."""
        e = (label or "").strip().lower()
        if not e or e in ("—", "-", "unknown", "other", "n/a"):
            return "Other"
        if "hispanic" in e or "latino" in e or "latina" in e:
            return "Hispanic"
        # Indic + MENA share one actual bucket (matches classifier branding)
        if (
            "indian" in e
            or "mena" in e
            or "south asian" in e
            or "desi" in e
            or "arab" in e
            or "middle east" in e
            or "middle_eastern" in e
        ):
            return "Indian/MENA"
        if (
            "african" in e
            or e in ("black", "b")
            or "afro" in e
        ):
            return "African American"
        if any(
            k in e
            for k in (
                "asian",
                "chinese",
                "korean",
                "vietnamese",
                "japanese",
                "filipino",
                "thai",
                "cambodian",
                "hmong",
            )
        ):
            return "Asian"
        if "jewish" in e or "israel" in e:
            return "Jewish"
        if "portuguese" in e or "brazil" in e:
            return "Portuguese"
        if "native" in e or "american indian" in e or "alaska" in e:
            return "Native American"
        if "european" in e or e in ("white", "caucasian", "w"):
            return "European"
        return "Other"


    def _reports_actual_label_for_mc(self, mc) -> str:
        """Best 'actual' ethnicity string for a hit (surname, then face)."""
        eth = (getattr(mc, "likely_ethnicity", None) or "").strip()
        if eth and eth not in ("—", "-", "Unknown", "unknown"):
            return eth
        rec = getattr(mc, "record", None) or {}
        for key in ("likely_ethnicity", "_misclass_likely"):
            v = (rec.get(key) or "").strip()
            if v and v not in ("—", "-", "Unknown", "unknown"):
                return v
        df = rec.get("_deepface") or {}
        lab = (df.get("predicted_label") or df.get("top_label") or "").strip()
        if lab:
            # Normalize face labels to display-ish form
            return lab.replace("_", " ").title()
        return eth or "Unknown"


    def _reports_actual_bucket_passes(self, got: str, want: str) -> bool:
        """Whether an Actual bucket passes the dropdown selection."""
        w = (want or "").strip() or "Non-white"
        if not w or w == "All":
            return True
        g = (got or "").strip() or "Other"
        if w in ("Non-white", "Non white", "non-white"):
            white = getattr(self, "_ACTUAL_WHITE_BUCKETS", None) or frozenset(
                {"European", "Jewish", "Portuguese"}
            )
            return g not in white
        return g == w

    def _reports_actual_passes(self, mc) -> bool:
        want = self._reports_actual_filter_value()
        got = self._reports_actual_bucket(self._reports_actual_label_for_mc(mc))
        return self._reports_actual_bucket_passes(got, want)


