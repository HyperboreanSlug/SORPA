"""IntegrityRequeueMixin."""
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


class IntegrityRequeueMixin:
    def _cancel_requeue(self):
        self._requeue_cancel = True
        self._enrich_cancel = True
        try:
            self.requeue_status.configure(text="Cancelling…")
        except Exception:
            pass


    def _start_requeue(self):
        if self.is_running:
            messagebox.showwarning("Busy", "Wait for the current job to finish.")
            return
        try:
            limit = max(1, int(self.requeue_limit_var.get()))
            delay = max(0.25, float(self.requeue_delay_var.get()))
            thr_var = getattr(self, "requeue_threads_var", None)
            threads = int(thr_var.get()) if thr_var is not None else 4
            threads = max(1, min(threads, 16))
        except (TypeError, ValueError):
            limit, delay, threads = 50, 0.75, 4

        need_race = bool(self.requeue_need_race.get())
        need_crime = bool(self.requeue_need_crime.get())
        need_photo = bool(self.requeue_need_photo.get())
        need_html = bool(self.requeue_need_html.get())
        if not any((need_race, need_crime, need_photo, need_html)):
            messagebox.showwarning("Nothing selected", "Enable at least one missing field.")
            return

        source_scope = (self.requeue_source_scope.get() or "all").strip().lower()
        ethnicity_filter = (self.requeue_ethnicity.get() or "all").strip().lower()
        self._ensure_misclass_filter_vars()
        try:
            min_conf = float(self.misclass_conf_var.get())
        except (TypeError, ValueError):
            min_conf = 0.5

        self._requeue_cancel = False
        self._set_running(True)
        self.requeue_btn.configure(state="disabled")
        self.requeue_cancel_btn.configure(state="normal")
        self.requeue_status.configure(text=f"Requeue running ({threads} threads)…")
        self.requeue_progress.set(0)
        self.requeue_progress.configure(mode="determinate")

        def log(msg):
            self.log_queue.put(msg)

        def on_progress(done: int, total: int):
            frac = (done / total) if total else 0.0
            self.after(
                0,
                lambda d=done, t=total, f=frac: (
                    self.requeue_progress.set(min(1.0, max(0.0, f))),
                    self.requeue_status.configure(text=f"Requeue {d}/{t}…"),
                ),
            )

        def worker():
            from scraper.nsopw_builder import NSOPWEthnicDatabaseBuilder

            builder = NSOPWEthnicDatabaseBuilder(
                db_path=self.db_path,
                delay=2.0,
                report_delay=delay,
                html_dir="data/report_pages",
                cancel_check=lambda: self._requeue_cancel,
                report_threads=threads,
            )
            try:
                summary = builder.requeue_incomplete(
                    need_race=need_race,
                    need_crime=need_crime,
                    need_photo=need_photo,
                    need_html=need_html,
                    limit=limit,
                    source_scope=source_scope,
                    ethnicity_filter=ethnicity_filter,
                    min_confidence=min_conf,
                    save_html=True,
                    log=log,
                    on_progress=on_progress,
                )

                def done():
                    self._set_running(False)
                    self.requeue_btn.configure(state="normal")
                    self.requeue_cancel_btn.configure(state="disabled")
                    self.requeue_progress.set(1.0)
                    self.requeue_status.configure(
                        text=(
                            f"Done · queued {summary.get('queued', 0)} · "
                            f"updated {summary.get('updated', 0)} · "
                            f"skipped scope {summary.get('skipped_scope', 0)} · "
                            f"errors {summary.get('errors', 0)} · "
                            f"threads {summary.get('threads', threads)}"
                        )
                    )
                    self._refresh_integrity()
                    self._refresh_header_db_path()

                self.after(0, done)
            except Exception as e:
                log(f"Requeue ERROR: {e}")

                def fail():
                    self._set_running(False)
                    self.requeue_btn.configure(state="normal")
                    self.requeue_cancel_btn.configure(state="disabled")
                    self.requeue_progress.set(0)
                    self.requeue_status.configure(text=f"Error: {e}")

                self.after(0, fail)
            finally:
                builder.close()

        threading.Thread(target=worker, daemon=True).start()


