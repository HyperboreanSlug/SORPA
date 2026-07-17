"""Misclass result helpers: exclude Correct, populate tree, refresh stats."""
from __future__ import annotations

from typing import Optional


class ReportsFilterStatsMixin:
    def _results_excluding_correct(self, results: Optional[list] = None) -> list:
        """Misclass results with Correct-label verdicts removed (for Statistics)."""
        src = list(results if results is not None else (self._misclass_results or []))
        out = []
        for mc in src:
            v = self._verdict_for_mc(mc) if hasattr(self, "_verdict_for_mc") else ""
            if v in ("correct", "confirmed"):
                continue
            # Also honor offenders.flags ethnicity_review
            try:
                from scraper.ethnicity_review import ethnicity_review_verdict

                if ethnicity_review_verdict(getattr(mc, "record", None)) == "correct":
                    continue
            except Exception:
                pass
            out.append(mc)
        return out

    def _refresh_stats_from_verdicts(self, *, repaint_tree: bool = True) -> None:
        """Recompute Statistics after Correct labels change.

        *repaint_tree*=False skips the heavy tree rebuild (used after a single
        sidebar confirmation that already patches the row in place).
        """
        meta = getattr(self, "_misclass_meta", None) or {}
        if not meta and not self._misclass_results:
            return
        filtered = self._results_excluding_correct()
        try:
            self._update_misclass_stats(
                filtered,
                db_total=int(meta.get("db_total") or 0),
                scanned_cap=int(meta.get("scanned_cap") or 0),
                min_conf=float(meta.get("min_conf") or 0.5),
                eth_filter=str(meta.get("eth_filter") or "all"),
                eth_base_count=meta.get("eth_base_count"),
            )
        except Exception:
            pass
        if repaint_tree and hasattr(self, "misclass_tree"):
            try:
                self._populate_misclass_tree(filtered)
            except Exception:
                pass

    def _misclass_patch_tree_verdict(
        self, record: dict, verdict: str
    ) -> None:
        """Update or drop one Misclassify row after confirm (no full rebuild)."""
        tree = getattr(self, "misclass_tree", None)
        by_iid = getattr(self, "_misclass_records_by_iid", None)
        if tree is None or by_iid is None:
            return
        rid = record.get("id")
        url = str(record.get("source_url") or "")
        pick = None
        for iid, rec in by_iid.items():
            if rid is not None and rec.get("id") == rid:
                pick = iid
                break
            if url and rec.get("source_url") == url:
                pick = iid
                break
        if pick is None:
            return
        # Keep cached row flags/ethnicity in sync with the sidebar record.
        cached = by_iid.get(pick)
        if isinstance(cached, dict):
            for key in ("flags", "likely_ethnicity", "id"):
                if record.get(key) is not None:
                    cached[key] = record.get(key)
        if verdict == "correct":
            try:
                tree.delete(pick)
            except Exception:
                pass
            by_iid.pop(pick, None)
            mc_map = getattr(self, "_misclass_mc_by_iid", None)
            if isinstance(mc_map, dict):
                mc_map.pop(pick, None)
            return
        try:
            from gui_app.tabs.browse.misclassify.constants import verification_label

            label = verification_label(cached or record)
            tree.set(pick, "confirmation", label)
        except Exception:
            pass

    def _populate_misclass_tree(self, results: list) -> None:
        if not hasattr(self, "misclass_tree"):
            return
        self.misclass_tree.delete(*self.misclass_tree.get_children())
        self._misclass_records_by_iid = {}
        self._misclass_mc_by_iid = {}
        display = (
            self._misclass_apply_display_filters(results)
            if hasattr(self, "_misclass_apply_display_filters")
            else results
        )
        from gui_app.tabs.browse.misclassify.constants import (
            format_confidence_cell,
            format_deepface_cell,
            verification_label,
        )
        from scraper.confidence_display import combine_name_face_confidence
        from scraper.crime_summary import summarize_crime

        # Reuse DeepFace map across confirm rebuilds; refresh on Analyze.
        oids = []
        for mc in display[:500]:
            rid = (getattr(mc, "record", None) or {}).get("id")
            if rid is not None:
                try:
                    oids.append(int(rid))
                except (TypeError, ValueError):
                    pass
        oids_key = tuple(oids)
        df_map: dict = {}
        cache = getattr(self, "_misclass_df_map_cache", None)
        if (
            isinstance(cache, dict)
            and cache.get("key") == oids_key
            and isinstance(cache.get("map"), dict)
        ):
            df_map = cache["map"]
        else:
            try:
                from scraper.database import Database

                if oids:
                    db_path = str(
                        getattr(self, "db_path", None) or "data/offenders.db"
                    )
                    db = Database(db_path)
                    try:
                        if hasattr(db, "get_deepface_scans_map"):
                            df_map = db.get_deepface_scans_map(oids) or {}
                    finally:
                        db.close()
            except Exception:
                df_map = {}
            self._misclass_df_map_cache = {"key": oids_key, "map": df_map}

        for mc in display[:500]:
            rec = dict(mc.record or {})
            name = (
                " ".join(
                    p for p in (
                        rec.get("first_name") or "",
                        rec.get("middle_name") or "",
                        rec.get("last_name") or "",
                    ) if str(p).strip()
                )
                or (rec.get("full_name") or "—")
            )
            rec["_misclass_expected_race"] = mc.expected_race
            rec["_misclass_likely"] = mc.likely_ethnicity
            # Raw surname conf (for recompute); display conf may be combined.
            try:
                name_conf = float(mc.confidence)
            except (TypeError, ValueError):
                name_conf = 0.0
            rec["_misclass_name_conf"] = name_conf
            rec["name_confidence"] = name_conf
            # Surface analyzer ethnicity on the record for the sidebar picker
            if mc.likely_ethnicity and not rec.get("likely_ethnicity"):
                rec["likely_ethnicity"] = mc.likely_ethnicity
            scan = None
            try:
                oid = int(rec["id"]) if rec.get("id") is not None else None
            except (TypeError, ValueError):
                oid = None
            if oid is not None:
                scan = df_map.get(oid)
            if scan:
                rec["_deepface"] = {
                    "top_label": scan.get("top_label"),
                    "top_confidence": scan.get("top_confidence"),
                    "predicted_label": scan.get("predicted_label"),
                    "scores": scan.get("scores") or {},
                    "is_hit": scan.get("is_hit"),
                    "severity": scan.get("severity"),
                    "error": scan.get("error"),
                    "face_detected": scan.get("face_detected"),
                }
            disp_conf, is_combined = combine_name_face_confidence(
                name_conf,
                name_ethnicity=str(mc.likely_ethnicity or ""),
                deepface=rec.get("_deepface") if scan else None,
            )
            rec["_misclass_conf"] = disp_conf
            rec["_misclass_conf_combined"] = is_combined
            rec["confidence"] = disp_conf
            conf_cell = format_confidence_cell(
                name_conf,
                name_ethnicity=str(mc.likely_ethnicity or ""),
                deepface=rec.get("_deepface") if scan else None,
            )
            df_cell = format_deepface_cell(scan)
            crime_raw = (
                rec.get("crime")
                or rec.get("offense_description")
                or rec.get("offense_type")
                or ""
            )
            crime = summarize_crime(str(crime_raw), max_len=72) if crime_raw else "—"
            conf_status = verification_label(rec)
            # Prefer JSON report verdict when set and flags empty
            if conf_status == "Unverified" and hasattr(self, "_verdict_for_mc"):
                v = self._verdict_for_mc(mc)
                if v in ("correct", "confirmed"):
                    conf_status = "Confirmed correct"
                elif v == "incorrect":
                    conf_status = "Confirmed incorrect"
            iid = self.misclass_tree.insert(
                "",
                "end",
                values=(
                    name,
                    (mc.expected_race or "—")[:14],
                    (mc.likely_ethnicity or "")[:22],
                    conf_cell,
                    df_cell,
                    crime or "—",
                    conf_status,
                ),
            )
            self._misclass_records_by_iid[iid] = rec
            self._misclass_mc_by_iid[iid] = mc

        # Rebuild inserts in scan order; restore the user's column sort
        # (e.g. after classification confirmation, which repopulates the tree).
        reapply = getattr(self.misclass_tree, "_reapply_sort", None)
        if callable(reapply):
            try:
                reapply()
            except Exception:
                pass
