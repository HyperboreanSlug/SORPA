"""Integrity refresh: coverage stats off the UI thread."""
from __future__ import annotations

import csv
import threading
from typing import Any, Dict, List, Optional

from tkinter import filedialog, messagebox


class IntegrityRefreshMixin:
    """Load integrity report without freezing the main window."""

    def _refresh_integrity(self, *, include_dupes: bool = False) -> None:
        """Queue a background integrity load (no-op if Integrity tab not built)."""
        if not hasattr(self, "integrity_summary"):
            return
        if getattr(self, "_integrity_refreshing", False):
            try:
                self.integrity_status.configure(text="Refresh already running…")
            except Exception:
                pass
            return
        self._integrity_refreshing = True
        try:
            self.integrity_status.configure(text="Loading…")
        except Exception:
            pass

        db_path = str(getattr(self, "db_path", None) or "data/offenders.db")
        want_dupes = bool(include_dupes)

        def worker() -> None:
            err: Optional[str] = None
            report: Optional[Dict[str, Any]] = None
            incomplete: List[Any] = []
            dup_summary = None
            notes: List[str] = []
            try:
                from scraper.database import Database

                db = Database(db_path)
                try:
                    # Cheap one-shot repair only — never unlimited backfills.
                    try:
                        fixed = db.repair_bogus_states()
                        if fixed:
                            notes.append(
                                f"Repaired {fixed:,} rows with bogus state codes"
                            )
                    except Exception:
                        pass
                    report = db.get_integrity_report()
                    incomplete = db.find_incomplete_reports(
                        need_race=True,
                        need_crime=True,
                        need_photo=True,
                        need_html=False,
                        limit=5000,
                    )
                    if want_dupes:
                        try:
                            from scraper.database import DEFAULT_DEDUPE_STRATEGIES

                            dup_summary = db.count_duplicates(
                                list(DEFAULT_DEDUPE_STRATEGIES)
                            )
                        except Exception as e:
                            notes.append(f"Duplicate scan skipped: {e}")
                finally:
                    db.close()
            except Exception as e:
                err = str(e)

            def done() -> None:
                self._integrity_refreshing = False
                for n in notes:
                    try:
                        self.log_queue.put(n)
                    except Exception:
                        pass
                if err or not report:
                    try:
                        self.integrity_summary.configure(
                            text=f"Error: {err or 'no report'}"
                        )
                        self.integrity_status.configure(text="Refresh failed")
                    except Exception:
                        pass
                    return
                self._apply_integrity_report(report, incomplete, dup_summary)

            try:
                self.after(0, done)
            except Exception:
                self._integrity_refreshing = False

        threading.Thread(
            target=worker, name="integrity-refresh", daemon=True
        ).start()

    def _apply_integrity_report(
        self, report: Dict[str, Any], incomplete: list, dup_summary: Any
    ) -> None:
        """Paint integrity widgets from a finished worker payload."""
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
            dup_line = (
                "\nDuplicates: " + " · ".join(parts)
                if parts
                else "\nDuplicates: none found (URL / external id / name+DOB / multi-state)"
            )
        try:
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
        except Exception as e:
            try:
                self.integrity_status.configure(text=f"UI update error: {e}")
            except Exception:
                pass
        self._last_integrity_report = report

    def _export_integrity_csv(self) -> None:
        report = getattr(self, "_last_integrity_report", None)
        if not report:
            self._refresh_integrity()
            messagebox.showinfo(
                "Export",
                "Integrity report is loading in the background.\n"
                "Click Export again when status shows Updated.",
            )
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv")
        if not path:
            return
        try:
            fields = [
                "state", "total", "with_race", "pct_race", "with_crime",
                "pct_crime", "with_photo", "pct_photo", "with_html",
                "pct_html", "with_url",
            ]
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                for row in report["by_state"]:
                    w.writerow(row)
            messagebox.showinfo("Exported", path)
        except Exception as e:
            messagebox.showerror("Export failed", str(e))
