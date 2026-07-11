"""DeepFace tab: Scan sub-tab (run mugshot scans) + Setup sub-tab (install/weights)."""
from __future__ import annotations

import csv
import os
import queue
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import customtkinter as ctk
from tkinter import filedialog, ttk

from gui_app.lazy_tabs import LazyTabHost
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
    _muted,
    _section_label,
    _stretch_columns,
    _tree_frame,
    _wire_wide_scroll,
)
from gui_app.paths import ROOT


class DeepfaceTabMixin:
    def _build_deepface(self, tab):
        """Nested sub-tabs: Scan (primary) and Setup (status / weights / install)."""
        tab.configure(fg_color=C["surface"])
        self._df_status_busy = False
        self._df_setup_built = False
        self._df_scan_running = False
        self._df_scan_cancel = False
        self._df_scan_hits: list = []

        sub = ctk.CTkTabview(
            tab,
            fg_color=C["surface"],
            segmented_button_fg_color=C["elevated"],
            segmented_button_selected_color=C["accent_dim"],
            segmented_button_selected_hover_color=C["select"],
            segmented_button_unselected_color=C["elevated"],
            segmented_button_unselected_hover_color=C["panel"],
            text_color=C["text"],
            corner_radius=10,
            border_width=0,
        )
        sub.pack(fill="both", expand=True, padx=6, pady=6)
        self.deepface_tabs = sub

        host = LazyTabHost(sub, on_change=self._on_deepface_subtab_change)
        self._deepface_lazy = host
        host.register("Scan", lambda p: self._build_deepface_scan(p) or True)
        host.register("Setup", lambda p: self._build_deepface_setup(p) or True)

        try:
            sub.set("Scan")
        except Exception:
            pass
        host.ensure("Scan")
        return host

    def _on_deepface_subtab_change(self, name: Optional[str] = None) -> None:
        try:
            name = name or self.deepface_tabs.get()
        except Exception:
            name = "Scan"
        if name == "Setup" and hasattr(self, "_deepface_refresh_status"):
            if getattr(self, "_df_setup_built", False):
                try:
                    self.after(30, self._deepface_refresh_status)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Scan sub-tab
    # ------------------------------------------------------------------
    def _build_deepface_scan(self, tab) -> None:
        """Scan options, start/stop, progress, and results monitor."""
        tab.configure(fg_color=C["surface"])
        sett = getattr(self, "app_settings", {}) or {}

        # Fixed top: options + controls; bottom expands for results
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 4))
        top.grid_columnconfigure(0, weight=1)

        opt = _card(top)
        opt.pack(fill="x", padx=2, pady=2)
        _section_label(opt, "Mugshot gross-misclass scan").pack(
            anchor="w", padx=14, pady=(12, 4)
        )
        _muted(
            opt,
            "Score mugshots with the local Race model. Flags high-confidence face "
            "ethnicity that contradicts the registry race (default: face Black/Indian/Asian "
            "while race is White). Does not use surnames. Configure weights under Setup.",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        grid = ctk.CTkFrame(opt, fg_color="transparent")
        grid.pack(fill="x", padx=14, pady=(0, 8))
        for c in range(4):
            grid.grid_columnconfigure(c, weight=1)

        # Row 0: state, min conf, limit, backend note
        ctk.CTkLabel(grid, text="State filter", font=FONT_SM, text_color=C["muted"]).grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=2
        )
        self.df_scan_state = ctk.CTkEntry(
            grid, width=80, placeholder_text="All",
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        )
        self.df_scan_state.grid(row=1, column=0, sticky="ew", padx=(0, 12), pady=(0, 6))
        st0 = str(sett.get("deepface_scan_state") or "").strip()
        if st0:
            self.df_scan_state.insert(0, st0)

        ctk.CTkLabel(grid, text="Min face confidence", font=FONT_SM, text_color=C["muted"]).grid(
            row=0, column=1, sticky="w", padx=(0, 8), pady=2
        )
        self.df_scan_min_conf = ctk.CTkEntry(
            grid, width=80, placeholder_text="0.85",
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        )
        self.df_scan_min_conf.grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=(0, 6))
        self.df_scan_min_conf.insert(0, str(sett.get("deepface_scan_min_conf") or "0.85"))

        ctk.CTkLabel(grid, text="Max candidates (0=all)", font=FONT_SM, text_color=C["muted"]).grid(
            row=0, column=2, sticky="w", padx=(0, 8), pady=2
        )
        self.df_scan_limit = ctk.CTkEntry(
            grid, width=80, placeholder_text="0",
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        )
        self.df_scan_limit.grid(row=1, column=2, sticky="ew", padx=(0, 12), pady=(0, 6))
        self.df_scan_limit.insert(0, str(sett.get("deepface_scan_limit") or "0"))

        ctk.CTkLabel(grid, text="Recorded race filter", font=FONT_SM, text_color=C["muted"]).grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=2
        )
        self.df_scan_recorded = ctk.CTkEntry(
            grid, placeholder_text="WHITE",
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        )
        self.df_scan_recorded.grid(row=3, column=0, columnspan=2, sticky="ew", padx=(0, 12), pady=(0, 6))
        self.df_scan_recorded.insert(
            0, str(sett.get("deepface_scan_recorded") or "WHITE")
        )

        ctk.CTkLabel(grid, text="Face labels to flag", font=FONT_SM, text_color=C["muted"]).grid(
            row=2, column=2, sticky="w", padx=(0, 8), pady=2
        )
        self.df_scan_faces = ctk.CTkEntry(
            grid, placeholder_text="black,indian,asian",
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        )
        self.df_scan_faces.grid(row=3, column=2, columnspan=2, sticky="ew", padx=(0, 0), pady=(0, 6))
        self.df_scan_faces.insert(
            0, str(sett.get("deepface_scan_faces") or "black,indian,asian")
        )

        # Controls
        ctrl = ctk.CTkFrame(opt, fg_color="transparent")
        ctrl.pack(fill="x", padx=14, pady=(4, 8))
        self.df_scan_start_btn = ctk.CTkButton(
            ctrl, text="Start scan", width=120,
            command=self._deepface_scan_start,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        )
        self.df_scan_start_btn.pack(side="left", padx=(0, 8))
        self.df_scan_stop_btn = ctk.CTkButton(
            ctrl, text="Stop", width=90,
            command=self._deepface_scan_stop,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"], state="disabled",
        )
        self.df_scan_stop_btn.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            ctrl, text="Export hits…", width=110,
            command=self._deepface_scan_export,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            ctrl, text="Clear results", width=100,
            command=self._deepface_scan_clear,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            ctrl, text="Open Setup →", width=110,
            command=self._deepface_goto_setup,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="right")

        self.df_scan_progress = ctk.CTkProgressBar(
            opt, height=8, progress_color=C["accent"], fg_color=C["elevated"],
        )
        self.df_scan_progress.pack(fill="x", padx=14, pady=(0, 4))
        self.df_scan_progress.set(0)
        self.df_scan_status = ctk.CTkLabel(
            opt, text="Ready — configure options and click Start scan",
            font=FONT_SM, text_color=C["dim"], anchor="w",
        )
        self.df_scan_status.pack(fill="x", padx=14, pady=(0, 12))

        # Bottom: results + activity
        bottom = ctk.CTkFrame(tab, fg_color="transparent")
        bottom.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        bottom.grid_columnconfigure(0, weight=3)
        bottom.grid_columnconfigure(1, weight=2)
        bottom.grid_rowconfigure(0, weight=1)

        res_card = _card(bottom)
        res_card.grid(row=0, column=0, sticky="nsew", padx=(2, 4), pady=2)
        res_card.grid_columnconfigure(0, weight=1)
        res_card.grid_rowconfigure(1, weight=1)
        _section_label(res_card, "Hits").pack(anchor="w", padx=14, pady=(12, 4))
        wrap, tree = _tree_frame(res_card)
        wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        cols = ("name", "state", "race", "face", "conf", "severity", "id")
        tree["columns"] = cols
        tree["show"] = "headings"
        widths = [160, 50, 90, 80, 60, 70, 60]
        for c, w in zip(cols, widths):
            tree.heading(c, text=c.upper())
            tree.column(c, width=w, minwidth=40, stretch=(c == "name"))
        _stretch_columns(tree, cols, widths)
        self.df_scan_tree = tree
        _bind_tree_scroll_isolation(tree, wrap)

        log_card = _card(bottom)
        log_card.grid(row=0, column=1, sticky="nsew", padx=(4, 2), pady=2)
        _section_label(log_card, "Scan activity").pack(
            anchor="w", padx=14, pady=(12, 4)
        )
        self.df_scan_log = ctk.CTkTextbox(
            log_card,
            font=FONT_MONO,
            fg_color=C["bg"],
            text_color=C["muted"],
            border_color=C["border"],
            border_width=1,
            corner_radius=8,
        )
        self.df_scan_log.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.df_scan_log.configure(state="disabled")
        self._df_scan_log_queue: queue.Queue = queue.Queue()
        self.after(120, self._deepface_poll_scan_log)

    def _deepface_goto_setup(self) -> None:
        try:
            self.deepface_tabs.set("Setup")
            if hasattr(self, "_deepface_lazy"):
                self._deepface_lazy.ensure("Setup")
        except Exception:
            pass

    def _deepface_scan_log_msg(self, msg: str) -> None:
        try:
            self._df_scan_log_queue.put(str(msg))
        except Exception:
            pass

    def _deepface_poll_scan_log(self) -> None:
        if not hasattr(self, "df_scan_log"):
            return
        try:
            while True:
                msg = self._df_scan_log_queue.get_nowait()
                self.df_scan_log.configure(state="normal")
                ts = datetime.now().strftime("%H:%M:%S")
                self.df_scan_log.insert("end", f"[{ts}] {msg}\n")
                self.df_scan_log.see("end")
                self.df_scan_log.configure(state="disabled")
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.after(200, self._deepface_poll_scan_log)
        except Exception:
            pass

    def _deepface_scan_collect_options(self) -> Dict[str, Any]:
        def _f(entry, default=""):
            try:
                return (entry.get() or "").strip() or default
            except Exception:
                return default

        try:
            min_conf = float(_f(self.df_scan_min_conf, "0.85") or "0.85")
        except ValueError:
            min_conf = 0.85
        try:
            limit = int(float(_f(self.df_scan_limit, "0") or "0"))
        except ValueError:
            limit = 0
        recorded = [
            p.strip()
            for p in _f(self.df_scan_recorded, "WHITE").split(",")
            if p.strip()
        ] or ["WHITE"]
        faces = [
            p.strip()
            for p in _f(self.df_scan_faces, "black,indian,asian").split(",")
            if p.strip()
        ] or ["black", "indian", "asian"]
        state = _f(self.df_scan_state, "") or None
        return {
            "min_confidence": min_conf,
            "limit": max(0, limit),
            "recorded_races": recorded,
            "face_labels": faces,
            "state": state,
        }

    def _deepface_scan_save_options(self) -> None:
        try:
            from scraper.app_settings import load_settings, save_settings, normalize_settings

            opts = self._deepface_scan_collect_options()
            raw = load_settings()
            raw["deepface_scan_state"] = opts["state"] or ""
            raw["deepface_scan_min_conf"] = str(opts["min_confidence"])
            raw["deepface_scan_limit"] = str(opts["limit"])
            raw["deepface_scan_recorded"] = ",".join(opts["recorded_races"])
            raw["deepface_scan_faces"] = ",".join(opts["face_labels"])
            save_settings(raw)
            self.app_settings = normalize_settings(raw)
        except Exception:
            pass

    def _deepface_scan_set_busy(self, busy: bool) -> None:
        self._df_scan_running = busy
        try:
            self.df_scan_start_btn.configure(state="disabled" if busy else "normal")
            self.df_scan_stop_btn.configure(state="normal" if busy else "disabled")
        except Exception:
            pass

    def _deepface_scan_stop(self) -> None:
        self._df_scan_cancel = True
        self._deepface_scan_log_msg("Stop requested — finishing current photo…")
        try:
            self.df_scan_status.configure(
                text="Stopping…", text_color=C["accent"]
            )
        except Exception:
            pass

    def _deepface_scan_clear(self) -> None:
        self._df_scan_hits = []
        try:
            self.df_scan_tree.delete(*self.df_scan_tree.get_children())
            self.df_scan_progress.set(0)
            self.df_scan_status.configure(
                text="Results cleared", text_color=C["dim"]
            )
        except Exception:
            pass

    def _deepface_scan_start(self) -> None:
        if getattr(self, "_df_scan_running", False):
            self._deepface_scan_log_msg("Scan already running")
            return
        self._deepface_scan_save_options()
        opts = self._deepface_scan_collect_options()
        self._df_scan_cancel = False
        self._df_scan_hits = []
        try:
            self.df_scan_tree.delete(*self.df_scan_tree.get_children())
            self.df_scan_progress.set(0)
        except Exception:
            pass
        self._deepface_scan_set_busy(True)
        self._deepface_scan_log_msg(
            f"Starting scan: state={opts['state'] or 'ALL'} "
            f"min_conf={opts['min_confidence']} limit={opts['limit'] or '∞'} "
            f"recorded={opts['recorded_races']} faces={opts['face_labels']}"
        )
        try:
            self.df_scan_status.configure(
                text="Starting…", text_color=C["accent"]
            )
        except Exception:
            pass

        db_path = str(getattr(self, "db_path", None) or "data/offenders.db")
        detector = "retinaface"
        try:
            from scraper.app_settings import load_settings

            detector = str(
                (getattr(self, "app_settings", None) or load_settings()).get(
                    "deepface_detector"
                )
                or "retinaface"
            )
        except Exception:
            pass

        def progress(done: int, total: int) -> None:
            def ui():
                try:
                    frac = (done / total) if total else 0.0
                    self.df_scan_progress.set(min(1.0, max(0.0, frac)))
                    self.df_scan_status.configure(
                        text=f"Scoring {done:,} / {total:,}  ·  hits {len(self._df_scan_hits)}",
                        text_color=C["text"],
                    )
                except Exception:
                    pass

            try:
                self.after(0, ui)
            except Exception:
                pass

        def worker() -> None:
            hits = []
            err = None
            try:
                from scraper.mugshot_ethnicity.setup import (
                    configure_tf_keras_env,
                    ensure_deepface,
                )
                from scraper.mugshot_ethnicity.scorer import (
                    BackendUnavailableError,
                    MugshotEthnicityScorer,
                )
                from scraper.mugshot_ethnicity.scanner import scan_gross_misclassifications

                configure_tf_keras_env()
                ensure_deepface(
                    auto_install=True,
                    warm=True,
                    log=self._deepface_scan_log_msg,
                )
                try:
                    scorer = MugshotEthnicityScorer(
                        backend="deepface",
                        auto_install=False,
                        log=self._deepface_scan_log_msg,
                    )
                except BackendUnavailableError as e:
                    raise RuntimeError(str(e)) from e

                # Detector is read by DeepFaceBackend from settings
                hits = scan_gross_misclassifications(
                    db_path=db_path,
                    scorer=scorer,
                    recorded_races=opts["recorded_races"],
                    face_labels=opts["face_labels"],
                    min_confidence=opts["min_confidence"],
                    limit=opts["limit"],
                    state=opts["state"],
                    progress=progress,
                    log=self._deepface_scan_log_msg,
                    cancel=lambda: bool(getattr(self, "_df_scan_cancel", False)),
                )
            except Exception as e:
                err = e
                self._deepface_scan_log_msg(f"ERROR: {e}")

            def done():
                self._deepface_scan_set_busy(False)
                self._df_scan_hits = list(hits or [])
                try:
                    self.df_scan_tree.delete(*self.df_scan_tree.get_children())
                    for h in self._df_scan_hits:
                        rec = h.record or {}
                        name = (
                            f"{rec.get('first_name') or ''} "
                            f"{rec.get('last_name') or ''}"
                        ).strip()
                        self.df_scan_tree.insert(
                            "",
                            "end",
                            values=(
                                name,
                                rec.get("state") or "—",
                                (h.recorded_race or "—")[:20],
                                h.predicted_label,
                                f"{h.confidence:.2f}",
                                h.severity,
                                rec.get("id") or "",
                            ),
                        )
                    n = len(self._df_scan_hits)
                    if err:
                        self.df_scan_status.configure(
                            text=f"Failed: {err}",
                            text_color=C["danger"],
                        )
                        self.df_scan_progress.set(0)
                    elif getattr(self, "_df_scan_cancel", False):
                        self.df_scan_status.configure(
                            text=f"Stopped — {n:,} hits so far",
                            text_color=C["accent"],
                        )
                    else:
                        self.df_scan_progress.set(1.0)
                        self.df_scan_status.configure(
                            text=f"Done — {n:,} hits",
                            text_color=C["success"],
                        )
                except Exception:
                    pass
                self._deepface_scan_log_msg(
                    f"Scan finished: {len(self._df_scan_hits)} hits"
                    + (f" (error: {err})" if err else "")
                )

            try:
                self.after(0, done)
            except Exception:
                pass

        threading.Thread(target=worker, name="deepface-scan", daemon=True).start()

    def _deepface_scan_export(self) -> None:
        hits = list(getattr(self, "_df_scan_hits", []) or [])
        if not hits:
            self._deepface_scan_log_msg("No hits to export")
            return
        path = filedialog.asksaveasfilename(
            title="Export DeepFace scan hits",
            defaultextension=".csv",
            filetypes=[
                ("CSV", "*.csv"),
                ("JSON", "*.json"),
                ("All", "*.*"),
            ],
            initialfile=f"deepface_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        if not path:
            return
        try:
            p = Path(path)
            if p.suffix.lower() == ".json":
                import json

                p.write_text(
                    json.dumps([h.to_dict() for h in hits], indent=2),
                    encoding="utf-8",
                )
            else:
                with open(p, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow([
                        "id", "name", "state", "recorded_race", "predicted_label",
                        "confidence", "severity", "reason", "photo_path",
                    ])
                    for h in hits:
                        rec = h.record or {}
                        w.writerow([
                            rec.get("id"),
                            f"{rec.get('first_name') or ''} {rec.get('last_name') or ''}".strip(),
                            rec.get("state"),
                            h.recorded_race,
                            h.predicted_label,
                            f"{h.confidence:.4f}",
                            h.severity,
                            h.reason,
                            getattr(h.face, "photo_path", None),
                        ])
            self._deepface_scan_log_msg(f"Exported {len(hits)} hits → {p}")
        except Exception as e:
            self._deepface_scan_log_msg(f"Export failed: {e}")

    # ------------------------------------------------------------------
    # Setup sub-tab (status / install / weights)
    # ------------------------------------------------------------------
    def _build_deepface_setup(self, tab) -> None:
        """Build scrollable status / options / weights UI (lazy, no TF import)."""
        if getattr(self, "_df_setup_built", False):
            return
        self._df_setup_built = True
        # Keep _df_built alias for shell refresh guards
        self._df_built = True
        self._df_tab = tab

        # Scrollable host fills the tab; mouse-wheel works over full area
        root = ctk.CTkScrollableFrame(
            tab,
            fg_color=C["surface"],
            corner_radius=0,
            border_width=0,
        )
        root.pack(fill="both", expand=True, padx=8, pady=8)
        _wire_wide_scroll(tab, root)
        self._df_scroll = root

        # --- Status (top, full width) ---
        status_card = _card(root)
        status_card.pack(fill="x", padx=4, pady=(4, 8))
        _section_label(status_card, "DeepFace status").pack(
            anchor="w", padx=14, pady=(12, 4)
        )
        _muted(
            status_card,
            "Local open-source face race model (no cloud). Used by mugshot verify/scan.",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        self.df_status_installed = ctk.CTkLabel(
            status_card, text="Installed: —", font=FONT_SM, text_color=C["text"], anchor="w",
        )
        self.df_status_installed.pack(fill="x", padx=14, pady=2)
        self.df_status_backend = ctk.CTkLabel(
            status_card, text="Backend: —", font=FONT_SM, text_color=C["text"], anchor="w",
        )
        self.df_status_backend.pack(fill="x", padx=14, pady=2)
        self.df_status_backends = ctk.CTkLabel(
            status_card, text="Available: —", font=FONT_SM, text_color=C["muted"], anchor="w",
        )
        self.df_status_backends.pack(fill="x", padx=14, pady=2)
        self.df_status_python = ctk.CTkLabel(
            status_card,
            text=f"Interpreter: {sys.executable}",
            font=FONT_MONO,
            text_color=C["dim"],
            anchor="w",
            wraplength=900,
            justify="left",
        )
        self.df_status_python.pack(fill="x", padx=14, pady=(2, 4))
        self.df_status_weights = ctk.CTkLabel(
            status_card, text="Weights cache: —", font=FONT_SM, text_color=C["muted"], anchor="w",
        )
        self.df_status_weights.pack(fill="x", padx=14, pady=(0, 10))

        btn_row = ctk.CTkFrame(status_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkButton(
            btn_row, text="Refresh status", width=120,
            command=self._deepface_refresh_status,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Open setup log", width=120,
            command=self._deepface_open_log,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Open weights folder", width=140,
            command=self._deepface_open_weights_dir,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left")

        # --- Options (full width) ---
        opt_card = _card(root)
        opt_card.pack(fill="x", padx=4, pady=(0, 8))
        _section_label(opt_card, "Setup options").pack(
            anchor="w", padx=14, pady=(12, 4)
        )
        _muted(
            opt_card,
            "Controls automatic install when the app starts and when mugshot tools run. "
            "Does not block the VBS launcher.",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        sett = getattr(self, "app_settings", {}) or {}
        self.df_auto_setup = ctk.BooleanVar(
            value=bool(sett.get("deepface_auto_setup", True))
        )
        self.df_auto_warm = ctk.BooleanVar(
            value=bool(sett.get("deepface_auto_warm", True))
        )
        ctk.CTkCheckBox(
            opt_card,
            text="Auto-install DeepFace on app start (background)",
            variable=self.df_auto_setup,
            font=FONT_SM,
            text_color=C["text"],
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            border_color=C["border"],
            checkmark_color=C["bg"],
            command=self._deepface_save_options,
        ).pack(anchor="w", padx=14, pady=4)
        ctk.CTkCheckBox(
            opt_card,
            text="Warm selected weights after install (download once to ~/.deepface/weights)",
            variable=self.df_auto_warm,
            font=FONT_SM,
            text_color=C["text"],
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            border_color=C["border"],
            checkmark_color=C["bg"],
            command=self._deepface_save_options,
        ).pack(anchor="w", padx=14, pady=(0, 10))

        act = ctk.CTkFrame(opt_card, fg_color="transparent")
        act.pack(fill="x", padx=14, pady=(0, 8))
        self.df_install_btn = ctk.CTkButton(
            act, text="Install / repair packages", width=160,
            command=lambda: self._deepface_run_setup(warm=True),
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        )
        self.df_install_btn.pack(side="left", padx=(0, 8))
        self.df_warm_btn = ctk.CTkButton(
            act, text="Download selected weights", width=170,
            command=self._deepface_download_selected_weights,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        )
        self.df_warm_btn.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            act, text="Packages only (no weights)", width=160,
            command=lambda: self._deepface_run_setup(warm=False),
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left")

        self.df_job_status = ctk.CTkLabel(
            opt_card, text="", font=FONT_SM, text_color=C["dim"], anchor="w",
        )
        self.df_job_status.pack(fill="x", padx=14, pady=(0, 12))

        # --- Weights / detector selection ---
        w_card = _card(root)
        w_card.pack(fill="x", padx=4, pady=(0, 8))
        _section_label(w_card, "Weights & face detector").pack(
            anchor="w", padx=14, pady=(12, 4)
        )
        from scraper.mugshot_ethnicity.weights_catalog import (
            DETECTOR_OPTIONS,
            DOWNLOAD_GUIDANCE,
            WEIGHT_MODELS,
            detector_dropdown_label,
            detector_local_status,
            explain_detector,
            explain_weight,
            weight_local_status,
        )

        guide = ctk.CTkLabel(
            w_card,
            text=DOWNLOAD_GUIDANCE,
            font=FONT_SM,
            text_color=C["muted"],
            anchor="w",
            justify="left",
            wraplength=920,
        )
        guide.pack(fill="x", padx=14, pady=(0, 10))

        det_default = str(sett.get("deepface_detector") or "retinaface")
        # Dropdown shows name + VRAM + local download status
        det_labels = [detector_dropdown_label(d) for d in DETECTOR_OPTIONS]
        det_id_by_label = {
            detector_dropdown_label(d): d["id"] for d in DETECTOR_OPTIONS
        }
        label_by_det_id = {
            d["id"]: detector_dropdown_label(d) for d in DETECTOR_OPTIONS
        }
        self._df_det_id_by_label = det_id_by_label
        self._df_label_by_det_id = label_by_det_id
        self._df_detector_options = DETECTOR_OPTIONS

        det_row = ctk.CTkFrame(w_card, fg_color="transparent")
        det_row.pack(fill="x", padx=14, pady=(0, 6))
        ctk.CTkLabel(
            det_row,
            text="Face detector (one only · VRAM · download status)",
            font=FONT_SM,
            text_color=C["muted"],
        ).pack(side="left", padx=(0, 8))
        self.df_detector_var = ctk.StringVar(
            value=label_by_det_id.get(det_default, det_labels[0])
        )
        self.df_detector_combo = ctk.CTkComboBox(
            det_row,
            variable=self.df_detector_var,
            values=det_labels,
            width=480,
            fg_color=C["bg"],
            border_color=C["border"],
            button_color=C["elevated"],
            text_color=C["text"],
            dropdown_fg_color=C["panel"],
            command=self._deepface_on_detector_change,
        )
        self.df_detector_combo.pack(side="left")

        det_st = detector_local_status(det_default)
        self.df_detector_status = ctk.CTkLabel(
            det_row,
            text=det_st.get("label") or "",
            font=FONT_SM,
            text_color=C["success"] if det_st.get("downloaded") else C["danger"],
            anchor="w",
        )
        self.df_detector_status.pack(side="left", padx=(12, 0))

        self.df_detector_help = ctk.CTkLabel(
            w_card,
            text=explain_detector(det_default),
            font=FONT_SM,
            text_color=C["dim"],
            anchor="w",
            justify="left",
            wraplength=920,
        )
        self.df_detector_help.pack(fill="x", padx=14, pady=(0, 10))

        ctk.CTkLabel(
            w_card,
            text=(
                "Model weights (check boxes, then Download selected weights). "
                "Green “Downloaded” = file present under ~/.deepface/weights. "
                "Race alone is enough for ethnicity tools."
            ),
            font=FONT_SM,
            text_color=C["muted"],
            anchor="w",
            wraplength=920,
            justify="left",
        ).pack(fill="x", padx=14, pady=(4, 4))

        saved_models = {
            p.strip()
            for p in str(sett.get("deepface_weight_models") or "Race").split(",")
            if p.strip()
        }
        if "Race" not in saved_models:
            saved_models.add("Race")

        self._df_weight_vars: Dict[str, ctk.BooleanVar] = {}
        self._df_weight_status_labels: Dict[str, ctk.CTkLabel] = {}
        self._df_weight_summary_labels: Dict[str, ctk.CTkLabel] = {}
        weights_frame = ctk.CTkFrame(w_card, fg_color="transparent")
        weights_frame.pack(fill="x", padx=10, pady=(0, 6))

        # Two columns of checkboxes
        left_col = ctk.CTkFrame(weights_frame, fg_color="transparent")
        right_col = ctk.CTkFrame(weights_frame, fg_color="transparent")
        left_col.pack(side="left", fill="both", expand=True, padx=(4, 8))
        right_col.pack(side="left", fill="both", expand=True, padx=(8, 4))

        for i, m in enumerate(WEIGHT_MODELS):
            parent = left_col if i % 2 == 0 else right_col
            mid = m["id"]
            var = ctk.BooleanVar(value=(mid in saved_models) or bool(m.get("required")))
            self._df_weight_vars[mid] = var
            row = ctk.CTkFrame(parent, fg_color=C["elevated"], corner_radius=8)
            row.pack(fill="x", pady=3)
            vram = m.get("vram_short") or m.get("vram") or ""
            size = m.get("size") or ""
            st = weight_local_status(mid)
            st_label = st.get("label") or "Not downloaded"
            st_ok = bool(st.get("downloaded"))

            head = ctk.CTkFrame(row, fg_color="transparent")
            head.pack(fill="x", padx=10, pady=(8, 2))
            cb = ctk.CTkCheckBox(
                head,
                text=f"{m['label']}  ·  {size}" + (f"  ·  {vram}" if vram else ""),
                variable=var,
                font=FONT_SM,
                text_color=C["text"],
                fg_color=C["accent"],
                hover_color=C["accent_hover"],
                border_color=C["border"],
                checkmark_color=C["bg"],
                command=lambda mid=mid: self._deepface_on_weight_toggle(mid),
            )
            cb.pack(side="left", anchor="w")
            if m.get("required"):
                try:
                    cb.configure(state="disabled")
                except Exception:
                    pass
            badge = ctk.CTkLabel(
                head,
                text=("✓ " + st_label) if st_ok else st_label,
                font=FONT_SM,
                text_color=C["success"] if st_ok else C["danger"],
                anchor="e",
            )
            badge.pack(side="right", padx=(8, 0))
            self._df_weight_status_labels[mid] = badge

            cat = m.get("category") or ""
            cat_note = {
                "attribute": "Attribute model",
                "recognition": "Identity model (not race)",
            }.get(cat, cat)
            sum_lbl = ctk.CTkLabel(
                row,
                text=f"{m['summary']}\n{cat_note} · disk {size} · load {vram}",
                font=FONT_SM,
                text_color=C["dim"],
                anchor="w",
                wraplength=420,
                justify="left",
            )
            sum_lbl.pack(fill="x", padx=14, pady=(0, 8))
            self._df_weight_summary_labels[mid] = sum_lbl

        self.df_weight_help = ctk.CTkLabel(
            w_card,
            text=explain_weight("Race"),
            font=FONT_SM,
            text_color=C["muted"],
            anchor="nw",
            justify="left",
            wraplength=920,
        )
        self.df_weight_help.pack(fill="x", padx=14, pady=(4, 12))

        # --- Activity log (scrolls with page; tall enough to read) ---
        log_card = _card(root)
        log_card.pack(fill="x", padx=4, pady=(0, 8))
        _section_label(log_card, "Setup activity").pack(
            anchor="w", padx=14, pady=(12, 4)
        )
        self.df_log = ctk.CTkTextbox(
            log_card,
            height=220,
            font=FONT_MONO,
            fg_color=C["bg"],
            text_color=C["muted"],
            border_color=C["border"],
            border_width=1,
            corner_radius=8,
        )
        self.df_log.pack(fill="x", expand=False, padx=12, pady=(0, 12))
        self.df_log.configure(state="disabled")
        self._df_log_queue: queue.Queue = queue.Queue()
        self._df_setup_running = False

        self.after(50, self._deepface_refresh_status)
        self.after(100, self._deepface_poll_log)
        # Re-bind wheel after children exist (wheel is delivered to widget under cursor)
        self.after(150, lambda: self._deepface_bind_scroll_children(tab, root))

    def _deepface_bind_scroll_children(self, tab, scroll_frame) -> None:
        """Fast mouse-wheel scrolling over the full DeepFace tab content."""
        try:
            canvas = scroll_frame._parent_canvas  # type: ignore[attr-defined]
        except Exception:
            return

        # Fraction of the visible page to move per wheel notch (~18% feels snappy)
        PAGE_FRAC = 0.18

        def _scroll_by_notches(notches: int) -> None:
            if notches == 0:
                return
            try:
                first, last = canvas.yview()
            except Exception:
                canvas.yview_scroll(notches * 12, "units")
                return
            page = max(last - first, 0.05)
            # Move ~PAGE_FRAC of the visible page per notch (not tiny Tk "units")
            step = notches * max(PAGE_FRAC * page, 0.08)
            try:
                canvas.yview_moveto(max(0.0, min(1.0, first + step)))
            except Exception:
                canvas.yview_scroll(notches * 12, "units")

        def _wheel(event):
            delta = getattr(event, "delta", 0) or 0
            if delta:
                # Windows: multiples of 120; high-res trackpads may send smaller values
                if abs(delta) >= 120:
                    notches = int(-delta / 120)
                else:
                    notches = -1 if delta > 0 else 1
                if notches == 0:
                    notches = -1 if delta > 0 else 1
                _scroll_by_notches(notches)
            else:
                num = getattr(event, "num", 0)
                if num == 4:
                    _scroll_by_notches(-1)
                elif num == 5:
                    _scroll_by_notches(1)
            return "break"

        def _walk(w):
            try:
                # Don't steal wheel from the activity textbox (it scrolls itself)
                if w is getattr(self, "df_log", None):
                    return
            except Exception:
                pass
            try:
                # Replace prior bindings so we don't stack slow + fast handlers
                w.bind("<MouseWheel>", _wheel)
                w.bind("<Button-4>", _wheel)
                w.bind("<Button-5>", _wheel)
            except Exception:
                pass
            try:
                for child in w.winfo_children():
                    _walk(child)
            except Exception:
                pass

        try:
            _walk(tab)
            _walk(scroll_frame)
            # Also re-wire the scroll frame's own canvas/parent (wire_wide_scroll is slow)
            for w in (
                tab,
                getattr(scroll_frame, "_parent_frame", None),
                canvas,
                scroll_frame,
            ):
                if w is None:
                    continue
                try:
                    w.bind("<MouseWheel>", _wheel)
                    w.bind("<Button-4>", _wheel)
                    w.bind("<Button-5>", _wheel)
                except Exception:
                    pass
        except Exception:
            pass

    def _deepface_append_log(self, msg: str) -> None:
        try:
            self._df_log_queue.put(str(msg))
        except Exception:
            pass

    def _deepface_poll_log(self) -> None:
        if not hasattr(self, "df_log"):
            return
        try:
            while True:
                msg = self._df_log_queue.get_nowait()
                self.df_log.configure(state="normal")
                ts = datetime.now().strftime("%H:%M:%S")
                self.df_log.insert("end", f"[{ts}] {msg}\n")
                self.df_log.see("end")
                self.df_log.configure(state="disabled")
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.after(200, self._deepface_poll_log)
        except Exception:
            pass

    def _deepface_refresh_status(self) -> None:
        """Update status labels. Keras/TF probe runs off the UI thread."""
        if not hasattr(self, "df_status_installed"):
            return
        if getattr(self, "_df_status_busy", False):
            return
        self._df_status_busy = True

        # Fast UI-thread updates only (no heavy imports)
        try:
            import importlib.util

            pkg = importlib.util.find_spec("deepface") is not None
        except Exception:
            pkg = False

        try:
            self.df_status_installed.configure(
                text=(
                    "Installed: package found — checking runtime…"
                    if pkg
                    else "Installed: No"
                ),
                text_color=C["accent"] if pkg else C["danger"],
            )
            self.df_status_backend.configure(
                text="Preferred backend: checking…",
                text_color=C["dim"],
            )
            self.df_status_backends.configure(text="Available: …")
        except Exception:
            pass

        home = Path.home() / ".deepface" / "weights"
        try:
            if home.is_dir():
                files = [f for f in home.glob("*") if f.is_file()]
                n = len(files)
                size = sum(f.stat().st_size for f in files)
                mb = size / (1024 * 1024)
                self.df_status_weights.configure(
                    text=f"Weights cache: {home}  ·  {n} files  ·  {mb:.1f} MB"
                )
            else:
                self.df_status_weights.configure(
                    text=f"Weights cache: not created yet ({home})"
                )
        except Exception:
            try:
                self.df_status_weights.configure(text=f"Weights cache: {home}")
            except Exception:
                pass

        skip = os.environ.get("SOR_SKIP_DEEPFACE_INSTALL", "").strip().lower() in (
            "1", "true", "yes",
        )
        if skip and hasattr(self, "df_job_status"):
            try:
                self.df_job_status.configure(
                    text="Note: SOR_SKIP_DEEPFACE_INSTALL is set — auto-install disabled in env"
                )
            except Exception:
                pass

        def worker() -> None:
            runtime_ok = False
            runtime_detail = "check failed"
            backends: Dict[str, bool] = {}
            err: Optional[str] = None
            try:
                # Import setup directly — avoid package side effects
                from scraper.mugshot_ethnicity.setup import deepface_runtime_ok
                from scraper.mugshot_ethnicity.scorer import get_available_backends

                runtime_ok, runtime_detail = deepface_runtime_ok()
                backends = get_available_backends()
            except Exception as e:
                err = str(e)

            def apply() -> None:
                self._df_status_busy = False
                if not hasattr(self, "df_status_installed"):
                    return
                try:
                    if err:
                        self.df_status_installed.configure(
                            text=f"Installed: error ({err})",
                            text_color=C["danger"],
                        )
                        return
                    if runtime_ok:
                        inst_txt = "Installed: Yes (runtime OK)"
                        inst_col = C["success"]
                    elif pkg:
                        inst_txt = (
                            f"Installed: package present but broken — {runtime_detail}"
                        )
                        inst_col = C["danger"]
                    else:
                        inst_txt = "Installed: No"
                        inst_col = C["danger"]
                    self.df_status_installed.configure(
                        text=inst_txt, text_color=inst_col
                    )
                    if runtime_ok and backends.get("deepface"):
                        be = "deepface (ready)"
                        col = C["success"]
                    elif backends.get("clip"):
                        be = "clip (fallback)"
                        col = C["accent"]
                    else:
                        be = "none — install / repair required for mugshot tools"
                        col = C["danger"]
                    self.df_status_backend.configure(
                        text=f"Preferred backend: {be}", text_color=col
                    )
                    parts = [
                        f"{k}={'yes' if v else 'no'}"
                        for k, v in sorted(backends.items())
                    ]
                    self.df_status_backends.configure(
                        text="Available: " + ", ".join(parts)
                    )
                    try:
                        self._deepface_refresh_download_badges()
                    except Exception:
                        pass
                except Exception:
                    pass

            try:
                self.after(0, apply)
            except Exception:
                self._df_status_busy = False

        threading.Thread(
            target=worker, name="deepface-status", daemon=True
        ).start()

    def _deepface_selected_weight_ids(self) -> List[str]:
        ids = ["Race"]
        for mid, var in getattr(self, "_df_weight_vars", {}).items():
            try:
                if bool(var.get()) and mid not in ids:
                    ids.append(mid)
            except Exception:
                pass
        return ids

    def _deepface_selected_detector_id(self) -> str:
        label = ""
        try:
            label = (self.df_detector_var.get() or "").strip()
        except Exception:
            pass
        return (getattr(self, "_df_det_id_by_label", {}) or {}).get(label, "retinaface")

    def _deepface_on_detector_change(self, _choice: str = "") -> None:
        from scraper.mugshot_ethnicity.weights_catalog import (
            detector_local_status,
            explain_detector,
        )

        det = self._deepface_selected_detector_id()
        try:
            self.df_detector_help.configure(text=explain_detector(det))
        except Exception:
            pass
        try:
            st = detector_local_status(det)
            if hasattr(self, "df_detector_status"):
                self.df_detector_status.configure(
                    text=st.get("label") or "",
                    text_color=C["success"] if st.get("downloaded") else C["danger"],
                )
        except Exception:
            pass
        self._deepface_save_options()

    def _deepface_refresh_download_badges(self) -> None:
        """Update per-weight / detector “Downloaded” badges from the local cache."""
        try:
            from scraper.mugshot_ethnicity.weights_catalog import (
                DETECTOR_OPTIONS,
                detector_dropdown_label,
                detector_local_status,
                weight_local_status,
            )
        except Exception:
            return

        # Weight cards
        for mid, lbl in list(getattr(self, "_df_weight_status_labels", {}).items()):
            try:
                st = weight_local_status(mid)
                ok = bool(st.get("downloaded"))
                text = st.get("label") or ("Downloaded" if ok else "Not downloaded")
                lbl.configure(
                    text=("✓ " + text) if ok else text,
                    text_color=C["success"] if ok else C["danger"],
                )
            except Exception:
                pass

        # Detector dropdown values + badge (preserve selected id)
        det = self._deepface_selected_detector_id()
        try:
            det_labels = [detector_dropdown_label(d) for d in DETECTOR_OPTIONS]
            det_id_by_label = {
                detector_dropdown_label(d): d["id"] for d in DETECTOR_OPTIONS
            }
            label_by_det_id = {
                d["id"]: detector_dropdown_label(d) for d in DETECTOR_OPTIONS
            }
            self._df_det_id_by_label = det_id_by_label
            self._df_label_by_det_id = label_by_det_id
            new_label = label_by_det_id.get(det, det_labels[0] if det_labels else "")
            if hasattr(self, "df_detector_combo"):
                self.df_detector_combo.configure(values=det_labels)
            if hasattr(self, "df_detector_var") and new_label:
                self.df_detector_var.set(new_label)
            st = detector_local_status(det)
            if hasattr(self, "df_detector_status"):
                self.df_detector_status.configure(
                    text=st.get("label") or "",
                    text_color=C["success"] if st.get("downloaded") else C["danger"],
                )
        except Exception:
            pass

    def _deepface_on_weight_toggle(self, model_id: str = "") -> None:
        from scraper.mugshot_ethnicity.weights_catalog import explain_weight

        mid = model_id or "Race"
        # Race always on
        if mid == "Race" and mid in getattr(self, "_df_weight_vars", {}):
            try:
                self._df_weight_vars["Race"].set(True)
            except Exception:
                pass
        try:
            self.df_weight_help.configure(text=explain_weight(mid))
        except Exception:
            pass
        self._deepface_save_options()

    def _deepface_save_options(self) -> None:
        try:
            from scraper.app_settings import load_settings, save_settings, normalize_settings

            raw = load_settings()
            raw["deepface_auto_setup"] = bool(self.df_auto_setup.get())
            raw["deepface_auto_warm"] = bool(self.df_auto_warm.get())
            raw["deepface_detector"] = self._deepface_selected_detector_id()
            raw["deepface_weight_models"] = ",".join(self._deepface_selected_weight_ids())
            save_settings(raw)
            self.app_settings = normalize_settings(raw)
            self._deepface_append_log(
                f"Saved: auto_setup={raw['deepface_auto_setup']} "
                f"auto_warm={raw['deepface_auto_warm']} "
                f"detector={raw['deepface_detector']} "
                f"weights={raw['deepface_weight_models']}"
            )
        except Exception as e:
            self._deepface_append_log(f"Could not save options: {e}")

    def _deepface_set_busy(self, busy: bool) -> None:
        self._df_setup_running = busy
        state = "disabled" if busy else "normal"
        for name in ("df_install_btn", "df_warm_btn"):
            w = getattr(self, name, None)
            if w is not None:
                try:
                    w.configure(state=state)
                except Exception:
                    pass
        if hasattr(self, "df_job_status"):
            self.df_job_status.configure(
                text="Working… (see activity log)" if busy else ""
            )

    def _deepface_download_selected_weights(self) -> None:
        """Download checked model weights + selected detector into local cache."""
        self._deepface_save_options()
        self._deepface_run_setup(warm=True, install=False, weights_only=True)

    def _deepface_run_setup(
        self,
        *,
        warm: bool = True,
        install: bool = True,
        weights_only: bool = False,
    ) -> None:
        if getattr(self, "_df_setup_running", False):
            self._deepface_append_log("Setup already running")
            return
        self._deepface_set_busy(True)
        models = self._deepface_selected_weight_ids()
        detector = self._deepface_selected_detector_id()
        self._deepface_append_log(
            f"Starting setup (install={install}, warm={warm}, "
            f"detector={detector}, models={models})…"
        )

        def worker():
            ok = False
            try:
                from scraper.mugshot_ethnicity.setup import (
                    ensure_deepface,
                    warm_deepface_models,
                    download_selected_weights,
                    deepface_available,
                )

                if install:
                    ok = ensure_deepface(
                        auto_install=True,
                        warm=False,  # download selected models next
                        log=self._deepface_append_log,
                        force_reinstall=False,
                    )
                    if ok and warm:
                        results = download_selected_weights(
                            models,
                            detector_backend=detector,
                            log=self._deepface_append_log,
                        )
                        ok = bool(results.get("Race") or any(results.values()))
                elif warm or weights_only:
                    if not deepface_available():
                        self._deepface_append_log(
                            "DeepFace not installed — use Install / repair packages first"
                        )
                        ok = False
                    else:
                        results = download_selected_weights(
                            models,
                            detector_backend=detector,
                            log=self._deepface_append_log,
                        )
                        ok = bool(results.get("Race") or any(results.values()))
                else:
                    ok = deepface_available()
            except Exception as e:
                self._deepface_append_log(f"ERROR: {e}")
                ok = False

            def done():
                self._deepface_set_busy(False)
                self._deepface_refresh_status()
                try:
                    self._deepface_refresh_download_badges()
                except Exception:
                    pass
                if hasattr(self, "df_job_status"):
                    self.df_job_status.configure(
                        text="Setup finished OK" if ok else "Setup failed — see log",
                        text_color=C["success"] if ok else C["danger"],
                    )
                self._deepface_append_log(
                    "Done." if ok else "Finished with errors."
                )

            try:
                self.after(0, done)
            except Exception:
                pass

        threading.Thread(target=worker, name="deepface-tab-setup", daemon=True).start()

    def _deepface_open_log(self) -> None:
        path = ROOT / "deepface_setup.log"
        if not path.is_file():
            try:
                path.write_text("# DeepFace setup log\n", encoding="utf-8")
            except OSError:
                pass
        if hasattr(self, "_open_path"):
            self._open_path(path)
        else:
            try:
                os.startfile(str(path))  # type: ignore[attr-defined]
            except Exception:
                pass

    def _deepface_open_weights_dir(self) -> None:
        path = Path.home() / ".deepface" / "weights"
        path.mkdir(parents=True, exist_ok=True)
        if hasattr(self, "_open_path"):
            self._open_path(path)
        else:
            try:
                os.startfile(str(path))  # type: ignore[attr-defined]
            except Exception:
                pass
