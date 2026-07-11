#!/usr/bin/env python3
"""
Public SOR Archiver — desktop GUI (CustomTkinter).

Dark, high-contrast UI for scrape / search / analysis / NSOPW.
Double-click run_gui.bat (recommended) or gui.py.

Tab UI lives under gui_app/ (lazy-loaded modules). See MODULES.md.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: path + cwd (double-click often starts in System32 / user home)
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
try:
    os.chdir(_ROOT)
except OSError:
    pass


def _fatal(msg: str) -> None:
    """Show an error even when launched with pythonw (no console)."""
    text = msg[:1800]
    try:
        (_ROOT / "gui_error.log").write_text(msg, encoding="utf-8")
    except OSError:
        pass
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "SOR Public Archiver", 0x10)
    except Exception:
        try:
            print(msg, file=sys.stderr)
        except Exception:
            pass


def _ensure_dependencies() -> None:
    """Install missing packages into *this* interpreter (fixes double-click)."""
    need = []
    for mod, pip_name in (
        ("customtkinter", "customtkinter"),
        ("bs4", "beautifulsoup4"),
        ("requests", "requests"),
        ("curl_cffi", "curl_cffi"),
    ):
        try:
            __import__(mod)
        except ImportError:
            need.append(pip_name)
    if not need:
        return
    req = _ROOT / "requirements.txt"
    cmd = [sys.executable, "-m", "pip", "install", "--user"]
    if req.is_file():
        cmd += ["-r", str(req)]
    else:
        cmd += need
    try:
        subprocess.check_call(cmd)
    except Exception as e:
        _fatal(
            "Missing packages and auto-install failed.\n\n"
            f"Interpreter:\n{sys.executable}\n\n"
            f"Need: {', '.join(need)}\n\n"
            f"{e}\n\n"
            "Open a terminal in this folder and run:\n"
            "  python -m pip install -r requirements.txt\n\n"
            "Or double-click run_gui.bat"
        )
        raise SystemExit(1) from e


_ensure_dependencies()


def _start_deepface_setup_background() -> None:
    """Install DeepFace + race weights in a daemon thread (never blocks GUI launch)."""
    def _log(msg: str) -> None:
        try:
            with open(_ROOT / "deepface_setup.log", "a", encoding="utf-8") as f:
                from datetime import datetime

                f.write(f"{datetime.now().isoformat()} {msg.rstrip()}\n")
        except OSError:
            pass

    def _run() -> None:
        try:
            # Delay so first paint / mainloop are not competing with pip/TF
            import time

            time.sleep(3)
            from scraper.mugshot_ethnicity.setup import ensure_deepface

            ensure_deepface(auto_install=True, warm=True, log=_log)
        except Exception as e:
            _log(f"Background DeepFace setup error: {e}")

    try:
        import threading

        threading.Thread(target=_run, name="deepface-setup", daemon=True).start()
    except Exception:
        pass


def main() -> None:
    try:
        from gui_app.shell import ArchiverApp
    except Exception as e:
        import traceback
        _fatal(
            f"Failed to import GUI:\n\n{e}\n\n{traceback.format_exc()}\n\n{sys.executable}"
        )
        raise SystemExit(1) from e
    app = ArchiverApp()
    # After UI exists: install DeepFace in background (does not block VBS/bat)
    _start_deepface_setup_background()
    app.mainloop()


if __name__ == "__main__":
    main()

