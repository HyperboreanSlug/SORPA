"""DeepFace tab: local face-race model install options and status."""
from __future__ import annotations

import os
import queue
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from gui_app.theme import (
    C,
    FONT_BOLD,
    FONT_MONO,
    FONT_SM,
    FONT_TITLE,
)
from gui_app.widgets import _card, _muted, _section_label, _wire_wide_scroll
from gui_app.paths import ROOT


class DeepfaceTabMixin:
    def _build_deepface(self, tab):
        """Full-area DeepFace status / options / activity (scrollable)."""
        tab.configure(fg_color=C["surface"])
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
            explain_detector,
            explain_weight,
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
        # Dropdown shows name + VRAM so the face-detector option itself reports usage
        det_labels = [detector_dropdown_label(d) for d in DETECTOR_OPTIONS]
        det_id_by_label = {
            detector_dropdown_label(d): d["id"] for d in DETECTOR_OPTIONS
        }
        label_by_det_id = {
            d["id"]: detector_dropdown_label(d) for d in DETECTOR_OPTIONS
        }
        self._df_det_id_by_label = det_id_by_label
        self._df_label_by_det_id = label_by_det_id

        det_row = ctk.CTkFrame(w_card, fg_color="transparent")
        det_row.pack(fill="x", padx=14, pady=(0, 6))
        ctk.CTkLabel(
            det_row,
            text="Face detector (one only · VRAM shown)",
            font=FONT_SM,
            text_color=C["muted"],
        ).pack(side="left", padx=(0, 8))
        self.df_detector_var = ctk.StringVar(
            value=label_by_det_id.get(det_default, det_labels[0])
        )
        ctk.CTkComboBox(
            det_row,
            variable=self.df_detector_var,
            values=det_labels,
            width=420,
            fg_color=C["bg"],
            border_color=C["border"],
            button_color=C["elevated"],
            text_color=C["text"],
            dropdown_fg_color=C["panel"],
            command=self._deepface_on_detector_change,
        ).pack(side="left")

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
            cb = ctk.CTkCheckBox(
                row,
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
            cb.pack(anchor="w", padx=10, pady=(8, 2))
            if m.get("required"):
                # Race cannot be unchecked
                try:
                    cb.configure(state="disabled")
                except Exception:
                    pass
            cat = m.get("category") or ""
            cat_note = {
                "attribute": "Attribute model",
                "recognition": "Identity model (not race)",
            }.get(cat, cat)
            ctk.CTkLabel(
                row,
                text=f"{m['summary']}\n{cat_note} · disk {size} · load {vram}",
                font=FONT_SM,
                text_color=C["dim"],
                anchor="w",
                wraplength=420,
                justify="left",
            ).pack(fill="x", padx=14, pady=(0, 8))

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

        self.after(80, self._deepface_refresh_status)
        self.after(150, self._deepface_poll_log)
        # Re-bind wheel after children exist (wheel is delivered to widget under cursor)
        self.after(200, lambda: self._deepface_bind_scroll_children(tab, root))

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
        if not hasattr(self, "df_status_installed"):
            return
        try:
            from scraper.mugshot_ethnicity import (
                deepface_available,
                deepface_runtime_ok,
                get_available_backends,
            )

            pkg = deepface_available()
            runtime_ok, runtime_detail = deepface_runtime_ok()
            if runtime_ok:
                inst_txt = "Installed: Yes (runtime OK)"
                inst_col = C["success"]
            elif pkg:
                inst_txt = f"Installed: package present but broken — {runtime_detail}"
                inst_col = C["danger"]
            else:
                inst_txt = "Installed: No"
                inst_col = C["danger"]
            self.df_status_installed.configure(text=inst_txt, text_color=inst_col)
            backends = get_available_backends()
            # Prefer deepface if available
            if runtime_ok and backends.get("deepface"):
                be = "deepface (ready)"
                col = C["success"]
            elif backends.get("clip"):
                be = "clip (fallback)"
                col = C["accent"]
            else:
                be = "none — install / repair required for mugshot tools"
                col = C["danger"]
            self.df_status_backend.configure(text=f"Preferred backend: {be}", text_color=col)
            parts = [f"{k}={'yes' if v else 'no'}" for k, v in sorted(backends.items())]
            self.df_status_backends.configure(text="Available: " + ", ".join(parts))
        except Exception as e:
            self.df_status_installed.configure(
                text=f"Installed: error ({e})", text_color=C["danger"]
            )

        # Weights dir
        home = Path.home() / ".deepface" / "weights"
        if home.is_dir():
            try:
                n = sum(1 for _ in home.glob("*") if _.is_file())
                size = sum(f.stat().st_size for f in home.glob("*") if f.is_file())
                mb = size / (1024 * 1024)
                self.df_status_weights.configure(
                    text=f"Weights cache: {home}  ·  {n} files  ·  {mb:.1f} MB"
                )
            except Exception:
                self.df_status_weights.configure(text=f"Weights cache: {home}")
        else:
            self.df_status_weights.configure(
                text=f"Weights cache: not created yet ({home})"
            )

        skip = os.environ.get("SOR_SKIP_DEEPFACE_INSTALL", "").strip().lower() in (
            "1", "true", "yes",
        )
        if skip and hasattr(self, "df_job_status"):
            self.df_job_status.configure(
                text="Note: SOR_SKIP_DEEPFACE_INSTALL is set — auto-install disabled in env"
            )

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
        from scraper.mugshot_ethnicity.weights_catalog import explain_detector

        det = self._deepface_selected_detector_id()
        try:
            self.df_detector_help.configure(text=explain_detector(det))
        except Exception:
            pass
        self._deepface_save_options()

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
