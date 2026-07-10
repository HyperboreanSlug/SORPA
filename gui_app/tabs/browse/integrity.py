"""Browse → Integrity sub-tab (enrich/requeue)."""
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



class IntegrityTabMixin:
    def _build_integrity(self, tab):
        """
        Integrity layout:
          - Management / requeue pinned at bottom (always visible)
          - Middle area scrolls (summary + state table)
        """
        tab.configure(fg_color=C["surface"])

        # --- Pinned management panel (pack bottom first so it stays visible) ---
        right = _card(tab)
        right.pack(side="bottom", fill="x", padx=12, pady=(4, 8))
        head = ctk.CTkFrame(right, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(10, 4))
        _section_label(head, "Integrity management · requeue incomplete reports").pack(
            side="left"
        )
        self.requeue_incomplete_label = ctk.CTkLabel(
            head, text="", font=FONT_SM, text_color=C["muted"],
        )
        self.requeue_incomplete_label.pack(side="right")
        _muted(
            right,
            "Re-downloads report pages for DB rows that have a source URL but are missing "
            "selected fields (race / crime / photo / HTML). Updates records in place.",
        ).pack(anchor="w", padx=14, pady=(0, 6))

        self.requeue_need_race = ctk.BooleanVar(value=True)
        self.requeue_need_crime = ctk.BooleanVar(value=True)
        self.requeue_need_photo = ctk.BooleanVar(value=True)
        self.requeue_need_html = ctk.BooleanVar(value=False)
        chk_row = ctk.CTkFrame(right, fg_color="transparent")
        chk_row.pack(fill="x", padx=12, pady=2)
        for text, var in (
            ("Missing race", self.requeue_need_race),
            ("Missing crime", self.requeue_need_crime),
            ("Missing photo", self.requeue_need_photo),
            ("Missing HTML", self.requeue_need_html),
        ):
            ctk.CTkCheckBox(
                chk_row, text=text, variable=var, font=FONT_SM, text_color=C["text"],
                fg_color=C["accent"], hover_color=C["accent_hover"],
                checkmark_color=C["bg"], border_color=C["border"],
            ).pack(side="left", padx=(0, 14))

        lim_row = ctk.CTkFrame(right, fg_color="transparent")
        lim_row.pack(fill="x", padx=12, pady=(6, 12))
        ctk.CTkLabel(lim_row, text="Max rows", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(0, 6)
        )
        self.requeue_limit_var = ctk.IntVar(value=50)
        ctk.CTkEntry(
            lim_row, textvariable=self.requeue_limit_var, width=70,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left")
        ctk.CTkLabel(lim_row, text="Delay (s)", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(12, 6)
        )
        self.requeue_delay_var = ctk.DoubleVar(value=0.75)
        ctk.CTkEntry(
            lim_row, textvariable=self.requeue_delay_var, width=60,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left")
        self.requeue_btn = ctk.CTkButton(
            lim_row, text="Requeue incomplete", height=32, width=150,
            command=self._start_requeue,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        )
        self.requeue_btn.pack(side="left", padx=(16, 8))
        self.requeue_cancel_btn = ctk.CTkButton(
            lim_row, text="Cancel", height=32, width=80, command=self._cancel_requeue,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"], state="disabled",
        )
        self.requeue_cancel_btn.pack(side="left", padx=(0, 10))
        self.requeue_status = ctk.CTkLabel(
            lim_row, text="Idle", font=FONT_SM, text_color=C["dim"],
        )
        self.requeue_status.pack(side="left")
        self.requeue_progress = ctk.CTkProgressBar(
            right, progress_color=C["accent"], fg_color=C["elevated"], height=6,
        )
        self.requeue_progress.pack(fill="x", padx=12, pady=(0, 10))
        self.requeue_progress.set(0)
        self._requeue_cancel = False

        # --- Scrollable body: summary + by-state table ---
        scroll = ctk.CTkScrollableFrame(tab, fg_color=C["surface"])
        scroll.pack(side="top", fill="both", expand=True, padx=4, pady=(4, 0))
        self._integrity_scroll = scroll

        top = ctk.CTkFrame(scroll, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(6, 4))
        ctk.CTkButton(
            top, text="Refresh", width=100, command=self._refresh_integrity,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            top, text="Export report CSV…", width=140, command=self._export_integrity_csv,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            top, text="Check duplicates", width=130, command=self._check_duplicates,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            top, text="Remove duplicates…", width=140, command=self._remove_duplicates,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=4)
        self.integrity_status = ctk.CTkLabel(
            top, text="", font=FONT_SM, text_color=C["muted"],
        )
        self.integrity_status.pack(side="right", padx=8)

        summary = _card(scroll)
        summary.pack(fill="x", padx=8, pady=(0, 6))
        _section_label(summary, "Archive integrity").pack(anchor="w", padx=14, pady=(8, 2))
        _muted(
            summary,
            "TOTAL = records in that state.  RACE/CRIME/PHOTO/HTML % = share with that field filled. "
            "Management settings are pinned at the bottom of this tab.",
        ).pack(anchor="w", padx=14, pady=(0, 4))
        self.integrity_summary = ctk.CTkLabel(
            summary, text="Click Refresh to load stats.",
            font=FONT_SM, text_color=C["text"], anchor="w", justify="left",
        )
        self.integrity_summary.pack(fill="x", padx=14, pady=(0, 8))

        table_card = _card(scroll)
        table_card.pack(fill="x", padx=8, pady=(0, 12))
        _section_label(table_card, "By state").pack(anchor="w", padx=14, pady=(10, 4))
        wrap, self.integrity_tree = _tree_frame(table_card)
        wrap.pack(fill="x", padx=10, pady=(0, 12))
        # Fixed tall viewport so many states show; outer frame still scrolls
        wrap.configure(height=420)
        wrap.pack_propagate(False)
        icols = [
            "state", "total", "pct_race", "pct_crime", "pct_photo", "pct_html",
            "with_race", "with_crime", "with_photo", "with_html",
        ]
        self.integrity_tree.configure(columns=icols, show="headings", height=16)
        _stretch_columns(
            self.integrity_tree,
            icols,
            [80, 90, 100, 100, 100, 100, 110, 110, 110, 110],
        )
        _enable_tree_column_sort(
            self.integrity_tree,
            icols,
            labels={
                "state": "STATE",
                "total": "TOTAL",
                "pct_race": "RACE %",
                "pct_crime": "CRIME %",
                "pct_photo": "PHOTO %",
                "pct_html": "HTML %",
                "with_race": "RACE COUNT",
                "with_crime": "CRIME COUNT",
                "with_photo": "PHOTO COUNT",
                "with_html": "HTML COUNT",
            },
        )
        _bind_tree_scroll_isolation(self.integrity_tree, wrap)

        self.after(200, self._refresh_integrity)

    def _refresh_integrity(self):
        from scraper.database import Database

        try:
            db = Database(self.db_path)
            try:
                # One-shot fix for NSOPW junk location.state codes (e.g. YY → FL)
                try:
                    fixed_yy = db.repair_bogus_states()
                    if fixed_yy:
                        self.log_queue.put(
                            f"Repaired {fixed_yy:,} rows with bogus state codes (YY/XX/…)"
                        )
                except Exception:
                    pass
                # Pull middle names from full_name / multi-token first / raw JSON
                try:
                    mid = db.backfill_middle_names()
                    if mid.get("updated"):
                        self.log_queue.put(
                            f"Backfilled middle_name on {mid['updated']:,} rows "
                            f"(scanned {mid['scanned']:,})"
                        )
                except Exception:
                    pass
                report = db.get_integrity_report()
                incomplete = db.find_incomplete_reports(
                    need_race=True, need_crime=True, need_photo=True, need_html=False,
                    limit=5000,
                )
                try:
                    from scraper.database import DEFAULT_DEDUPE_STRATEGIES

                    dup_summary = db.count_duplicates(list(DEFAULT_DEDUPE_STRATEGIES))
                except Exception:
                    dup_summary = None
            finally:
                db.close()
        except Exception as e:
            self.integrity_summary.configure(text=f"Error: {e}")
            return

        o = report["overall"]
        complete = int(o.get("with_everything") or 0)
        total = int(o.get("total") or 0)
        dup_line = ""
        if dup_summary and isinstance(dup_summary.get("by_strategy"), dict):
            parts = []
            for s, info in dup_summary["by_strategy"].items():
                safe_e = int(info.get("safe_extra_rows") or 0)
                unsafe_g = int(info.get("unsafe_groups") or 0)
                if safe_e or unsafe_g or info.get("extra_rows"):
                    bit = f"{s}: {safe_e:,} safe"
                    if unsafe_g:
                        bit += f" (+{unsafe_g} portal/CAPTCHA clusters skipped)"
                    parts.append(bit)
            if parts:
                dup_line = "\nDuplicates: " + " · ".join(parts)
            else:
                dup_line = "\nDuplicates: none found (URL / external id / name+DOB / multi-state)"
        self.integrity_summary.configure(
            text=(
                f"Total records: {total:,}  ·  "
                f"Complete (race+crime+photo+HTML): {complete:,} "
                f"({o.get('pct_everything', 0)}%)\n"
                f"Race: {o['with_race']:,} ({o.get('pct_race', 0)}%)  ·  "
                f"Crime: {o['with_crime']:,} ({o.get('pct_crime', 0)}%)  ·  "
                f"Photo: {o['with_photo']:,} ({o.get('pct_photo', 0)}%)  ·  "
                f"HTML: {o['with_html']:,} ({o.get('pct_html', 0)}%)"
                f"{dup_line}"
            )
        )
        self.requeue_incomplete_label.configure(
            text=f"Incomplete with URL (race/crime/photo): {len(incomplete):,}"
        )
        self.integrity_tree.delete(*self.integrity_tree.get_children())
        for st in report["by_state"]:
            self.integrity_tree.insert(
                "",
                "end",
                values=(
                    st["state"],
                    st["total"],
                    f"{st['pct_race']:.0f}%",
                    f"{st['pct_crime']:.0f}%",
                    f"{st['pct_photo']:.0f}%",
                    f"{st['pct_html']:.0f}%",
                    st["with_race"],
                    st["with_crime"],
                    st["with_photo"],
                    st["with_html"],
                ),
            )
        n_states = max(8, len(report["by_state"]))
        self.integrity_tree.configure(height=min(24, max(12, n_states + 2)))

        self.integrity_status.configure(
            text=f"Updated · {len(report['by_state'])} states/territories in DB"
        )
        self._last_integrity_report = report

    def _export_integrity_csv(self):
        report = getattr(self, "_last_integrity_report", None)
        if not report:
            self._refresh_integrity()
            report = getattr(self, "_last_integrity_report", None)
        if not report:
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(
                    f,
                    fieldnames=[
                        "state", "total", "with_race", "pct_race", "with_crime", "pct_crime",
                        "with_photo", "pct_photo", "with_html", "pct_html", "with_url",
                    ],
                )
                w.writeheader()
                for row in report["by_state"]:
                    w.writerow(row)
            messagebox.showinfo("Exported", path)
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    def _refresh_header_db_path(self):
        """Show active SQLite path in the header."""
        try:
            p = Path(self.db_path)
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            else:
                p = p.resolve()
            # Prefer short relative path when under project
            try:
                show = str(p.relative_to(Path.cwd()))
            except ValueError:
                show = str(p)
            if len(show) > 52:
                show = "…" + show[-50:]
            n = ""
            try:
                from scraper.database import Database
                db = Database(self.db_path)
                try:
                    n = f"  ·  {db.get_total_count():,} records"
                finally:
                    db.close()
            except Exception:
                pass
            if hasattr(self, "header_db_label"):
                self.header_db_label.configure(text=f"DB: {show}{n}")
        except Exception:
            if hasattr(self, "header_db_label"):
                self.header_db_label.configure(text=f"DB: {self.db_path}")

    def _open_data_folder_header(self):
        path = Path("data")
        path.mkdir(parents=True, exist_ok=True)
        # Prefer folder containing the DB
        try:
            dbp = Path(self.db_path)
            if dbp.parent.is_dir():
                path = dbp.parent
        except Exception:
            pass
        self._open_path(path)

    def _cancel_requeue(self):
        self._requeue_cancel = True
        self._enrich_cancel = True
        try:
            self.requeue_status.configure(text="Cancelling…")
        except Exception:
            pass

    def _start_enrich_misclassified(self):
        """NSOPW search + report fetch for current misclassification candidates."""
        if self.is_running:
            messagebox.showwarning("Busy", "Wait for the current job to finish.")
            return

        from scraper.nsopw_builder import NSOPWEthnicDatabaseBuilder

        results = list(self._misclass_results or [])
        items = list(self._report_items or [])
        if items:
            records = []
            for mc in items:
                if self._verdict_for_mc(mc) == "correct":
                    continue
                rec = dict(mc.record or {})
                if rec.get("id") is not None:
                    records.append(rec)
            source_label = "Reports list (excl. Correct)"
        else:
            records = []
            for mc in results:
                rec = dict(mc.record or {})
                if rec.get("id") is not None:
                    records.append(rec)
            source_label = "last Analyze results"

        if not records:
            messagebox.showinfo(
                "NSOPW enrich",
                "No misclassified people to enrich.\n\n"
                "Run Analyze (or Reports → Analyze & build) first.",
            )
            return

        # Only rows still missing photo / race / crime / URL
        incomplete = [
            r for r in records
            if NSOPWEthnicDatabaseBuilder.record_needs_enrichment(r)
        ]
        n_complete = len(records) - len(incomplete)
        if not incomplete:
            messagebox.showinfo(
                "NSOPW enrich",
                f"All {len(records):,} candidates already have photo + race + crime + URL.\n"
                "Nothing to look up.",
            )
            return

        try:
            enrich_lim = int(self.enrich_limit_var.get()) if hasattr(self, "enrich_limit_var") else 25
        except (TypeError, ValueError):
            enrich_lim = 25
        if enrich_lim <= 0:
            # 0 = no cap (still hard-cap to avoid runaway API use)
            enrich_lim = min(len(incomplete), 500)
        else:
            enrich_lim = max(1, min(enrich_lim, 500))

        ok = messagebox.askyesno(
            "NSOPW enrich misclassified?",
            (
                f"Source: {source_label}\n"
                f"Candidates: {len(records):,} · incomplete (need data): {len(incomplete):,}\n"
                f"Already complete (skipped): {n_complete:,}\n"
                f"Lookup limit: {enrich_lim}\n\n"
                "Only people missing photo, race, crime, or source URL are processed.\n"
                "Prefer missing photos first.\n\n"
                "For each incomplete person:\n"
                "  • If they have a report URL → re-fetch photo/race/crime\n"
                "  • Else → NSOPW first+last search, attach best match, fetch report\n\n"
                "Existing DB rows are updated (no new duplicates).\n"
                "Rate-limited — watch the Activity log on NSOPW/Scrape.\n\n"
                "Continue?"
            ),
        )
        if not ok:
            return

        self._enrich_cancel = False
        self._requeue_cancel = False
        self._set_running(True)
        if hasattr(self, "requeue_btn"):
            self.requeue_btn.configure(state="disabled")
        if hasattr(self, "requeue_cancel_btn"):
            self.requeue_cancel_btn.configure(state="normal")
        if hasattr(self, "requeue_status"):
            self.requeue_status.configure(text="NSOPW enrich running…")
        if hasattr(self, "requeue_progress"):
            self.requeue_progress.set(0)
        if hasattr(self, "report_status"):
            self.report_status.configure(text="NSOPW enrich running… see Activity log")

        def log(msg):
            self.log_queue.put(msg)

        def on_progress(done: int, total: int):
            frac = (done / total) if total else 0.0

            def _ui(d=done, t=total, f=frac):
                if hasattr(self, "requeue_progress"):
                    self.requeue_progress.set(min(1.0, max(0.0, f)))
                if hasattr(self, "requeue_status"):
                    self.requeue_status.configure(text=f"NSOPW enrich {d}/{t}…")

            self.after(0, _ui)

        def worker():
            from scraper.nsopw_builder import NSOPWEthnicDatabaseBuilder

            builder = NSOPWEthnicDatabaseBuilder(
                db_path=self.db_path,
                delay=2.0,
                report_delay=0.75,
                html_dir="data/report_pages",
                cancel_check=lambda: getattr(self, "_enrich_cancel", False)
                or getattr(self, "_requeue_cancel", False),
            )
            try:
                summary = builder.enrich_misclassified(
                    incomplete,
                    limit=enrich_lim,
                    prefer_missing_photo=True,
                    only_missing_data=True,
                    enrich_reports=True,
                    save_html=True,
                    log=log,
                    on_progress=on_progress,
                )

                def done():
                    self._set_running(False)
                    if hasattr(self, "requeue_btn"):
                        self.requeue_btn.configure(state="normal")
                    if hasattr(self, "requeue_cancel_btn"):
                        self.requeue_cancel_btn.configure(state="disabled")
                    msg = (
                        f"NSOPW enrich: updated {summary.get('updated', 0)}/"
                        f"{summary.get('attempted', 0)} "
                        f"· matched {summary.get('nsopw_matched', 0)} "
                        f"· photos {summary.get('with_photo', 0)} "
                        f"· skipped complete {summary.get('skipped_complete', 0)} "
                        f"· errors {summary.get('errors', 0)}"
                    )
                    if hasattr(self, "requeue_status"):
                        self.requeue_status.configure(text=msg)
                    if hasattr(self, "report_status"):
                        self.report_status.configure(
                            text=msg + " · re-run Analyze & build"
                        )
                    if hasattr(self, "requeue_progress"):
                        self.requeue_progress.set(1.0)
                    self.log_queue.put(msg)
                    try:
                        self._after_db_data_changed()
                    except Exception:
                        pass
                    messagebox.showinfo("NSOPW enrich", msg)

                self.after(0, done)
            except Exception as e:
                err = str(e)

                def fail():
                    self._set_running(False)
                    if hasattr(self, "requeue_btn"):
                        self.requeue_btn.configure(state="normal")
                    if hasattr(self, "requeue_cancel_btn"):
                        self.requeue_cancel_btn.configure(state="disabled")
                    messagebox.showerror("NSOPW enrich failed", err)

                self.after(0, fail)
            finally:
                try:
                    builder.close()
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _start_requeue(self):
        if self.is_running:
            messagebox.showwarning("Busy", "Wait for the current job to finish.")
            return
        try:
            limit = max(1, int(self.requeue_limit_var.get()))
            delay = max(0.25, float(self.requeue_delay_var.get()))
        except (TypeError, ValueError):
            limit, delay = 50, 0.75

        need_race = bool(self.requeue_need_race.get())
        need_crime = bool(self.requeue_need_crime.get())
        need_photo = bool(self.requeue_need_photo.get())
        need_html = bool(self.requeue_need_html.get())
        if not any((need_race, need_crime, need_photo, need_html)):
            messagebox.showwarning("Nothing selected", "Enable at least one missing field.")
            return

        self._requeue_cancel = False
        self._set_running(True)
        self.requeue_btn.configure(state="disabled")
        self.requeue_cancel_btn.configure(state="normal")
        self.requeue_status.configure(text="Requeue running…")
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
            )
            try:
                summary = builder.requeue_incomplete(
                    need_race=need_race,
                    need_crime=need_crime,
                    need_photo=need_photo,
                    need_html=need_html,
                    limit=limit,
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
                            f"errors {summary.get('errors', 0)}"
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

    # -----------------------------------------------------------------------
    # Misclassify
    # -----------------------------------------------------------------------
