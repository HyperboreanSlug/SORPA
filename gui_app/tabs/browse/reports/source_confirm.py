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
            raw_n = len(getattr(self, "_misclass_results", None) or [])
            if hasattr(self, "report_status"):
                try:
                    self.report_status.configure(
                        text=(
                            f"Analyze found {raw_n:,} mismatches · "
                            "building report pool…"
                        )
                    )
                except Exception:
                    pass

            # Snapshot Tk filters on the main thread; worker must not touch widgets
            try:
                snap = self._reports_filter_snapshot()
                snap_all = dict(snap)
                snap_all["vfilter"] = "all"
            except Exception:
                snap = {
                    "photos_only": True,
                    "include_deepface": False,
                    "vfilter": "unreviewed",
                    "race_allow": {"White"},
                    "actual": "Non-white",
                    "listed": "White",
                }
                snap_all = dict(snap)
                snap_all["vfilter"] = "all"

            def work():
                base = self._reports_filtered_source(
                    verdict_key="all", snapshot=snap_all
                )
                vfilter = str(snap.get("vfilter") or "unreviewed")
                if vfilter == "all":
                    sheet = list(base)
                else:
                    sheet = [
                        mc
                        for mc in base
                        if self._reports_verdict_passes_filter(
                            self._verdict_for_mc(mc), vfilter
                        )
                    ]
                return {"base": base, "sheet": sheet, "snap": snap, "raw_n": raw_n}

            def done(result=None, error=None):
                if error is not None:
                    messagebox.showerror("Analyze & build", str(error))
                    return
                payload = result if isinstance(result, dict) else {}
                base = list(payload.get("base") or [])
                sheet = list(payload.get("sheet") or [])
                snap_used = payload.get("snap") or snap
                raw = int(payload.get("raw_n") or raw_n)
                self._report_page = 0
                self._report_metrics_base = base
                self._report_pool = sheet
                if not sheet:
                    listed = snap_used.get("listed") or "?"
                    photos = "on" if snap_used.get("photos_only") else "off"
                    show = snap_used.get("vfilter") or "?"
                    actual = snap_used.get("actual") or "All"
                    if raw <= 0:
                        msg = (
                            "Analyze found 0 surname mismatches.\n\n"
                            "On Misclassify / Statistics, try:\n"
                            "• lower Min conf. (e.g. 0.5)\n"
                            "• ethnicity = all\n"
                            "• Scan cap = 0 (entire DB) or a larger cap\n\n"
                            "Or enable DeepFace hits after running DeepFace → Scan."
                        )
                    elif base:
                        msg = (
                            f"Analyze found {raw:,} mismatches, and "
                            f"{len(base):,} match Listed/Photos/Actual — "
                            f"but 0 match Show={show}.\n\n"
                            "Switch Show to All (or Confirmed incorrect / "
                            "Confirmed correct) to see them."
                        )
                    else:
                        msg = (
                            f"Analyze found {raw:,} mismatches, but none match "
                            "the current Reports filters:\n"
                            f"• Listed as: {listed}\n"
                            f"• Photos only: {photos}\n"
                            f"• Actual: {actual}\n"
                            f"• Show: {show}\n\n"
                            "Try Listed as → All, turn Photos only off, "
                            "or Actual → All."
                        )
                    messagebox.showinfo("Reports", msg)
                    self._report_items = []
                    self._reports_rebuild_cards(refilter=False)
                    self._reports_update_metrics()
                    return
                self._reports_rebuild_cards(refilter=False)
                self._reports_update_metrics()
                if hasattr(self, "report_status"):
                    try:
                        self.report_status.configure(
                            text=(
                                f"Report ready · {len(sheet):,} on sheet "
                                f"· {len(base):,} in filter · "
                                f"{raw:,} analyzed"
                            )
                        )
                    except Exception:
                        pass

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


