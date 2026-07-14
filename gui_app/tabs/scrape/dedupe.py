"""ScrapeDedupeMixin."""
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


class ScrapeDedupeMixin:
    def _check_duplicates(self) -> None:
        """Scan DB for duplicate groups off the UI thread (~10–20s on large DBs)."""
        if getattr(self, "_dup_check_running", False):
            messagebox.showinfo("Duplicate check", "Already scanning…")
            return
        self._dup_check_running = True
        if hasattr(self, "integrity_status"):
            try:
                self.integrity_status.configure(text="Scanning duplicates…")
            except Exception:
                pass
        db_path = str(getattr(self, "db_path", None) or "data/offenders.db")

        def worker() -> None:
            err = None
            summary = None
            samples = []
            try:
                from scraper.database import DEFAULT_DEDUPE_STRATEGIES, Database

                strats = list(DEFAULT_DEDUPE_STRATEGIES)
                db = Database(db_path)
                try:
                    summary = db.count_duplicates(strats)
                    samples = db.find_duplicate_groups("source_url", limit_groups=8)
                finally:
                    db.close()
            except Exception as e:
                err = str(e)

            def done() -> None:
                self._dup_check_running = False
                if err or not summary:
                    messagebox.showerror(
                        "Duplicate check failed", err or "no result"
                    )
                    return
                lines = [
                    f"Total offenders: {summary['total_offenders']:,}",
                    "",
                    "By match key (safe extras are auto-removable; "
                    "portal/CAPTCHA clusters are not):",
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
                        lines.append(
                            f"  · ×{g['count']}  {str(g.get('key') or '')[:60]}"
                        )
                lines.append("")
                lines.append(
                    "Use Remove duplicates… to delete safe extras. "
                    "Details are merged onto the keeper "
                    "(states, charges, listings/URLs)."
                )
                msg = "\n".join(lines)
                try:
                    self.log_queue.put("Duplicate check:\n" + msg)
                except Exception:
                    pass
                if hasattr(self, "integrity_status"):
                    safe_extra = int(summary.get("total_safe_extra_rows") or 0)
                    try:
                        self.integrity_status.configure(
                            text=f"Duplicates: {safe_extra:,} safe removable"
                        )
                    except Exception:
                        pass
                messagebox.showinfo("Duplicate check", msg)

            try:
                self.after(0, done)
            except Exception:
                self._dup_check_running = False

        threading.Thread(target=worker, name="dup-check", daemon=True).start()


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


