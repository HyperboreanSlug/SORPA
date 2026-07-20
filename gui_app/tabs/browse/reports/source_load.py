"""SLoad"""
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


class ReportsSourceLoadMixin:
    def _reports_load_deepface_hits(self) -> list:
        """Load stored DeepFace gross-misclass hits as Misclassification rows.

        Re-validates with the same recorded-race / face-label rules as Scan.
        """
        try:
            from scraper.mugshot_ethnicity.scanner import load_deepface_hits_as_misclass

            recorded = None
            faces = None
            min_c = None
            if hasattr(self, "_deepface_scan_collect_options"):
                try:
                    opts = self._deepface_scan_collect_options()
                    recorded = list(opts.get("recorded_races") or [])
                    faces = list(opts.get("face_labels") or [])
                    min_c = float(opts.get("min_confidence") or 0.85)
                except Exception:
                    pass
            if recorded is None or faces is None or min_c is None:
                try:
                    from scraper.app_settings import load_settings

                    sett = load_settings()
                    if recorded is None:
                        raw_r = str(sett.get("deepface_scan_recorded") or "WHITE")
                        recorded = [
                            p.strip().upper()
                            for p in raw_r.replace(";", ",").split(",")
                            if p.strip()
                        ] or ["WHITE"]
                    if faces is None:
                        raw_f = str(
                            sett.get("deepface_scan_faces") or "black,indian,asian"
                        )
                        faces = [
                            p.strip().lower()
                            for p in raw_f.replace(";", ",").split(",")
                            if p.strip()
                        ] or ["black", "indian", "asian"]
                    if min_c is None:
                        try:
                            min_c = float(sett.get("deepface_scan_min_conf") or 0.85)
                        except (TypeError, ValueError):
                            min_c = 0.85
                except Exception:
                    recorded = recorded or ["WHITE"]
                    faces = faces or ["black", "indian", "asian"]
                    min_c = 0.85 if min_c is None else min_c
            return load_deepface_hits_as_misclass(
                db_path=str(getattr(self, "db_path", None) or "data/offenders.db"),
                min_confidence=float(min_c if min_c is not None else 0.85),
                recorded_races=recorded,
                face_labels=faces,
                revalidate=True,
            )
        except Exception:
            return []


    def _reports_merge_sources(self, surname_results: list, deepface_results: list) -> list:
        """Merge surname misclass + DeepFace hits; attach face data onto surname rows."""
        from scraper.database import Database

        by_id: Dict[Any, Any] = {}
        # Index deepface by offender id
        df_by_id: Dict[int, Any] = {}
        for mc in deepface_results or []:
            rec = mc.record or {}
            try:
                oid = int(rec["id"]) if rec.get("id") is not None else None
            except (TypeError, ValueError):
                oid = None
            if oid is not None:
                df_by_id[oid] = mc

        for mc in surname_results or []:
            rec = dict(mc.record or {})
            try:
                oid = int(rec["id"]) if rec.get("id") is not None else None
            except (TypeError, ValueError):
                oid = None
            # Attach DeepFace face info when available for the same person
            if oid is not None and oid in df_by_id:
                df_mc = df_by_id[oid]
                df_payload = (df_mc.record or {}).get("_deepface") or {}
                rec["_deepface"] = df_payload
                rec["_deepface_is_hit"] = True
                names = list(mc.matching_names or [])
                for n in (df_mc.matching_names or []):
                    if n not in names:
                        names.append(n)
                mc.matching_names = names
                # Blend name + DeepFace into displayed confidence when face scan exists.
                try:
                    from scraper.confidence_display import combine_name_face_confidence

                    name_c = float(mc.confidence or 0)
                    rec["_misclass_name_conf"] = name_c
                    disp, is_comb = combine_name_face_confidence(
                        name_c,
                        name_ethnicity=str(mc.likely_ethnicity or ""),
                        deepface=df_payload,
                    )
                    mc.confidence = disp
                    rec["_misclass_conf"] = disp
                    rec["_misclass_conf_combined"] = is_comb
                    rec["confidence"] = disp
                    rec["name_confidence"] = name_c
                except (TypeError, ValueError):
                    pass
                mc.record = rec
                del df_by_id[oid]
            key = oid if oid is not None else id(mc)
            by_id[key] = mc

        # Remaining pure DeepFace hits (no surname misclass row)
        for oid, mc in df_by_id.items():
            by_id[oid] = mc

        return list(by_id.values())


    @staticmethod
    def _reports_photo_exists(photo: str) -> bool:
        """True if mugshot is present *and loadable* (not truncated/corrupt)."""
        try:
            from gui_app.shared.export_card_photo import is_usable_mugshot_path

            return bool(is_usable_mugshot_path(photo))
        except Exception:
            pass
        # Fallback: path exists only (legacy)
        raw = (photo or "").strip()
        if not raw:
            return False
        candidates = (
            Path(raw),
            Path.cwd() / raw,
            ROOT / raw,
            Path.cwd() / raw.replace("\\", "/"),
            ROOT / raw.replace("\\", "/"),
        )
        for p in candidates:
            try:
                if p.is_file() and p.stat().st_size >= 400:
                    return True
            except OSError:
                continue
        return False

    def _reports_filter_snapshot(self, *, verdict_key: Optional[str] = None) -> dict:
        """Read Tk filter widgets on the main thread (safe for worker use)."""
        photos_only = False
        include_df = False
        try:
            photos_only = bool(
                getattr(self, "report_photos_only", None)
                and self.report_photos_only.get()
            )
        except Exception:
            photos_only = True
        try:
            include_df = bool(
                getattr(self, "report_include_deepface", None)
                and self.report_include_deepface.get()
            )
        except Exception:
            include_df = False
        if verdict_key is not None:
            vfilter = str(verdict_key).strip().lower() or "all"
        else:
            try:
                vfilter = self._reports_verdict_filter_key()
            except Exception:
                vfilter = "unreviewed"
        try:
            race_allow = set(self._reports_race_buckets_allowed() or ())
        except Exception:
            race_allow = {"White", "Black", "Other"}
        if not race_allow:
            race_allow = {"White", "Black", "Other"}
        try:
            actual = self._reports_actual_filter_value()
        except Exception:
            actual = "Non-white"
        try:
            listed = self._reports_listed_filter_value()
        except Exception:
            listed = "All"
        # Legacy checkbox mirror (main thread only)
        try:
            if hasattr(self, "report_race_white"):
                self.report_race_white.set(listed in ("All", "White"))
            if hasattr(self, "report_race_black"):
                self.report_race_black.set(listed in ("All", "Black"))
            if hasattr(self, "report_race_other"):
                self.report_race_other.set(listed in ("All", "Other"))
        except Exception:
            pass
        return {
            "photos_only": photos_only,
            "include_deepface": include_df,
            "vfilter": vfilter,
            "race_allow": race_allow,
            "actual": actual,
            "listed": listed,
        }

    def _reports_filtered_source(
        self,
        *,
        verdict_key: Optional[str] = None,
        snapshot: Optional[dict] = None,
    ) -> list:
        """Apply report filters to surname + DeepFace results (full pool).

        *verdict_key*: optional override (``all`` / ``unreviewed`` / …).
        *snapshot*: pre-read filter state (use when calling from a worker thread
        so Tk widgets are not touched off the main thread).
        """
        snap = snapshot if isinstance(snapshot, dict) else self._reports_filter_snapshot(
            verdict_key=verdict_key
        )
        if verdict_key is not None:
            snap = dict(snap)
            snap["vfilter"] = str(verdict_key).strip().lower() or "all"

        photos_only = bool(snap.get("photos_only"))
        vfilter = str(snap.get("vfilter") or "unreviewed")
        race_allow = set(snap.get("race_allow") or {"White", "Black", "Other"})
        actual_want = str(snap.get("actual") or "All")

        # Reports owns its own full-DB analyze pool — never the Misclassify tab run
        surname = list(getattr(self, "_report_analyze_results", None) or [])
        deepface: list = []
        if bool(snap.get("include_deepface")):
            deepface = self._reports_load_deepface_hits()
        results = self._reports_merge_sources(surname, deepface)
        if not results:
            return []

        # Ensure verdicts file is loaded (first open / new session)
        if not getattr(self, "_report_verdicts_loaded", False):
            if not hasattr(self, "_report_verdicts") or self._report_verdicts is None:
                self._report_verdicts = {}
            self._load_report_verdicts()
            self._report_verdicts_loaded = True
        # Re-sync the verdict store from disk on EVERY pass: confirmations made
        # mid-session (card export, Misclassify sidebar, other tabs) write to
        # report_verdicts.json / DB flags but not this in-memory dict, so without
        # a reload confirmed people keep reappearing under "Unconfirmed".
        try:
            self._load_report_verdicts()
        except Exception:
            pass

        # Prefetch photo paths when missing
        need_ids: List[int] = []
        for mc in results:
            rec = mc.record or {}
            if not (rec.get("photo_path") or "").strip() and rec.get("id") is not None:
                try:
                    need_ids.append(int(rec["id"]))
                except (TypeError, ValueError):
                    pass
        photo_by_id: Dict[int, Dict[str, Any]] = {}
        if need_ids:
            try:
                from scraper.database import Database

                db = Database(self.db_path)
                try:
                    for oid in need_ids[:2000]:
                        full = db.get_offender_by_id(oid)
                        if full:
                            photo_by_id[oid] = full
                finally:
                    db.close()
            except Exception:
                photo_by_id = {}

        # Enrich records with DB fields (photo / HTML / URL)
        if photo_by_id:
            for mc in results:
                rec = mc.record or {}
                try:
                    oid = int(rec["id"]) if rec.get("id") is not None else None
                except (TypeError, ValueError):
                    oid = None
                if oid is None or oid not in photo_by_id:
                    continue
                full = photo_by_id[oid]
                merged = dict(full)
                for k, v in rec.items():
                    if str(k).startswith("_"):
                        merged[k] = v
                mc.record = merged

        # Collapse same-person duplicates (session-url variants etc.)
        from scraper.database import Database

        def _actual_ok(mc) -> bool:
            try:
                got = self._reports_actual_bucket(self._reports_actual_label_for_mc(mc))
                return self._reports_actual_bucket_passes(got, actual_want)
            except Exception:
                return True

        best_by_person: Dict[str, Any] = {}
        for mc in results:
            rec = mc.record or {}
            bucket = _misclass_race_bucket(mc.expected_race)
            if bucket not in race_allow:
                continue
            if not _actual_ok(mc):
                continue
            photo = (rec.get("photo_path") or "").strip()
            has_photo = self._reports_photo_exists(photo)
            if photos_only and not has_photo:
                continue
            try:
                person = self._report_person_key(mc)
            except Exception:
                rid = rec.get("id")
                person = f"id:{rid}" if rid is not None else f"obj:{id(mc)}"
            prev = best_by_person.get(person)
            if prev is None:
                best_by_person[person] = mc
                continue
            # Prefer richer record / higher confidence / deepface attachment
            prev_rec = prev.record or {}
            score_new = (
                1 if (rec.get("_deepface") or {}).get("is_hit") else 0,
                Database._row_richness(rec),
                float(mc.confidence or 0),
                1 if has_photo else 0,
            )
            score_old = (
                1 if (prev_rec.get("_deepface") or {}).get("is_hit") else 0,
                Database._row_richness(prev_rec),
                float(prev.confidence or 0),
                1 if self._reports_photo_exists(
                    (prev_rec.get("photo_path") or "").strip()
                )
                else 0,
            )
            if score_new >= score_old:
                best_by_person[person] = mc

        out = []
        for mc in best_by_person.values():
            verdict = self._verdict_for_mc(mc)
            if not self._reports_verdict_passes_filter(verdict, vfilter):
                continue
            out.append(mc)

        # Stable order: confidence desc within the selected verdict bucket
        out.sort(key=lambda m: float(m.confidence or 0), reverse=True)
        return out


