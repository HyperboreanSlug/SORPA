"""RecordSidebar show/clear/bind methods."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from gui_app.shared.record_sidebar_photo import resolve_photo_path
from gui_app.shared.record_sidebar_ui import ACTUAL_RACE_OPTIONS
from gui_app.theme import C


class RecordSidebarShowMixin:
    """Selection binding, show/clear, size adjustments."""

    def bind_after(self, after_fn: Callable[..., Any]) -> None:
        self._after = after_fn
        if not self._pumping:
            self._pumping = True
            self._pump_ui()

    def bind_verdict(
        self, callback: Optional[Callable[[Dict[str, Any], str], None]]
    ) -> None:
        self._on_verdict = callback

    def bind_actual_race(
        self, callback: Optional[Callable[[Dict[str, Any], str], None]]
    ) -> None:
        self._on_actual_race = callback

    def _emit_verdict(self, verdict: str) -> None:
        if not self._record or not self._on_verdict:
            return
        self._on_verdict(self._record, verdict)  # live record, not a copy
        self._apply_verdict_status(self._record)

    def _emit_actual_race(self, choice: str) -> None:
        if self._syncing_actual or not self._record or not self._on_actual_race:
            return
        actual = (choice or self.actual_race.get() or "").strip() or "Unknown"
        self._record["likely_ethnicity"] = actual
        self._on_actual_race(dict(self._record), actual)
        self._fill_text(self._record)

    @staticmethod
    def review_label(record: Optional[Dict[str, Any]]) -> str:
        from scraper.ethnicity_review import ethnicity_review_verdict

        review = ethnicity_review_verdict(record)
        if review == "correct":
            return "Marked: classified correctly"
        if review == "incorrect":
            return "Marked: classified incorrectly"
        return ""

    def _apply_verdict_status(self, record: Optional[Dict[str, Any]]) -> None:
        label = self.review_label(record)
        if "incorrect" in label:
            color = C["danger"]
        elif "correct" in label:
            color = C["success"]
        else:
            color = C["muted"]
        self.verdict_status.configure(text=label or "", text_color=color)

    def _drain_ui_queue(self) -> int:
        drained = 0
        try:
            while True:
                fn = self._ui_q.get_nowait()
                drained += 1
                try:
                    fn()
                except Exception:
                    pass
        except Exception:
            pass
        return drained

    def _pump_ui(self) -> None:
        drained = self._drain_ui_queue()
        if self._after:
            # Fast poll while photo apply callbacks are queued; idle slower.
            delay = 8 if drained else 30
            self._after(delay, self._pump_ui)

    def _schedule(self, fn: Callable[[], None]) -> None:
        self._ui_q.put(fn)
        # Drain ASAP without starting a second continuous pump chain.
        if self._after and not getattr(self, "_flush_pending", False):
            self._flush_pending = True

            def _kick() -> None:
                self._flush_pending = False
                self._drain_ui_queue()

            try:
                self._after(1, _kick)
            except Exception:
                self._flush_pending = False


    def _on_sidebar_configure(self, _event=None) -> None:
        if not self._after:
            return
        if self._resize_after is not None:
            try:
                self.frame.after_cancel(self._resize_after)
            except Exception:
                pass
        self._resize_after = self.frame.after(80, self._apply_photo_slot_size)

    def _apply_photo_slot_size(self) -> None:
        self._resize_after = None
        size = self._target_photo_size()
        changed = size != getattr(self, "photo_size", None)
        self.photo_size = size
        try:
            self.photo.configure(width=size[0], height=size[1])
        except Exception:
            pass
        if self._record and (changed or getattr(self, "_pil_source", None)):
            if hasattr(self, "_refit_current_photo"):
                self._refit_current_photo()
            else:
                self._load_photo(self._record, self._load_token)

    def _target_photo_size(self) -> tuple[int, int]:
        """Photo box from sidebar geometry (width-driven square)."""
        try:
            fw, fh = int(self.frame.winfo_width()), int(self.frame.winfo_height())
        except Exception:
            return getattr(self, "photo_size", (320, 320))
        if fw < 60:
            return getattr(self, "photo_size", (320, 320))
        max_w = max(120, fw - 24)
        max_h = max(120, fh - 340) if fh >= 120 else max_w
        side = max(140, min(int(min(max_w, max_h)), 520))
        return (side, side)

    @staticmethod
    def _marked_race_text(record: Optional[Dict[str, Any]]) -> str:
        from scraper.searcher import format_race_label

        if not record:
            return "Marked race: —"
        label = format_race_label(str(record.get("race") or "").strip())
        if not label or label == "—":
            label = "Unknown"
        return f"Marked race: {label}"

    def clear(self, message: str = "Select a record") -> None:
        self._load_token += 1
        self._record = None
        self._image_ref = None
        self._pil_source = None
        self.photo.configure(image="", text=message)
        self.open_btn.configure(state="disabled")
        self.open_photo_btn.configure(state="disabled")
        self.export_btn.configure(state="disabled")
        self.correct_btn.configure(state="disabled")
        self.incorrect_btn.configure(state="disabled")
        self.actual_race.configure(state="disabled")
        self.race_banner.configure(text="Marked race: —")
        self.verdict_status.configure(text="", text_color=C["muted"])
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("end", message)
        self.details.configure(state="disabled")

    def show(self, record: Optional[Dict[str, Any]]) -> None:
        if not record:
            self.clear()
            return
        prev = self._record
        prev_path = str((prev or {}).get("photo_path") or "").strip()
        new_path = str(record.get("photo_path") or "").strip()
        # Same disk path → keep in-flight/cached load (bg enrich must not cancel it).
        same_photo = bool(prev_path and new_path and prev_path == new_path)
        self._record = dict(record)
        if not same_photo:
            self._load_token += 1
        token = self._load_token
        self.photo_size = self._target_photo_size()
        self.photo.configure(width=self.photo_size[0], height=self.photo_size[1])
        self._fill_text(self._record)
        self.race_banner.configure(text=self._marked_race_text(self._record))
        has_url = bool(str(self._record.get("source_url") or "").strip())
        has_html = bool(
            str(
                self._record.get("report_html_path")
                or self._record.get("html_path")
                or ""
            ).strip()
        )
        self.open_btn.configure(state="normal" if (has_url or has_html) else "disabled")
        photo_path = resolve_photo_path(self._record.get("photo_path"))
        self.open_photo_btn.configure(
            state="normal" if photo_path and photo_path.is_file() else "disabled"
        )
        self.export_btn.configure(state="normal")
        enabled = "normal" if self._on_verdict else "disabled"
        self.correct_btn.configure(state=enabled)
        self.incorrect_btn.configure(state=enabled)
        self._apply_verdict_status(self._record)
        likely_raw = (
            str(
                self._record.get("likely_ethnicity")
                or self._record.get("_misclass_likely")
                or self._record.get("race")
                or "Unknown"
            ).strip()
            or "Unknown"
        )
        opts = list(getattr(self, "_actual_race_options", None) or ACTUAL_RACE_OPTIONS)
        try:
            from gui_app.tabs.browse.misclassify.constants import picker_actual_race

            likely = picker_actual_race(likely_raw, opts)
        except Exception:
            likely = opts[0] if opts else "Unknown"
        if likely not in opts:
            likely = opts[0] if opts else "Unknown"
        self._syncing_actual = True
        try:
            self.actual_race.configure(
                values=opts,
                state="normal" if self._on_actual_race else "disabled",
            )
            self.actual_race.set(likely)
        finally:
            self._syncing_actual = False
        if same_photo:
            # Flags/text only — do not cancel an in-flight decode of this path.
            if getattr(self, "_pil_source", None) is not None and hasattr(
                self, "_refit_current_photo"
            ):
                try:
                    self._refit_current_photo()
                except Exception:
                    pass
            return
        self._load_photo(self._record, token)

