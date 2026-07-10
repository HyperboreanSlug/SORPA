"""Scrape main tab."""
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



class ScrapeTabMixin:
    def _build_scrape(self, tab):
        tab.configure(fg_color=C["surface"])
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(12, 6))

        self.scrape_direct_only = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            top,
            text="Direct / bulk only",
            variable=self.scrape_direct_only,
            font=FONT_SM,
            text_color=C["text"],
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            checkmark_color=C["bg"],
            border_color=C["border"],
        ).pack(side="left", padx=(0, 12))

        ctk.CTkButton(
            top, text="Select all", width=100, command=self._scrape_select_all,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            top, text="Clear", width=80, command=self._scrape_clear_selection,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=4)

        mid_split = _hpaned(tab)
        mid_split.pack(fill="both", expand=True, padx=12, pady=6)
        self._scrape_split = mid_split

        left = _card(mid_split)
        right = _card(mid_split)
        mid_split.add(left, minsize=320, stretch="always")
        mid_split.add(right, minsize=220, stretch="never")
        self.after(150, lambda: self._set_sash(mid_split, 0, 0.72))

        _section_label(left, "Jurisdictions").pack(anchor="w", padx=14, pady=(12, 4))
        _muted(
            left,
            "INTERACTIVE: the site only offers a web search form (often disclaimer, CAPTCHA, "
            "or session). There is no public bulk download, so automated scrape returns no "
            "records — look up offenders in a browser. Prefer Direct / bulk only for "
            "jurisdictions that publish downloadable data (DIRECT, ARCGIS, etc.).",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        tree_wrap, self.scrape_tree = _tree_frame(left)
        tree_wrap.pack(fill="both", expand=True, padx=10, pady=(0, 12))
        self.scrape_tree.configure(columns=("abbr", "method", "notes"), show="tree headings", selectmode="extended")
        self.scrape_tree.heading("#0", text="Jurisdiction")
        self.scrape_tree.heading("abbr", text="Code")
        self.scrape_tree.heading("method", text="Method")
        self.scrape_tree.heading("notes", text="Notes")
        self.scrape_tree.column("#0", width=220, minwidth=80, stretch=True)
        self.scrape_tree.column("abbr", width=50, anchor="center", minwidth=40, stretch=False)
        self.scrape_tree.column("method", width=90, anchor="center", minwidth=60, stretch=False)
        self.scrape_tree.column("notes", width=280, minwidth=80, stretch=True)
        self.scrape_tree.bind("<<TreeviewSelect>>", self._scrape_on_select)
        self.scrape_tree.tag_configure("direct", background="#1a241c")
        _bind_tree_scroll_isolation(self.scrape_tree, tree_wrap)

        _section_label(right, "Options").pack(anchor="w", padx=14, pady=(12, 8))

        ctk.CTkLabel(right, text="Output folder", font=FONT_SM, text_color=C["muted"]).pack(
            anchor="w", padx=14
        )
        out_row = ctk.CTkFrame(right, fg_color="transparent")
        out_row.pack(fill="x", padx=14, pady=4)
        self.scrape_output_var = ctk.StringVar(value=str(Path("data/downloads")))
        ctk.CTkEntry(
            out_row, textvariable=self.scrape_output_var, fg_color=C["bg"],
            border_color=C["border"], text_color=C["text"],
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            out_row, text="…", width=36, command=self._scrape_browse_output,
            fg_color=C["elevated"], hover_color=C["border"],
        ).pack(side="left", padx=(6, 0))

        ctk.CTkLabel(right, text="Delay (seconds)", font=FONT_SM, text_color=C["muted"]).pack(
            anchor="w", padx=14, pady=(12, 0)
        )
        self.scrape_delay_var = ctk.DoubleVar(value=2.0)
        ctk.CTkSlider(
            right, from_=0.5, to=10.0, variable=self.scrape_delay_var,
            progress_color=C["accent"], button_color=C["accent"],
            button_hover_color=C["accent_hover"], fg_color=C["elevated"],
        ).pack(fill="x", padx=14, pady=8)

        self.scrape_btn = ctk.CTkButton(
            right,
            text="Start scraping",
            font=FONT_BOLD,
            height=42,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color=C["bg"],
            command=self._start_scrape,
        )
        self.scrape_btn.pack(fill="x", padx=14, pady=(16, 6))

        ctk.CTkButton(
            right, text="Open output folder", height=36,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
            command=self._open_output_folder,
        ).pack(fill="x", padx=14, pady=4)

        ctk.CTkLabel(
            right, text="Import to database", font=FONT_BOLD, text_color=C["muted"],
        ).pack(anchor="w", padx=14, pady=(12, 4))
        _muted(
            right,
            "Scraped rows must be in the SQLite DB for Search, Integrity, and Misclassify. "
            "Auto-import after scrape is on by default; you can also load CSVs manually.",
        ).pack(anchor="w", padx=14, pady=(0, 6))
        self.scrape_auto_import = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            right, text="Import scrape results into DB (for Misclassify)",
            variable=self.scrape_auto_import, font=FONT_SM, text_color=C["text"],
            fg_color=C["accent"], hover_color=C["accent_hover"],
            checkmark_color=C["bg"], border_color=C["border"],
        ).pack(anchor="w", padx=14, pady=2)
        self.scrape_import_skip = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            right, text="Skip existing source URLs",
            variable=self.scrape_import_skip, font=FONT_SM, text_color=C["text"],
            fg_color=C["accent"], hover_color=C["accent_hover"],
            checkmark_color=C["bg"], border_color=C["border"],
        ).pack(anchor="w", padx=14, pady=2)
        ctk.CTkButton(
            right, text="Import folder → DB", height=36,
            command=self._import_downloads_folder,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(fill="x", padx=14, pady=(8, 4))
        ctk.CTkButton(
            right, text="Import CSV file…", height=32,
            command=self._import_csv_file,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(fill="x", padx=14, pady=4)
        self.scrape_import_status = ctk.CTkLabel(
            right, text="", font=FONT_SM, text_color=C["muted"], anchor="w",
        )
        self.scrape_import_status.pack(fill="x", padx=14, pady=(4, 8))

        self.scrape_progress = ctk.CTkProgressBar(
            right, progress_color=C["accent"], fg_color=C["elevated"], height=8
        )
        self.scrape_progress.pack(fill="x", padx=14, pady=(8, 16))
        self.scrape_progress.set(0)

        # Sources may have been loaded before this tab was lazy-built
        self._populate_scrape_tree()

    def _scrape_select_all(self):
        for item in self.scrape_tree.get_children():
            if self.scrape_direct_only.get():
                if "direct" not in self.scrape_tree.item(item, "tags"):
                    continue
            self.scrape_tree.selection_add(item)
        self._update_scrape_selection()

    def _scrape_clear_selection(self):
        self.scrape_tree.selection_remove(*self.scrape_tree.selection())
        self._update_scrape_selection()

    def _scrape_on_select(self, _event=None):
        self._update_scrape_selection()

    def _update_scrape_selection(self):
        self.selected_states.clear()
        for item in self.scrape_tree.selection():
            vals = self.scrape_tree.item(item, "values")
            if vals:
                self.selected_states.add(vals[0])
        self.stats_label.configure(text=f"{len(self.selected_states)} selected")

    def _scrape_browse_output(self):
        folder = filedialog.askdirectory(initialdir=self.scrape_output_var.get())
        if folder:
            self.scrape_output_var.set(folder)

    def _start_scrape(self):
        if self.is_running:
            return
        from scraper.config import REGISTRIES, get_registry_by_abbr
        from scraper.scrapers.base import ScraperFactory

        states = list(self.selected_states)
        delay = float(self.scrape_delay_var.get())
        direct_only = bool(self.scrape_direct_only.get())

        if states:
            registries = [get_registry_by_abbr(s) for s in states]
            registries = [r for r in registries if r]
            if direct_only:
                registries = [r for r in registries if r.direct_downloads]
        elif direct_only:
            registries = [r for r in REGISTRIES if r.abbr != "US" and r.direct_downloads]
        else:
            messagebox.showwarning(
                "No selection",
                "Select jurisdictions or enable Direct / bulk only.",
            )
            return
        if not registries:
            messagebox.showwarning("No targets", "No matching registries.")
            return

        output_dir = Path(self.scrape_output_var.get())
        output_dir.mkdir(parents=True, exist_ok=True)
        auto_import = bool(self.scrape_auto_import.get()) if hasattr(self, "scrape_auto_import") else True
        skip_urls = bool(self.scrape_import_skip.get()) if hasattr(self, "scrape_import_skip") else True
        db_path = self.db_path
        self._set_running(True)
        self.scrape_progress.set(0)
        total = len(registries)

        def log(msg):
            self.log_queue.put(msg)

        def worker():
            from scraper.database import Database

            try:
                total_records = 0
                total_imported = 0
                total_skipped = 0
                for i, reg in enumerate(registries):
                    log(f"[{reg.abbr}] Scraping {reg.name}…")
                    scraper = ScraperFactory.create(reg.abbr, delay=delay)
                    try:
                        records = scraper.scrape()
                    finally:
                        scraper.close()
                    if records:
                        csv_path = output_dir / f"{reg.abbr.lower()}_offenders.csv"
                        fields: List[str] = []
                        seen = set()
                        for rec in records:
                            for k in rec:
                                if k not in seen:
                                    seen.add(k)
                                    fields.append(k)
                        with open(csv_path, "w", newline="", encoding="utf-8") as f:
                            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                            w.writeheader()
                            w.writerows(records)
                        log(f"  Saved {len(records)} → {csv_path}")
                        total_records += len(records)
                        if auto_import:
                            try:
                                db = Database(db_path)
                                try:
                                    imp = db.import_records(
                                        records,
                                        state=reg.abbr,
                                        skip_existing_urls=skip_urls,
                                    )
                                finally:
                                    db.close()
                                total_imported += int(imp.get("imported") or 0)
                                total_skipped += int(imp.get("skipped") or 0)
                                log(
                                    f"  DB import: +{imp.get('imported', 0)} "
                                    f"(skipped {imp.get('skipped', 0)})"
                                )
                            except Exception as ie:
                                log(f"  DB import error: {ie}")
                    else:
                        log("  No records")
                    pct = (i + 1) / max(total, 1)
                    self.after(0, lambda p=pct: self.scrape_progress.set(p))
                log(
                    f"Done. Scraped {total_records}"
                    + (
                        f" · DB imported {total_imported} (skipped {total_skipped})"
                        if auto_import
                        else " · (DB auto-import off — use Import for Misclassify)"
                    )
                )
                if auto_import and total_imported:
                    self.after(0, self._after_db_data_changed)
            except Exception as e:
                log(f"ERROR: {e}")
            finally:
                self.after(0, lambda: self._set_running(False))

        threading.Thread(target=worker, daemon=True).start()

    def _import_downloads_folder(self):
        from scraper.database import Database

        folder = self.scrape_output_var.get() or "data/downloads"
        if not Path(folder).is_dir():
            messagebox.showwarning("Missing folder", f"Not a directory: {folder}")
            return
        skip = bool(self.scrape_import_skip.get())
        try:
            db = Database(self.db_path)
            try:
                summary = db.import_csv_directory(folder, skip_existing_urls=skip)
            finally:
                db.close()
        except Exception as e:
            messagebox.showerror("Import failed", str(e))
            return
        msg = (
            f"Files: {summary['files']} · imported {summary['imported']} · "
            f"skipped {summary['skipped']} · rows {summary['total_rows']}"
        )
        if summary.get("errors"):
            msg += f" · errors: {len(summary['errors'])}"
        self.scrape_import_status.configure(text=msg)
        self.log_queue.put(f"CSV import folder: {msg}")
        for err in summary.get("errors") or []:
            self.log_queue.put(f"  import error: {err}")
        self._after_db_data_changed()

    def _import_csv_file(self):
        from scraper.database import Database

        path = filedialog.askopenfilename(
            filetypes=[("CSV", "*.csv"), ("All", "*.*")],
            initialdir=self.scrape_output_var.get() or "data/downloads",
        )
        if not path:
            return
        skip = bool(self.scrape_import_skip.get())
        try:
            db = Database(self.db_path)
            try:
                result = db.import_csv(path, skip_existing_urls=skip)
            finally:
                db.close()
        except Exception as e:
            messagebox.showerror("Import failed", str(e))
            return
        msg = (
            f"{Path(path).name}: imported {result['imported']} · "
            f"skipped {result['skipped']} · rows {result['total_rows']}"
        )
        self.scrape_import_status.configure(text=msg)
        self.log_queue.put(f"CSV import: {msg}")
        self._after_db_data_changed()

    def _after_db_data_changed(self) -> None:
        """Refresh Integrity / header; mark Misclassify stats as needing re-Analyze."""
        if hasattr(self, "_refresh_integrity"):
            try:
                self._refresh_integrity()
            except Exception:
                pass
        try:
            self._refresh_header_db_path()
        except Exception:
            pass
        # Misclassify / Statistics are computed on demand — prompt re-run
        note = "DB updated · open Misclassify → Analyze to include new rows"
        if hasattr(self, "misclass_status"):
            try:
                self.misclass_status.configure(text=note)
            except Exception:
                pass
        if hasattr(self, "mcstat_status"):
            try:
                self.mcstat_status.configure(text=note)
            except Exception:
                pass
        self.log_queue.put(note)

    def _check_duplicates(self) -> None:
        """Scan DB for duplicate groups and show a summary dialog."""
        from scraper.database import DEFAULT_DEDUPE_STRATEGIES, Database

        strats = list(DEFAULT_DEDUPE_STRATEGIES)
        try:
            db = Database(self.db_path)
            try:
                summary = db.count_duplicates(strats)
                samples = db.find_duplicate_groups("source_url", limit_groups=8)
            finally:
                db.close()
        except Exception as e:
            messagebox.showerror("Duplicate check failed", str(e))
            return

        lines = [
            f"Total offenders: {summary['total_offenders']:,}",
            "",
            "By match key (safe extras are auto-removable; portal/CAPTCHA clusters are not):",
        ]
        for s, info in (summary.get("by_strategy") or {}).items():
            lines.append(
                f"  · {s}: {info.get('safe_extra_rows', 0):,} safe removable "
                f"/ {info.get('extra_rows', 0):,} raw extra "
                f"({info.get('unsafe_groups', 0)} unsafe groups)"
            )
        safe_samples = [g for g in samples if g.get("safe", True)][:5]
        unsafe_samples = [g for g in samples if not g.get("safe", True)][:3]
        if safe_samples:
            lines.append("")
            lines.append("Sample safe source_url duplicates:")
            for g in safe_samples:
                lines.append(
                    f"  · keep #{g['keep_id']} {g['keep_preview']} "
                    f"(×{g['count']}) remove {g['remove_ids'][:4]}"
                )
        if unsafe_samples:
            lines.append("")
            lines.append("Skipped portal/CAPTCHA URL clusters (not removed):")
            for g in unsafe_samples:
                lines.append(f"  · ×{g['count']}  {str(g.get('key') or '')[:60]}")
        lines.append("")
        lines.append(
            "Use Remove duplicates… to delete safe extras. "
            "Details are merged onto the keeper (states, charges, listings/URLs)."
        )
        msg = "\n".join(lines)
        self.log_queue.put("Duplicate check:\n" + msg)
        if hasattr(self, "integrity_status"):
            safe_extra = int(summary.get("total_safe_extra_rows") or 0)
            self.integrity_status.configure(
                text=f"Duplicates: {safe_extra:,} safe removable"
            )
        messagebox.showinfo("Duplicate check", msg)
        try:
            self._refresh_integrity()
        except Exception:
            pass

    def _remove_duplicates(self) -> None:
        """Confirm and remove duplicates (merge multi-state/charges, then delete)."""
        from scraper.database import DEFAULT_DEDUPE_STRATEGIES, Database

        strats = list(DEFAULT_DEDUPE_STRATEGIES)
        try:
            db = Database(self.db_path)
            try:
                preview = db.remove_duplicates_all(
                    strats,
                    dry_run=True,
                    merge_fields=True,
                    safe_only=True,
                )
            finally:
                db.close()
        except Exception as e:
            messagebox.showerror("Duplicate scan failed", str(e))
            return

        would = int(preview.get("total_deleted") or 0)
        skipped_u = int(preview.get("total_skipped_unsafe") or 0)
        merged_preview = int(preview.get("total_merged_fields") or 0)
        if would <= 0:
            messagebox.showinfo(
                "Remove duplicates",
                "No safe duplicates found for URL / external id / name+DOB "
                "(same-state or multi-state).\n"
                f"(Skipped {skipped_u} portal/CAPTCHA URL clusters.)",
            )
            return

        detail_lines = []
        for r in preview.get("strategies") or []:
            if r.get("deleted"):
                detail_lines.append(
                    f"  · {r['strategy']}: {r['deleted']:,} rows in {r['groups']:,} groups"
                    + (f" · ~{r.get('merged_fields', 0)} field merges" if r.get("merged_fields") else "")
                )
        detail = "\n".join(detail_lines) if detail_lines else ""
        ok = messagebox.askyesno(
            "Remove duplicates?",
            (
                f"About to permanently delete {would:,} safe duplicate row(s).\n\n"
                f"{detail}\n\n"
                f"Portal/CAPTCHA URL clusters skipped: {skipped_u}\n"
                f"Field merges onto keepers (preview): {merged_preview:,}\n\n"
                "Keeps the richest record per group and merges details from the "
                "others — multiple states, charges/listings, and source URLs are "
                "combined (e.g. FL | TX · Assault | Burglary) before extras are deleted.\n\n"
                "Continue?"
            ),
        )
        if not ok:
            return

        try:
            db = Database(self.db_path)
            try:
                result = db.remove_duplicates_all(
                    strats,
                    dry_run=False,
                    merge_fields=True,
                    safe_only=True,
                )
            finally:
                db.close()
        except Exception as e:
            messagebox.showerror("Remove duplicates failed", str(e))
            return

        deleted = int(result.get("total_deleted") or 0)
        left = int(result.get("total_offenders") or 0)
        skipped_u = int(result.get("total_skipped_unsafe") or 0)
        merged_n = int(result.get("total_merged_fields") or 0)
        msg = (
            f"Deleted {deleted:,} duplicates · {left:,} remain"
            + (f" · merged {merged_n:,} fields" if merged_n else "")
            + (f" · skipped {skipped_u} unsafe URL clusters" if skipped_u else "")
        )
        self.log_queue.put(f"Dedupe: {msg}")
        if hasattr(self, "integrity_status"):
            self.integrity_status.configure(text=msg)
        messagebox.showinfo("Duplicates removed", msg)
        self._after_db_data_changed()

    # -----------------------------------------------------------------------
    # Search
    # -----------------------------------------------------------------------
