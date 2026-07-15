"""Non-blocking top-right DB sync progress indicator for the shell header."""
from __future__ import annotations

import re
from typing import Optional

# Parse "… (45%)" from download log lines for determinate bar progress.
_PCT_RE = re.compile(r"\((\d{1,3}(?:\.\d+)?)%\)")


class ShellSyncUiMixin:
    """Header progress bar + status; never blocks the main UI."""

    def _build_header_sync_indicator(self, header) -> None:
        import customtkinter as ctk
        from gui_app.theme import C, FONT_SM

        self._db_sync_panel = ctk.CTkFrame(header, fg_color="transparent")
        # Not packed until sync starts

        self._db_sync_status_label = ctk.CTkLabel(
            self._db_sync_panel,
            text="Syncing database…",
            font=FONT_SM,
            text_color=C["accent"],
            anchor="e",
            width=200,
        )
        self._db_sync_status_label.pack(side="top", anchor="e", padx=(4, 0))

        self._db_sync_progress = ctk.CTkProgressBar(
            self._db_sync_panel,
            width=168,
            height=8,
            progress_color=C["accent"],
            fg_color=C["elevated"],
            corner_radius=4,
        )
        self._db_sync_progress.pack(side="top", anchor="e", pady=(2, 0), padx=(4, 0))
        self._db_sync_progress.set(0)
        self._db_sync_ui_visible = False
        self._db_sync_indeterminate = False

    def _db_sync_ui_show(self, message: str = "Syncing database…") -> None:
        if getattr(self, "_closing", False):
            return
        panel = getattr(self, "_db_sync_panel", None)
        if panel is None:
            return
        try:
            self._db_sync_ui_visible = True
            if not panel.winfo_ismapped():
                # before=stats → panel is rightmost among side=right widgets
                panel.pack(
                    side="right",
                    padx=(6, 10),
                    pady=4,
                    before=getattr(self, "stats_label", None),
                )
            self._db_sync_status_label.configure(text=(message or "Syncing…")[:64])
            bar = self._db_sync_progress
            bar.set(0)
            try:
                bar.start()
                self._db_sync_indeterminate = True
            except Exception:
                self._db_sync_indeterminate = False
                bar.set(0.15)
            try:
                self.stats_label.configure(text="")
            except Exception:
                pass
        except Exception:
            pass

    def _db_sync_ui_update(self, message: str) -> None:
        """Thread-safe status/progress update (schedules onto UI thread)."""
        if getattr(self, "_closing", False):
            return
        msg = (message or "").strip()
        if not msg:
            return

        def apply() -> None:
            if getattr(self, "_closing", False):
                return
            if not getattr(self, "_db_sync_ui_visible", False):
                self._db_sync_ui_show(msg)
            short = msg if len(msg) <= 56 else "…" + msg[-54:]
            try:
                self._db_sync_status_label.configure(text=short)
            except Exception:
                pass
            m = _PCT_RE.search(msg)
            if not m:
                return
            try:
                frac = max(0.0, min(1.0, float(m.group(1)) / 100.0))
                bar = self._db_sync_progress
                if getattr(self, "_db_sync_indeterminate", False):
                    try:
                        bar.stop()
                    except Exception:
                        pass
                    self._db_sync_indeterminate = False
                bar.set(frac)
            except Exception:
                pass

        try:
            self.after(0, apply)
        except Exception:
            pass

    def _db_sync_ui_hide(self, final_message: Optional[str] = None) -> None:
        if getattr(self, "_closing", False):
            return
        panel = getattr(self, "_db_sync_panel", None)
        try:
            bar = getattr(self, "_db_sync_progress", None)
            if bar is not None and getattr(self, "_db_sync_indeterminate", False):
                try:
                    bar.stop()
                except Exception:
                    pass
            self._db_sync_indeterminate = False
            if panel is not None and panel.winfo_ismapped():
                panel.pack_forget()
            self._db_sync_ui_visible = False
            if hasattr(self, "stats_label"):
                if final_message:
                    text = final_message.strip()
                    if len(text) > 72:
                        text = text[:69] + "…"
                    self.stats_label.configure(text=text or "Ready")
                else:
                    self.stats_label.configure(text="Ready")
        except Exception:
            pass

    def _db_sync_ui_complete_bar(self) -> None:
        """Fill bar to 100% before hide (main thread)."""
        try:
            bar = getattr(self, "_db_sync_progress", None)
            if bar is None:
                return
            if getattr(self, "_db_sync_indeterminate", False):
                try:
                    bar.stop()
                except Exception:
                    pass
                self._db_sync_indeterminate = False
            bar.set(1.0)
        except Exception:
            pass
