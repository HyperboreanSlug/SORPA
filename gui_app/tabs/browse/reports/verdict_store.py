"""Store"""
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


class ReportsVerdictStoreMixin:
    def _load_report_verdicts(self) -> None:
        path = getattr(self, "_report_verdicts_path", None) or (
            ROOT / "data" / "report_verdicts.json"
        )
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._report_verdicts = {
                        str(k): str(v)
                        for k, v in data.items()
                        if v in ("confirmed", "correct", "skip", "unreviewed")
                    }
        except Exception:
            self._report_verdicts = {}


    def _save_report_verdicts(self) -> None:
        path = getattr(self, "_report_verdicts_path", None) or (
            ROOT / "data" / "report_verdicts.json"
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._report_verdicts, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception:
            pass


    @staticmethod
    def _report_item_key(mc) -> str:
        rec = mc.record or {}
        rid = rec.get("id")
        if rid is not None and str(rid).strip() != "":
            return f"id:{rid}"
        name = (
            f"{rec.get('first_name', '') or ''} {rec.get('last_name', '') or ''}"
        ).strip() or (rec.get("full_name") or "")
        return f"n:{name}|{mc.expected_race}|{mc.likely_ethnicity}|{mc.confidence}"


    def _report_verdict_lookup_keys(self, mc) -> List[str]:
        """All keys that may hold a saved verdict for this row (stable + legacy)."""
        keys: List[str] = []
        primary = self._report_item_key(mc)
        keys.append(primary)
        rec = mc.record or {}
        rid = rec.get("id")
        if rid is not None and str(rid).strip() != "":
            keys.append(f"id:{rid}")
        try:
            keys.append(self._report_person_key(mc))
        except Exception:
            pass
        # de-dupe preserve order
        seen = set()
        out: List[str] = []
        for k in keys:
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        return out


    @staticmethod
    def _report_person_key(mc) -> str:
        """Collapse near-duplicate listings of the same person for the report list.

        Includes middle name + DOB so NIRAJ V PATEL and NIRAJ RASHMIBABU PATEL
        do not collapse together.
        """
        from scraper.database import Database
        from scraper.database.identity import person_identity_key

        rec = mc.record or {}
        try:
            stable = Database.stable_external_key(rec)
            if stable:
                return f"p:{stable}"
        except Exception:
            pass
        try:
            norm = Database.normalize_identity_url(rec.get("source_url") or "")
            if norm:
                return f"u:{norm}"
        except Exception:
            pass
        try:
            return f"idk:{person_identity_key(rec)}"
        except Exception:
            pass
        fn = (rec.get("first_name") or "").strip().casefold()
        mn = (rec.get("middle_name") or "").strip().casefold()
        ln = (rec.get("last_name") or "").strip().casefold()
        st = (
            rec.get("state") or rec.get("source_state") or ""
        ).strip().upper()
        dob = (rec.get("date_of_birth") or "").strip().casefold()
        if fn and ln:
            return f"n:{fn}|{mn}|{ln}|{st}|{dob}"
        rid = rec.get("id")
        if rid is not None and str(rid).strip() != "":
            return f"id:{rid}"
        name = (
            f"{rec.get('first_name', '') or ''} {rec.get('last_name', '') or ''}"
        ).strip() or (rec.get("full_name") or "")
        return (
            f"n:{name}|{getattr(mc, 'expected_race', '')}|"
            f"{getattr(mc, 'likely_ethnicity', '')}|{getattr(mc, 'confidence', '')}"
        )


    def _verdict_for_mc(self, mc) -> str:
        """Resolve verdict; prefer non-unreviewed if any alias key has a decision."""
        found = "unreviewed"
        for k in self._report_verdict_lookup_keys(mc):
            v = (self._report_verdicts.get(k) or "").strip()
            if v in ("confirmed", "correct", "skip"):
                return v
            if v == "unreviewed":
                found = "unreviewed"
        return found


    def _set_verdict_for_mc(self, mc, verdict: str, *, save: bool = True) -> None:
        keys = self._report_verdict_lookup_keys(mc)
        if verdict == "unreviewed":
            for key in keys:
                self._report_verdicts.pop(key, None)
        else:
            # Write primary + id alias so later key shape changes still resolve
            for key in keys:
                self._report_verdicts[key] = verdict
        if save:
            self._save_report_verdicts()


    def _set_ethnicity_for_mc(self, mc, ethnicity: str) -> None:
        """Persist a manual ethnicity correction on the misclass row + DB."""
        eth = (ethnicity or "").strip() or "Unknown"
        mc.likely_ethnicity = eth
        names = list(mc.matching_names or [])
        if "manual_override" not in names:
            names = ["manual_override"] + names
        mc.matching_names = names
        rec = mc.record if isinstance(mc.record, dict) else {}
        rec["likely_ethnicity"] = eth
        mc.record = rec
        rid = rec.get("id")
        if rid is not None:
            try:
                from scraper.database import Database

                db = Database(self.db_path)
                try:
                    db.update_offender(int(rid), {"likely_ethnicity": eth})
                finally:
                    db.close()
            except Exception:
                pass


    def _ethnicity_compatible_with_record(self, mc) -> bool:
        """True if name-based ethnicity now matches recorded race (not a mismatch)."""
        try:
            from scraper.searcher import _is_compatible

            rec = mc.record or {}
            return bool(
                _is_compatible(
                    mc.likely_ethnicity or "",
                    (rec.get("race") or mc.expected_race or "").strip(),
                    recorded_ethnicity=(rec.get("ethnicity") or "").strip() or None,
                )
            )
        except Exception:
            return False


