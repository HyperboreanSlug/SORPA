"""SConfirm"""
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


class ReportsSourceConfirmMixin:
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
        """Run Analyze off UI (shared filters), then merge DeepFace hits + cards."""
        try:
            if hasattr(self, "_ensure_misclass_filter_vars"):
                self._ensure_misclass_filter_vars()
        except Exception as e:
            messagebox.showerror("Analyze & build", str(e))
            return
        if hasattr(self, "report_status"):
            try:
                self.report_status.configure(
                    text="Analyzing… (UI stays responsive)"
                )
            except Exception:
                pass

        def after_analyze() -> None:
            """Misclass done — build full report pool (DB) off the UI thread."""
            if hasattr(self, "report_status"):
                try:
                    self.report_status.configure(
                        text="Building report pool… (DeepFace merge)"
                    )
                except Exception:
                    pass

            def work():
                return self._reports_filtered_source()

            def done(result=None, error=None):
                if error is not None:
                    messagebox.showerror("Analyze & build", str(error))
                    return
                self._report_page = 0
                self._report_pool = list(result or [])
                if not self._report_pool:
                    messagebox.showinfo(
                        "Reports",
                        "No mismatches for the current filters.\n"
                        "• Run surname Analyze with lower min conf., or\n"
                        "• Run DeepFace → Scan first "
                        "(hits appear when “DeepFace hits” is checked).",
                    )
                    self._report_items = []
                    self._reports_rebuild_cards(refilter=False)
                    self._reports_update_metrics()
                    return
                self._reports_rebuild_cards(refilter=False)
                self._reports_update_metrics()

            if hasattr(self, "run_bg"):
                self.run_bg(work, done, name="reports-pool")
            else:
                try:
                    done(result=work(), error=None)
                except Exception as e:
                    done(result=None, error=e)

        try:
            self._run_misclassification(on_done=after_analyze)
        except Exception as e:
            messagebox.showerror("Analyze & build", str(e))


