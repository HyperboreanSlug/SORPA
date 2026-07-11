"""Throttle CustomTkinter canvas redraws during live window resize.

CTk binds ``<Configure>`` on every widget and re-draws rounded-rect canvases
on each pixel of a drag-resize. With many widgets (tabs, cards, scroll frames)
that freezes the UI. Strategy:

1. Patch ``CTkBaseClass._update_dimensions_event`` so size is tracked but
   ``_draw`` is deferred while the main window is being resized.
2. On the root ``<Configure>`` (size change only), enter suspend mode and
   schedule a single flush ~80 ms after the last size event.
3. Callers should also use ``opaqueresize=False`` on tk.PanedWindow so sash
   drags do not reflow the whole tree every mouse move.
"""
from __future__ import annotations

import weakref
from typing import Any, List, Optional, Set

_installed = False
_suspend = False
_dirty: Set[Any] = set()  # weakref.ref objects
_orig_update_dimensions = None


def install_ctk_resize_throttle() -> bool:
    """Monkey-patch CTk widget dimension events. Safe to call multiple times."""
    global _installed, _orig_update_dimensions
    if _installed:
        return True
    try:
        from customtkinter.windows.widgets.core_widget_classes.ctk_base_class import (
            CTkBaseClass,
        )
    except Exception:
        return False

    _orig_update_dimensions = CTkBaseClass._update_dimensions_event

    def _update_dimensions_event(self, event):  # type: ignore[no-untyped-def]
        try:
            new_w = self._reverse_widget_scaling(event.width)
            new_h = self._reverse_widget_scaling(event.height)
        except Exception:
            if _orig_update_dimensions is not None:
                return _orig_update_dimensions(self, event)
            return None

        if round(self._current_width) == round(new_w) and round(
            self._current_height
        ) == round(new_h):
            return None

        self._current_width = new_w
        self._current_height = new_h

        if _suspend:
            try:
                self._sor_resize_dirty = True  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                _dirty.add(weakref.ref(self))
            except TypeError:
                # Unhashable edge case — draw immediately
                try:
                    self._draw(no_color_updates=True)
                except Exception:
                    pass
            return None

        try:
            self._draw(no_color_updates=True)
        except Exception:
            pass
        return None

    CTkBaseClass._update_dimensions_event = _update_dimensions_event  # type: ignore[method-assign]
    _installed = True
    return True


def _flush_dirty_draws() -> None:
    global _suspend
    _suspend = False
    refs: List[Any] = list(_dirty)
    _dirty.clear()
    for ref in refs:
        try:
            w = ref() if callable(ref) else None
        except Exception:
            w = None
        if w is None:
            continue
        try:
            if not getattr(w, "_sor_resize_dirty", False):
                continue
            w._sor_resize_dirty = False  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            w._draw(no_color_updates=True)
        except Exception:
            pass


def bind_root_resize_throttle(root: Any, settle_ms: int = 80) -> None:
    """Suspend CTk redraws while *root* is being drag-resized; flush after settle."""
    install_ctk_resize_throttle()

    state = {"w": None, "h": None, "after": None}

    def _schedule_flush() -> None:
        aid = state.get("after")
        if aid is not None:
            try:
                root.after_cancel(aid)
            except Exception:
                pass
        try:
            state["after"] = root.after(int(settle_ms), _on_settle)
        except Exception:
            state["after"] = None
            _flush_dirty_draws()

    def _on_settle() -> None:
        state["after"] = None
        _flush_dirty_draws()

    def _on_configure(event) -> None:  # type: ignore[no-untyped-def]
        global _suspend
        try:
            if event.widget is not root:
                return
        except Exception:
            return

        w = getattr(event, "width", None)
        h = getattr(event, "height", None)
        if w is None or h is None:
            return

        # First map / init — record size, do not suspend
        if state["w"] is None:
            state["w"] = w
            state["h"] = h
            return

        # Pure move (same size) — ignore
        if state["w"] == w and state["h"] == h:
            return

        state["w"] = w
        state["h"] = h
        if not _suspend:
            _suspend = True
        _schedule_flush()

    try:
        root.bind("<Configure>", _on_configure, add="+")
    except Exception:
        pass


def is_resize_suspended() -> bool:
    return bool(_suspend)
