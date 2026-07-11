"""Browse → DeepFace Reports: review and track mugshot face-vs-race hits."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import customtkinter as ctk
from tkinter import messagebox

from gui_app.theme import (
    C,
    FONT_BOLD,
    FONT_MONO,
    FONT_SM,
    FONT_TITLE,
)
from gui_app.widgets import (
    _bind_tree_scroll_isolation,
    _card,
    _format_race_display,
    _format_state_display,
    _muted,
    _section_label,
    _stretch_columns,
    _tree_frame,
)
from gui_app.paths import ROOT


class DeepfaceReportsTabMixin:
    """Dedicated queue for stored DeepFace gross-misclass hits + verdict tracking."""

    def _build_deepface_reports(self, tab) -> None:
        tab.configure(fg_color=C["surface"])
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        self._dfr_hits: List[Any] = []
        self._dfr_hits_by_iid: Dict[str, Any] = {}
        self._dfr_selected_iid: Optional[str] = None
        self._dfr_image_refs: list = []

        # Ensure shared verdict store
        if not hasattr(self, "_report_verdicts") or self._report_verdicts is None:
            self._report_verdicts = {}
        if hasattr(self, "_load_report_verdicts"):
            try:
                self._load_report_verdicts()
            except Exception:
                pass

        # ---- Toolbar ----
        top = ctk.CTkFrame(tab, fg_color=C["surface"])
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        bar = ctk.CTkFrame(top, fg_color="transparent")
        bar.pack(fill="x", padx=4, pady=(0, 4))

        ctk.CTkButton(
            bar, text="Refresh hits", width=110,
            command=self._dfr_refresh,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(bar, text="Show", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(4, 4)
        )
        self.dfr_verdict_filter = ctk.StringVar(value="Unconfirmed")
        ctk.CTkComboBox(
            bar, variable=self.dfr_verdict_filter, width=150,
            values=["Unconfirmed", "Confirmed incorrect", "Confirmed correct", "Skip", "All"],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
            command=lambda _v: self._dfr_apply_filters(),
        ).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(bar, text="Face", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(4, 4)
        )
        self.dfr_face_filter = ctk.StringVar(value="All")
        ctk.CTkComboBox(
            bar, variable=self.dfr_face_filter, width=120,
            values=["All", "black", "indian", "asian", "hispanic", "middle_eastern", "white"],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
            command=lambda _v: self._dfr_apply_filters(),
        ).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(bar, text="State", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(4, 4)
        )
        self.dfr_state = ctk.CTkEntry(
            bar, width=56, placeholder_text="All",
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        )
        self.dfr_state.pack(side="left", padx=(0, 8))
        self.dfr_state.bind("<Return>", lambda _e: self._dfr_apply_filters())

        ctk.CTkLabel(bar, text="Min conf", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(4, 4)
        )
        self.dfr_min_conf = ctk.CTkEntry(
            bar, width=56, placeholder_text="0.85",
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        )
        self.dfr_min_conf.insert(0, "0.85")
        self.dfr_min_conf.pack(side="left", padx=(0, 8))
        self.dfr_min_conf.bind("<Return>", lambda _e: self._dfr_apply_filters())

        ctk.CTkButton(
            bar, text="Apply", width=70,
            command=self._dfr_apply_filters,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            bar, text="Next unreviewed", width=120,
            command=self._dfr_next_unreviewed,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="right")

        # Metrics
        metrics = ctk.CTkFrame(top, fg_color="transparent")
        metrics.pack(fill="x", padx=4, pady=(0, 4))
        self.dfr_m_total = ctk.CTkLabel(
            metrics, text="Hits: —", font=FONT_SM, text_color=C["text"]
        )
        self.dfr_m_total.pack(side="left", padx=(0, 12))
        self.dfr_m_open = ctk.CTkLabel(
            metrics, text="Open: —", font=FONT_SM, text_color=C["muted"]
        )
        self.dfr_m_open.pack(side="left", padx=(0, 12))
        self.dfr_m_bad = ctk.CTkLabel(
            metrics, text="Incorrect: —", font=FONT_SM, text_color=C["danger"]
        )
        self.dfr_m_bad.pack(side="left", padx=(0, 12))
        self.dfr_m_ok = ctk.CTkLabel(
            metrics, text="Correct: —", font=FONT_SM, text_color=C["success"]
        )
        self.dfr_m_ok.pack(side="left", padx=(0, 12))
        self.dfr_status = ctk.CTkLabel(
            metrics, text="Stored DeepFace scan hits (from DeepFace → Scan).",
            font=FONT_SM, text_color=C["dim"],
        )
        self.dfr_status.pack(side="left", fill="x", expand=True)

        # ---- Body: list | review ----
        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        list_card = _card(body)
        list_card.grid(row=0, column=0, sticky="nsew", padx=(2, 4), pady=2)
        _section_label(list_card, "DeepFace hits").pack(
            anchor="w", padx=14, pady=(12, 4)
        )
        wrap, tree = _tree_frame(list_card)
        wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        cols = ("name", "state", "listed", "face", "conf", "severity", "verdict", "id")
        tree["columns"] = cols
        tree["show"] = "headings"
        widths = [150, 44, 80, 80, 50, 60, 80, 50]
        labels = {
            "name": "NAME",
            "state": "ST",
            "listed": "LISTED",
            "face": "FACE",
            "conf": "CONF",
            "severity": "SEV",
            "verdict": "VERDICT",
            "id": "ID",
        }
        for c, w in zip(cols, widths):
            tree.heading(c, text=labels.get(c, c.upper()))
            tree.column(c, width=w, minwidth=36, stretch=(c == "name"))
        _stretch_columns(tree, cols, widths)
        self.dfr_tree = tree
        tree.bind("<<TreeviewSelect>>", self._dfr_on_select)
        _bind_tree_scroll_isolation(tree, wrap)

        # Review pane
        rev = _card(body)
        rev.grid(row=0, column=1, sticky="nsew", padx=(4, 2), pady=2)
        _section_label(rev, "Review").pack(anchor="w", padx=14, pady=(12, 4))
        _muted(
            rev,
            "Confirm incorrect = real face/race mismatch. "
            "Confirm correct = not a misclass. Verdicts sync with Browse → Reports.",
        ).pack(anchor="w", padx=14, pady=(0, 6))

        rev_body = ctk.CTkFrame(rev, fg_color="transparent")
        rev_body.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        rev_body.grid_columnconfigure(0, weight=1)

        photo_wrap = ctk.CTkFrame(
            rev_body, fg_color=C["tree_bg"], corner_radius=10, height=240,
        )
        photo_wrap.pack(fill="x", pady=(0, 8))
        photo_wrap.pack_propagate(False)
        self.dfr_photo = ctk.CTkLabel(
            photo_wrap, text="Select a hit", font=FONT_SM, text_color=C["dim"],
        )
        self.dfr_photo.place(relx=0.5, rely=0.5, anchor="center")

        self.dfr_name = ctk.CTkLabel(
            rev_body, text="—", font=FONT_TITLE, text_color=C["text"], anchor="w",
        )
        self.dfr_name.pack(fill="x")
        self.dfr_meta = ctk.CTkLabel(
            rev_body,
            text="",
            font=FONT_SM,
            text_color=C["muted"],
            anchor="nw",
            justify="left",
            wraplength=360,
        )
        self.dfr_meta.pack(fill="x", pady=(4, 6))
        self.dfr_verdict_lbl = ctk.CTkLabel(
            rev_body, text="", font=FONT_BOLD, text_color=C["dim"], anchor="w",
        )
        self.dfr_verdict_lbl.pack(fill="x", pady=(0, 8))

        btns = ctk.CTkFrame(rev, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(0, 12))
        self.dfr_btn_bad = ctk.CTkButton(
            btns, text="Confirmed incorrect", width=150,
            command=lambda: self._dfr_set_verdict("confirmed"),
            fg_color="#5c3030", hover_color="#7a4040", text_color=C["text"],
            state="disabled",
        )
        self.dfr_btn_bad.pack(side="left", padx=(0, 6))
        self.dfr_btn_ok = ctk.CTkButton(
            btns, text="Confirmed correct", width=140,
            command=lambda: self._dfr_set_verdict("correct"),
            fg_color="#2a4a38", hover_color="#356348", text_color=C["text"],
            state="disabled",
        )
        self.dfr_btn_ok.pack(side="left", padx=(0, 6))
        self.dfr_btn_skip = ctk.CTkButton(
            btns, text="Skip", width=70,
            command=lambda: self._dfr_set_verdict("skip"),
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["muted"],
            border_width=1, border_color=C["border"],
            state="disabled",
        )
        self.dfr_btn_skip.pack(side="left")

        self.after(80, self._dfr_refresh)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def _dfr_refresh(self) -> None:
        """Reload hits from DB."""
        try:
            min_c = 0.0
            try:
                min_c = float((self.dfr_min_conf.get() or "0").strip() or "0")
            except ValueError:
                min_c = 0.0
            state = ""
            try:
                state = (self.dfr_state.get() or "").strip() or None
            except Exception:
                state = None

            from scraper.mugshot_ethnicity.scanner import load_deepface_hits_as_misclass
            from scraper.database import Database

            db_path = str(getattr(self, "db_path", None) or "data/offenders.db")
            hits = load_deepface_hits_as_misclass(
                db_path=db_path,
                min_confidence=min_c,
                state=state,
            )
            # Also show scan stats
            try:
                db = Database(db_path)
                try:
                    st = db.count_deepface_scans()
                finally:
                    db.close()
            except Exception:
                st = {"total": 0, "hits": len(hits)}

            self._dfr_all_hits = list(hits)
            self._dfr_apply_filters()
            if hasattr(self, "dfr_status"):
                self.dfr_status.configure(
                    text=(
                        f"Loaded {len(hits):,} DeepFace hits · "
                        f"DB scanned {st.get('total', 0):,}"
                    )
                )
        except Exception as e:
            if hasattr(self, "dfr_status"):
                self.dfr_status.configure(text=f"Load error: {e}")
            messagebox.showerror("DeepFace reports", str(e))

    def _dfr_verdict_key_for_mc(self, mc) -> str:
        rec = mc.record or {}
        rid = rec.get("id")
        if rid is not None and str(rid).strip() != "":
            return f"id:{rid}"
        name = (
            f"{rec.get('first_name') or ''} {rec.get('last_name') or ''}"
        ).strip()
        return f"df:{name}|{mc.likely_ethnicity}"

    def _dfr_get_verdict(self, mc) -> str:
        if hasattr(self, "_verdict_for_mc"):
            try:
                return self._verdict_for_mc(mc)
            except Exception:
                pass
        if not hasattr(self, "_report_verdicts") or self._report_verdicts is None:
            self._report_verdicts = {}
        key = self._dfr_verdict_key_for_mc(mc)
        v = (self._report_verdicts.get(key) or "").strip()
        return v if v in ("confirmed", "correct", "skip") else "unreviewed"

    def _dfr_verdict_label(self, v: str) -> str:
        return {
            "confirmed": "Incorrect",
            "correct": "Correct",
            "skip": "Skip",
            "unreviewed": "—",
        }.get(v or "unreviewed", "—")

    def _dfr_show_filter_key(self) -> str:
        raw = (self.dfr_verdict_filter.get() or "Unconfirmed").strip().lower()
        if "incorrect" in raw:
            return "confirmed"
        if "correct" in raw:
            return "correct"
        if raw.startswith("skip"):
            return "skip"
        if raw == "all":
            return "all"
        return "unreviewed"

    def _dfr_apply_filters(self) -> None:
        all_hits = list(getattr(self, "_dfr_all_hits", None) or [])
        vfilter = self._dfr_show_filter_key()
        face_f = (self.dfr_face_filter.get() or "All").strip().lower()
        try:
            min_c = float((self.dfr_min_conf.get() or "0").strip() or "0")
        except ValueError:
            min_c = 0.0

        filtered = []
        for mc in all_hits:
            if float(mc.confidence or 0) < min_c:
                continue
            v = self._dfr_get_verdict(mc)
            if vfilter != "all" and v != vfilter:
                continue
            if face_f and face_f != "all":
                df = (mc.record or {}).get("_deepface") or {}
                lab = (
                    df.get("predicted_label")
                    or df.get("top_label")
                    or (mc.likely_ethnicity or "")
                ).lower()
                lab = lab.replace(" ", "_").replace("(south_asian)", "").replace("indian_(south_asian)", "indian")
                if "indian" in lab:
                    lab = "indian"
                if face_f not in lab and lab != face_f:
                    # also match face:black@ style in matching_names
                    names = " ".join(mc.matching_names or []).lower()
                    if face_f not in names:
                        continue
            filtered.append(mc)

        filtered.sort(key=lambda m: float(m.confidence or 0), reverse=True)
        self._dfr_hits = filtered
        self._dfr_populate_tree()
        self._dfr_update_metrics()

    def _dfr_populate_tree(self) -> None:
        if not hasattr(self, "dfr_tree"):
            return
        self.dfr_tree.delete(*self.dfr_tree.get_children())
        self._dfr_hits_by_iid = {}
        self._dfr_selected_iid = None
        self._dfr_clear_review()
        for mc in self._dfr_hits:
            rec = mc.record or {}
            name = (
                f"{rec.get('first_name') or ''} {rec.get('last_name') or ''}"
            ).strip() or (rec.get("full_name") or "—")
            df = rec.get("_deepface") or {}
            face = df.get("predicted_label") or df.get("top_label") or "—"
            sev = df.get("severity") or ""
            race = _format_race_display(mc.expected_race) or (mc.expected_race or "—")
            v = self._dfr_get_verdict(mc)
            iid = self.dfr_tree.insert(
                "",
                "end",
                values=(
                    name,
                    _format_state_display(rec),
                    str(race)[:18],
                    face,
                    f"{float(mc.confidence or 0):.2f}",
                    sev,
                    self._dfr_verdict_label(v),
                    rec.get("id") or "",
                ),
            )
            self._dfr_hits_by_iid[iid] = mc
        # Auto-select first unreviewed
        self.after(30, self._dfr_next_unreviewed)

    def _dfr_update_metrics(self) -> None:
        all_hits = list(getattr(self, "_dfr_all_hits", None) or [])
        n_open = n_bad = n_ok = 0
        for mc in all_hits:
            v = self._dfr_get_verdict(mc)
            if v == "unreviewed":
                n_open += 1
            elif v == "confirmed":
                n_bad += 1
            elif v == "correct":
                n_ok += 1
        shown = len(getattr(self, "_dfr_hits", []) or [])
        try:
            self.dfr_m_total.configure(
                text=f"Hits: {len(all_hits):,} · showing {shown:,}"
            )
            self.dfr_m_open.configure(text=f"Open: {n_open:,}")
            self.dfr_m_bad.configure(text=f"Incorrect: {n_bad:,}")
            self.dfr_m_ok.configure(text=f"Correct: {n_ok:,}")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Review
    # ------------------------------------------------------------------
    def _dfr_clear_review(self) -> None:
        try:
            self.dfr_photo.configure(image=None, text="Select a hit")
            self.dfr_name.configure(text="—")
            self.dfr_meta.configure(text="")
            self.dfr_verdict_lbl.configure(text="", text_color=C["dim"])
            for b in (self.dfr_btn_bad, self.dfr_btn_ok, self.dfr_btn_skip):
                b.configure(state="disabled")
        except Exception:
            pass

    def _dfr_on_select(self, _event=None) -> None:
        try:
            sel = self.dfr_tree.selection()
            if not sel:
                return
            iid = sel[0]
            mc = self._dfr_hits_by_iid.get(iid)
            if mc is None:
                return
            self._dfr_show(iid, mc)
        except Exception:
            pass

    def _dfr_show(self, iid: str, mc) -> None:
        self._dfr_selected_iid = iid
        rec = dict(mc.record or {})
        name = (
            f"{rec.get('first_name') or ''} {rec.get('middle_name') or ''} "
            f"{rec.get('last_name') or ''}"
        ).strip() or (rec.get("full_name") or "—")
        name = " ".join(name.split())
        state = _format_state_display(rec)
        race = _format_race_display(mc.expected_race) or (mc.expected_race or "—")
        df = rec.get("_deepface") or {}
        face = df.get("predicted_label") or df.get("top_label") or "—"
        conf = float(mc.confidence or 0)
        sev = df.get("severity") or ""
        reason = df.get("reason") or ""
        crime = ""
        for key in ("crime", "offense_description", "offense_type"):
            if rec.get(key):
                crime = str(rec.get(key)).strip()
                break
        lines = [
            f"LISTED AS: {race}",
            f"Face: {face} @ {conf:.0%}{(' · ' + sev) if sev else ''}",
            f"State: {state}  ·  ID: {rec.get('id') or '—'}",
        ]
        if df.get("scanned_at"):
            lines.append(f"Scanned: {df.get('scanned_at')}")
        if crime:
            lines.append(f"Crime: {crime[:200]}")
        if reason:
            lines.append(str(reason)[:220])
        try:
            self.dfr_name.configure(text=name)
            self.dfr_meta.configure(text="\n".join(lines))
        except Exception:
            pass

        photo = (rec.get("photo_path") or "").strip()
        shown = False
        if photo and Path(photo).is_file():
            try:
                from PIL import Image

                img = Image.open(photo)
                img.thumbnail((340, 320))
                ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
                self._dfr_image_refs.append(ctk_img)
                if len(self._dfr_image_refs) > 20:
                    self._dfr_image_refs = self._dfr_image_refs[-12:]
                self.dfr_photo.configure(image=ctk_img, text="")
                shown = True
            except Exception:
                shown = False
        if not shown:
            try:
                self.dfr_photo.configure(image=None, text="No photo on disk")
            except Exception:
                pass

        v = self._dfr_get_verdict(mc)
        vtxt = {
            "confirmed": "● Confirmed incorrect",
            "correct": "● Confirmed correct",
            "skip": "● Skipped",
            "unreviewed": "○ Unconfirmed — choose below",
        }.get(v, "○ Unconfirmed")
        vcol = {
            "confirmed": C["danger"],
            "correct": C["success"],
            "skip": C["dim"],
            "unreviewed": C["muted"],
        }.get(v, C["muted"])
        try:
            self.dfr_verdict_lbl.configure(text=vtxt, text_color=vcol)
            for b in (self.dfr_btn_bad, self.dfr_btn_ok, self.dfr_btn_skip):
                b.configure(state="normal")
        except Exception:
            pass

    def _dfr_set_verdict(self, verdict: str) -> None:
        iid = getattr(self, "_dfr_selected_iid", None)
        mc = self._dfr_hits_by_iid.get(iid) if iid else None
        if mc is None:
            try:
                sel = self.dfr_tree.selection()
                if sel:
                    iid = sel[0]
                    mc = self._dfr_hits_by_iid.get(iid)
            except Exception:
                pass
        if mc is None:
            return

        if hasattr(self, "_set_verdict_for_mc"):
            try:
                self._set_verdict_for_mc(mc, verdict, save=True)
            except Exception:
                self._dfr_save_verdict_fallback(mc, verdict)
        else:
            self._dfr_save_verdict_fallback(mc, verdict)

        # Update tree cell
        if iid and hasattr(self, "dfr_tree"):
            try:
                vals = list(self.dfr_tree.item(iid, "values") or [])
                # name state listed face conf severity verdict id
                if len(vals) >= 7:
                    vals[6] = self._dfr_verdict_label(verdict)
                    self.dfr_tree.item(iid, values=vals)
            except Exception:
                pass
        self._dfr_show(iid, mc)
        self._dfr_update_metrics()
        self.after(40, self._dfr_next_unreviewed)

    def _dfr_save_verdict_fallback(self, mc, verdict: str) -> None:
        if not hasattr(self, "_report_verdicts") or self._report_verdicts is None:
            self._report_verdicts = {}
        key = self._dfr_verdict_key_for_mc(mc)
        keys = [key]
        rid = (mc.record or {}).get("id")
        if rid is not None:
            keys.append(f"id:{rid}")
        if verdict == "unreviewed":
            for k in keys:
                self._report_verdicts.pop(k, None)
        else:
            for k in keys:
                self._report_verdicts[k] = verdict
        if hasattr(self, "_save_report_verdicts"):
            try:
                self._save_report_verdicts()
                return
            except Exception:
                pass
        path = ROOT / "data" / "report_verdicts.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._report_verdicts, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _dfr_next_unreviewed(self) -> None:
        if not hasattr(self, "dfr_tree"):
            return
        kids = list(self.dfr_tree.get_children() or [])
        if not kids:
            return
        start = 0
        sel = self.dfr_tree.selection()
        if sel:
            try:
                start = kids.index(sel[0]) + 1
            except ValueError:
                start = 0
        order = kids[start:] + kids[:start]
        for iid in order:
            mc = self._dfr_hits_by_iid.get(iid)
            if mc is None:
                continue
            if self._dfr_get_verdict(mc) == "unreviewed":
                self.dfr_tree.selection_set(iid)
                self.dfr_tree.focus(iid)
                self.dfr_tree.see(iid)
                self._dfr_show(iid, mc)
                return
        if hasattr(self, "dfr_status"):
            self.dfr_status.configure(text="No unreviewed hits in current filter")
