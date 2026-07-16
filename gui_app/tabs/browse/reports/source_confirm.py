"""Reports confirm actions + independent full-DB Analyze & build."""
from __future__ import annotations

from tkinter import messagebox


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
                f"Mark {len(unchecked):,} unconfirmed card(s) on this page "
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
                continue
            self._set_verdict_for_mc(mc, "confirmed", save=False)
            n += 1
        self._save_report_verdicts()
        self._reports_rebuild_cards()
        self._refresh_stats_from_verdicts()
        if hasattr(self, "report_status"):
            self.report_status.configure(
                text=f"Confirmed {n:,} other unchecked visible cards"
            )

    def _reports_analyze_min_conf(self) -> float:
        """Min surname confidence for Reports full-DB scan (independent of Misclassify)."""
        for attr in ("report_min_conf_var",):
            if hasattr(self, attr):
                try:
                    return float(getattr(self, attr).get())
                except Exception:
                    pass
        return 0.5

    def _reports_build_list(self):
        """Full-DB surname mismatch scan, then apply Reports filters (not Misclassify)."""
        if getattr(self, "_reports_analyzing", False):
            if hasattr(self, "report_status"):
                try:
                    self.report_status.configure(text="Analyze already running…")
                except Exception:
                    pass
            return

        self._reports_analyzing = True
        min_conf = self._reports_analyze_min_conf()
        db_path = str(getattr(self, "db_path", None) or "data/offenders.db")

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

        if hasattr(self, "report_status"):
            try:
                self.report_status.configure(
                    text=(
                        "Analyzing full DB (all ethnicities, no scan cap)… "
                        "UI stays responsive"
                    )
                )
            except Exception:
                pass

        def analyze_work():
            from scraper.searcher import SexOffenderSearcher

            searcher = SexOffenderSearcher(db_path=db_path)
            try:
                results = searcher.analyze_ethnicities(
                    min_confidence=min_conf,
                    limit=0,
                    ethnicity_filter=None,
                )
                return list(results or [])
            finally:
                searcher.close()

        def after_analyze(result=None, error=None):
            if error is not None:
                self._reports_analyzing = False
                if hasattr(self, "report_status"):
                    try:
                        self.report_status.configure(text=f"Analyze error: {error}")
                    except Exception:
                        pass
                messagebox.showerror("Analyze & build", str(error))
                return

            raw = list(result or [])
            self._report_analyze_results = raw
            self._report_analyze_meta = {
                "min_conf": min_conf,
                "ethnicity": "all",
                "limit": 0,
                "raw_n": len(raw),
            }
            raw_n = len(raw)
            if hasattr(self, "report_status"):
                try:
                    self.report_status.configure(
                        text=(
                            f"Full-DB analyze: {raw_n:,} mismatches · "
                            "applying Reports filters…"
                        )
                    )
                except Exception:
                    pass

            def pool_work():
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
                return {
                    "base": base,
                    "sheet": sheet,
                    "snap": snap,
                    "raw_n": raw_n,
                }

            def pool_done(payload=None, error=None):
                self._reports_analyzing = False
                if error is not None:
                    messagebox.showerror("Analyze & build", str(error))
                    return
                data = payload if isinstance(payload, dict) else {}
                base = list(data.get("base") or [])
                sheet = list(data.get("sheet") or [])
                snap_used = data.get("snap") or snap
                raw_count = int(data.get("raw_n") or raw_n)
                self._report_page = 0
                self._report_metrics_base = base
                self._report_pool = sheet
                if not sheet:
                    listed = snap_used.get("listed") or "?"
                    photos = "on" if snap_used.get("photos_only") else "off"
                    show = snap_used.get("vfilter") or "?"
                    actual = snap_used.get("actual") or "All"
                    if raw_count <= 0:
                        msg = (
                            "Full-DB analyze found 0 surname mismatches.\n\n"
                            "Try a lower min confidence, or enable DeepFace hits "
                            "after DeepFace → Scan."
                        )
                    elif base:
                        msg = (
                            f"Full-DB analyze found {raw_count:,} mismatches, and "
                            f"{len(base):,} match Listed/Photos/Actual — "
                            f"but 0 match Show={show}.\n\n"
                            "Switch Show to All (or Confirmed incorrect / "
                            "Confirmed correct) to see them."
                        )
                    else:
                        msg = (
                            f"Full-DB analyze found {raw_count:,} mismatches, "
                            "but none match the current Reports filters:\n"
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
                                f"{raw_count:,} full-DB mismatches "
                                f"(min conf {min_conf})"
                            )
                        )
                    except Exception:
                        pass

            if hasattr(self, "run_bg"):
                self.run_bg(pool_work, pool_done, name="reports-pool")
            else:
                try:
                    pool_done(payload=pool_work(), error=None)
                except Exception as e:
                    pool_done(payload=None, error=e)

        if hasattr(self, "run_bg"):
            self.run_bg(analyze_work, after_analyze, name="reports-analyze")
        else:
            try:
                after_analyze(result=analyze_work(), error=None)
            except Exception as e:
                after_analyze(result=None, error=e)
