"""Write uncaught exceptions to gui_error.log (pythonw has no console)."""
from __future__ import annotations

import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parents[1]
_LOG = _ROOT / "gui_error.log"
_installed = False


def log_exception(prefix: str, exc: BaseException, *, tb: Any = None) -> None:
    """Append a crash report (never raises)."""
    try:
        lines = [
            "",
            "=" * 72,
            f"{datetime.now().isoformat()} {prefix}",
            f"executable={sys.executable}",
            f"version={sys.version.split()[0]}",
        ]
        if tb is not None:
            lines.append("".join(traceback.format_exception(type(exc), exc, tb)))
        else:
            lines.append("".join(traceback.format_exception_only(type(exc), exc)))
            lines.append(traceback.format_exc())
        text = "\n".join(lines)
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")
    except Exception:
        pass


def install_crash_logging() -> None:
    """Hook sys / threading / Tk so crashes leave a trail."""
    global _installed
    if _installed:
        return
    _installed = True

    def _sys_hook(exc_type, exc, tb):
        try:
            log_exception("sys.excepthook", exc, tb=tb)
        finally:
            # Preserve default stderr dump when a console exists
            sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _sys_hook

    if hasattr(threading, "excepthook"):

        def _thread_hook(args) -> None:  # type: ignore[no-untyped-def]
            try:
                if args.exc_type is SystemExit:
                    return
                log_exception(
                    f"threading.excepthook thread={getattr(args.thread, 'name', '?')}",
                    args.exc_value or args.exc_type("?"),
                    tb=args.exc_traceback,
                )
            except Exception:
                pass

        threading.excepthook = _thread_hook  # type: ignore[assignment]

    try:
        import tkinter as tk

        def _tk_hook(exc, val, tb) -> None:
            log_exception("tk.report_callback_exception", val, tb=tb)

        # Class-level so all Tk / CTk windows inherit it
        tk.Tk.report_callback_exception = staticmethod(_tk_hook)  # type: ignore[assignment]
    except Exception:
        pass


def last_error_snippet(max_chars: int = 1200) -> str:
    try:
        if not _LOG.is_file():
            return ""
        text = _LOG.read_text(encoding="utf-8", errors="replace")
        return text[-max_chars:]
    except Exception:
        return ""
