"""Misclassify sidebar verdict / actual-race handlers."""
from __future__ import annotations

from typing import Any, Dict, Optional

from gui_app.shared.record_sidebar import merge_race_manual_flags
from gui_app.tabs.browse.misclassify.constants import verification_label
from gui_app.tabs.browse.misclassify.verdict import apply_misclass_verdict


class MisclassifySidebarActionsMixin:
    def _misclass_show_sidebar(self, rec: Optional[Dict[str, Any]]) -> None:
        if getattr(self, "misclass_sidebar", None) is not None:
            if rec:
                if rec.get("_misclass_likely") and not rec.get("likely_ethnicity"):
                    rec = dict(rec)
                    rec["likely_ethnicity"] = rec["_misclass_likely"]
                self.misclass_sidebar.show(rec)
            else:
                self.misclass_sidebar.clear()
            return
        if getattr(self, "misclass_detail", None) is not None and hasattr(
            self, "_fill_detail_drawer"
        ):
            self._fill_detail_drawer(self.misclass_detail, rec)

    def _misclass_next_target_after(
        self, record: Dict[str, Any], *, drop_current: bool
    ) -> Optional[Dict[str, Any]]:
        """Pick the next tree row to review after confirming *record*.

        Prefer the following row; if none, the previous. When *drop_current*
        (confirmed correct), the following row lands at the current index after
        rebuild.
        """
        tree = getattr(self, "misclass_tree", None)
        by_iid = getattr(self, "_misclass_records_by_iid", None) or {}
        if tree is None:
            return None
        kids = list(tree.get_children(""))
        if not kids:
            return None

        # Locate current row by selection or by id/url match
        cur_iid = None
        sel = tree.selection()
        if sel:
            cur_iid = sel[0]
        if cur_iid not in by_iid:
            cur_iid = None
            rid = record.get("id")
            url = str(record.get("source_url") or "")
            for iid, rec in by_iid.items():
                if rid is not None and rec.get("id") == rid:
                    cur_iid = iid
                    break
                if url and rec.get("source_url") == url:
                    cur_iid = iid
                    break
        if cur_iid is None or cur_iid not in kids:
            return None
        pos = kids.index(cur_iid)
        if drop_current:
            # Current will leave the list → old next becomes index *pos*
            candidates = []
            if pos + 1 < len(kids):
                candidates.append(kids[pos + 1])
            if pos - 1 >= 0:
                candidates.append(kids[pos - 1])
        else:
            # Stay in list → advance past current
            candidates = []
            if pos + 1 < len(kids):
                candidates.append(kids[pos + 1])
            if pos - 1 >= 0:
                candidates.append(kids[pos - 1])
        for iid in candidates:
            rec = by_iid.get(iid)
            if rec:
                return dict(rec)
        return None

    def _misclass_select_record(self, target: Optional[Dict[str, Any]]) -> None:
        """Select tree row matching *target* and load the sidebar."""
        tree = getattr(self, "misclass_tree", None)
        by_iid = getattr(self, "_misclass_records_by_iid", None) or {}
        if tree is None:
            return
        if not target:
            try:
                tree.selection_remove(*tree.selection())
            except Exception:
                pass
            self._misclass_show_sidebar(None)
            return

        rid = target.get("id")
        url = str(target.get("source_url") or "")
        pick_iid = None
        for iid, rec in by_iid.items():
            if rid is not None and rec.get("id") == rid:
                pick_iid = iid
                break
            if url and rec.get("source_url") == url:
                pick_iid = iid
                break
        if pick_iid is None:
            self._misclass_show_sidebar(None)
            return
        try:
            tree.selection_set(pick_iid)
            tree.focus(pick_iid)
            tree.see(pick_iid)
        except Exception:
            pass
        rec = by_iid.get(pick_iid) or target
        self._misclass_show_sidebar(rec)

    def _misclass_sidebar_verdict(self, record: Dict[str, Any], verdict: str) -> None:
        label = (
            "confirmed correct" if verdict == "correct" else "confirmed incorrect"
        )
        # Capture next target *before* tree rebuild (iids change)
        drop = verdict == "correct"
        next_target = self._misclass_next_target_after(record, drop_current=drop)

        db_path = str(getattr(self, "db_path", None) or "data/offenders.db")
        ok, err, record = apply_misclass_verdict(
            db_path=db_path,
            db=getattr(self, "db", None),
            record=record,
            verdict=verdict,
        )
        if not ok:
            if hasattr(self, "misclass_status"):
                self.misclass_status.configure(
                    text=f"Could not save verification: {err or 'unknown error'}"
                )
            if hasattr(self, "log_queue"):
                self.log_queue.put(f"Misclassify verification save failed: {err}")
            return
        if err and hasattr(self, "log_queue"):
            self.log_queue.put(f"Misclassify verification warning: {err}")

        mc = self._misclass_find_mc(record)
        if mc is not None and hasattr(self, "_set_verdict_for_mc"):
            try:
                self._set_verdict_for_mc(mc, verdict, save=True)
            except Exception:
                pass
        self._misclass_sync_cached(record)

        name = (
            " ".join(
                p
                for p in (
                    record.get("first_name") or "",
                    record.get("last_name") or "",
                )
                if str(p).strip()
            )
            or (record.get("full_name") or "—")
        )
        if hasattr(self, "misclass_status"):
            self.misclass_status.configure(
                text=f"Saved {name} as {label}. {verification_label(record)}"
            )
        if hasattr(self, "log_queue"):
            self.log_queue.put(f"Misclassify verification: {name} → {label}")

        # Patch one row (or drop if correct) — avoid full tree + DeepFace rebuild
        # so the next mugshot can start loading immediately.
        if hasattr(self, "_misclass_patch_tree_verdict"):
            try:
                self._misclass_patch_tree_verdict(record, verdict)
            except Exception:
                pass
        if hasattr(self, "_refresh_stats_from_verdicts"):
            try:
                self._refresh_stats_from_verdicts(repaint_tree=False)
            except TypeError:
                try:
                    self._refresh_stats_from_verdicts()
                except Exception:
                    pass
            except Exception:
                pass
        elif hasattr(self, "_populate_misclass_tree") and hasattr(
            self, "_results_excluding_correct"
        ):
            try:
                self._populate_misclass_tree(
                    self._results_excluding_correct(self._misclass_results)
                )
            except Exception:
                pass

        # Advance to next row for continuous review
        self._misclass_select_record(next_target)
        if next_target is None and getattr(self, "misclass_sidebar", None):
            msg = (
                "Marked correct — no more rows."
                if verdict == "correct"
                else "Marked incorrect — no more rows."
            )
            try:
                self.misclass_sidebar.clear(msg)
            except Exception:
                pass
        if next_target is not None and hasattr(self, "misclass_status"):
            try:
                cur = self.misclass_status.cget("text") or ""
                self.misclass_status.configure(text=f"{cur} · next selected")
            except Exception:
                pass

    def _misclass_sidebar_actual_race(
        self, record: Dict[str, Any], actual: str
    ) -> None:
        raw = (actual or "").strip() or "Unknown"
        record["likely_ethnicity"] = raw
        flags_json = merge_race_manual_flags(record.get("flags"))
        record["flags"] = flags_json
        rid = record.get("id")
        if rid is not None:
            try:
                from scraper.database import Database

                db_path = str(getattr(self, "db_path", None) or "data/offenders.db")
                db = Database(db_path)
                try:
                    db.update_offender(
                        int(rid),
                        {"likely_ethnicity": raw, "flags": flags_json},
                    )
                finally:
                    db.close()
            except Exception as exc:
                if hasattr(self, "misclass_status"):
                    self.misclass_status.configure(
                        text=f"Could not save actual race: {exc}"
                    )
                return
        self._misclass_sync_cached(record)
        mc = self._misclass_find_mc(record)
        if mc is not None and hasattr(self, "_set_ethnicity_for_mc"):
            try:
                self._set_ethnicity_for_mc(mc, raw)
            except Exception:
                pass
        if hasattr(self, "misclass_status"):
            self.misclass_status.configure(text=f"Actual race set to {raw}.")
        if hasattr(self, "_populate_misclass_tree") and hasattr(
            self, "_results_excluding_correct"
        ):
            try:
                self._populate_misclass_tree(
                    self._results_excluding_correct(self._misclass_results)
                )
            except Exception:
                pass

    def _misclass_find_mc(self, record: Dict[str, Any]):
        rid = record.get("id")
        url = str(record.get("source_url") or "")
        for iid, rec in (getattr(self, "_misclass_records_by_iid", None) or {}).items():
            if rid is not None and rec.get("id") == rid:
                return (getattr(self, "_misclass_mc_by_iid", None) or {}).get(iid)
            if url and rec.get("source_url") == url:
                return (getattr(self, "_misclass_mc_by_iid", None) or {}).get(iid)
        for mc in getattr(self, "_misclass_results", None) or []:
            r = getattr(mc, "record", None) or {}
            if rid is not None and r.get("id") == rid:
                return mc
            if url and r.get("source_url") == url:
                return mc
        return None

    def _misclass_sync_cached(self, record: Dict[str, Any]) -> None:
        rid = record.get("id")
        url = str(record.get("source_url") or "")
        for iid, rec in list(
            (getattr(self, "_misclass_records_by_iid", None) or {}).items()
        ):
            match = (rid is not None and rec.get("id") == rid) or (
                url and rec.get("source_url") == url
            )
            if not match:
                continue
            for key in ("flags", "likely_ethnicity", "id"):
                if record.get(key) is not None:
                    rec[key] = record.get(key)
            mc = (getattr(self, "_misclass_mc_by_iid", None) or {}).get(iid)
            if mc is not None and isinstance(getattr(mc, "record", None), dict):
                for key in ("flags", "likely_ethnicity", "id"):
                    if record.get(key) is not None:
                        mc.record[key] = record.get(key)
