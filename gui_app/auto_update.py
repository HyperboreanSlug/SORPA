"""GitHub auto-update on launch: fetch, fast-forward when behind, relaunch.

Uses the local git clone (origin). Skips cleanly when offline, not a repo,
diverged, or disabled. Local data/ is gitignored and never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence, Tuple

_SKIP_ENV = "ARCHIVER_SKIP_AUTO_UPDATE"
_DISABLE_ENV = "ARCHIVER_DISABLE_AUTO_UPDATE"
_FETCH_TIMEOUT = 45
_GIT_TIMEOUT = 90
_LOG_NAME = "auto_update.log"


def maybe_update_and_relaunch(root: Path, *, app_title: str) -> None:
    """If an update is applied, spawn a new process and exit this one."""
    root = Path(root).resolve()
    if not _is_enabled(root):
        return
    try:
        result = _try_update(root)
    except Exception as e:
        _log(root, f"update error: {e}")
        return
    if not result:
        return
    old, new = result
    _log(root, f"updated {old[:10]} -> {new[:10]}; relaunching")
    _notify(app_title, "An update was installed from GitHub.\n\nThe app will reopen.")
    _relaunch(root)
    _log(root, "relaunch failed; continuing with current process")


def _is_enabled(root: Path) -> bool:
    for key in (_DISABLE_ENV, _SKIP_ENV):
        if os.environ.get(key, "").strip().lower() in ("1", "true", "yes", "on"):
            return False
    path = root / "data" / "app_settings.json"
    if not path.is_file():
        return True
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "auto_update_enabled" in raw:
            return bool(raw["auto_update_enabled"])
    except Exception:
        pass
    return True


def _log(root: Path, msg: str) -> None:
    try:
        with (root / _LOG_NAME).open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} {msg}\n")
    except OSError:
        pass


def _notify(title: str, text: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
    except Exception:
        pass


def _git() -> Optional[str]:
    return shutil.which("git")


def _no_window() -> int:
    if sys.platform == "win32":
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return 0


def _run_git(
    root: Path, args: Sequence[str], timeout: float
) -> Tuple[int, str, str]:
    exe = _git()
    if not exe:
        return 127, "", "git not found"
    try:
        p = subprocess.run(
            [exe, *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_no_window(),
        )
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", str(e)


def _try_update(root: Path) -> Optional[Tuple[str, str]]:
    """Return (old_sha, new_sha) when a fast-forward update was applied."""
    if not _git():
        _log(root, "skip: git not on PATH")
        return None
    code, out, _ = _run_git(root, ["rev-parse", "--is-inside-work-tree"], 10)
    if code != 0 or out != "true":
        _log(root, "skip: not a git work tree")
        return None
    code, branch, _ = _run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"], 10)
    if code != 0 or not branch or branch == "HEAD":
        _log(root, "skip: detached HEAD")
        return None
    code, local, _ = _run_git(root, ["rev-parse", "HEAD"], 10)
    if code != 0 or not local:
        return None
    code, _, err = _run_git(root, ["fetch", "--quiet", "origin"], _FETCH_TIMEOUT)
    if code != 0:
        _log(root, f"fetch failed: {err or 'unknown'}")
        return None
    remote = _resolve_remote_tip(root, branch)
    if not remote:
        _log(root, "skip: cannot resolve origin tip")
        return None
    if local == remote:
        _log(root, f"up to date ({local[:10]})")
        return None
    code, _, _ = _run_git(root, ["merge-base", "--is-ancestor", local, remote], 15)
    if code != 0:
        _log(root, f"skip: not strictly behind ({local[:10]} vs {remote[:10]})")
        return None
    code, out, err = _run_git(root, ["merge", "--ff-only", remote], _GIT_TIMEOUT)
    if code != 0:
        _log(root, f"ff-only merge failed: {err or out}")
        return None
    code, new, _ = _run_git(root, ["rev-parse", "HEAD"], 10)
    if code != 0 or not new or new == local:
        return None
    return local, new


def _resolve_remote_tip(root: Path, branch: str) -> str:
    code, upstream, _ = _run_git(
        root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], 10
    )
    candidates = []
    if code == 0 and upstream:
        candidates.append(upstream)
    candidates.extend((f"origin/{branch}", "origin/main", "origin/master"))
    for ref in candidates:
        code, sha, _ = _run_git(root, ["rev-parse", ref], 10)
        if code == 0 and sha:
            return sha
    return ""


def _gui_executable() -> str:
    """Prefer pythonw on Windows so relaunch does not open a console."""
    exe = sys.executable or "python"
    if sys.platform != "win32" or getattr(sys, "frozen", False):
        return exe
    try:
        p = Path(exe)
        if p.name.lower() == "python.exe":
            pyw = p.with_name("pythonw.exe")
            if pyw.is_file():
                return str(pyw)
    except Exception:
        pass
    return exe


def _relaunch(root: Path) -> None:
    env = os.environ.copy()
    env[_SKIP_ENV] = "1"
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, *sys.argv[1:]]
    else:
        argv = list(sys.argv) if sys.argv else [str(root / "gui.py")]
        if argv and not os.path.isabs(argv[0]):
            cand = root / argv[0]
            if cand.is_file():
                argv[0] = str(cand.resolve())
        cmd = [_gui_executable(), *argv]
    kwargs: dict = {"cwd": str(root), "env": env, "close_fds": True}
    if sys.platform == "win32":
        # DETACHED + NEW_GROUP + no console (python.exe would otherwise flash)
        kwargs["creationflags"] = (
            0x00000008 | 0x00000200 | _no_window()
        )
    try:
        subprocess.Popen(cmd, **kwargs)
    except Exception as e:
        _log(root, f"relaunch spawn failed: {e}")
        return
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)
