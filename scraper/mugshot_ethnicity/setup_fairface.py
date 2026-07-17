"""Auto-install FairFace via standalone face-race package + weights."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[2]
_DEPS = [
    "torch>=2.0.0",
    "torchvision>=0.15.0",
    "Pillow>=9.0.0",
    "numpy>=1.22.0,<2.3",
    "gdown>=4.7.0",
]

_install_lock = threading.Lock()
_install_attempted = False
_install_ok: Optional[bool] = None


def _log(log: Optional[Callable[[str], None]], msg: str) -> None:
    if log:
        try:
            log(msg)
        except Exception:
            pass
    else:
        print(msg, flush=True)


def _pip_python() -> str:
    exe = sys.executable or "python"
    try:
        p = Path(exe)
        if p.name.lower() == "pythonw.exe":
            sibling = p.with_name("python.exe")
            if sibling.is_file():
                return str(sibling)
    except Exception:
        pass
    return exe


def _in_venv() -> bool:
    return getattr(sys, "base_prefix", sys.prefix) != sys.prefix or bool(
        os.environ.get("VIRTUAL_ENV")
    )


def face_race_roots() -> List[Path]:
    return [_ROOT.parent / "face-race", Path.home() / "face-race"]


def ensure_face_race_on_path() -> Optional[Path]:
    """Prefer sibling editable tree; return root if found."""
    for root in face_race_roots():
        if (root / "face_race").is_dir():
            s = str(root)
            if s not in sys.path:
                sys.path.insert(0, s)
            return root
    return None


def fairface_package_importable() -> bool:
    ensure_face_race_on_path()
    try:
        return importlib.util.find_spec("face_race") is not None
    except Exception:
        return False


def fairface_runtime_ok() -> Tuple[bool, str]:
    """True when face_race + torch/torchvision/Pillow can score (weights optional)."""
    ensure_face_race_on_path()
    try:
        from face_race import fairface_available, runtime_status

        if not fairface_available():
            st = runtime_status()
            missing = [k for k in ("torch", "torchvision", "pillow") if not st.get(k)]
            return False, "missing deps: " + (", ".join(missing) or str(st))
        return True, "ok"
    except Exception as e:
        return False, f"import failed: {e}"


def fairface_available() -> bool:
    return fairface_runtime_ok()[0]


def _no_window_flags() -> int:
    if sys.platform == "win32":
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return 0


def _pip_install(
    packages_or_req: List[str],
    *,
    log: Optional[Callable[[str], None]],
    retries: int = 3,
) -> bool:
    py = _pip_python()
    cmd = [py, "-m", "pip", "install", "--upgrade"]
    if not _in_venv():
        cmd.append("--user")
    cmd.extend(packages_or_req)
    for attempt in range(1, max(1, retries) + 1):
        _log(log, f"FairFace pip (attempt {attempt}/{retries}):\n  {' '.join(cmd)}")
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=int(os.environ.get("SOR_FAIRFACE_PIP_TIMEOUT", "3600")),
                creationflags=_no_window_flags(),
            )
        except subprocess.TimeoutExpired:
            _log(log, "FairFace pip install timed out")
            return False
        except Exception as e:
            _log(log, f"FairFace pip failed to start: {e}")
            return False
        out = (proc.stderr or "") + "\n" + (proc.stdout or "")
        if proc.returncode == 0:
            _log(log, "FairFace packages installed OK")
            return True
        _log(log, f"FairFace pip exit {proc.returncode}:\n{out[-1600:]}")
        if attempt < retries:
            time.sleep(3.0 * attempt)
    return False


def _warm_weights(log: Optional[Callable[[str], None]]) -> bool:
    ensure_face_race_on_path()
    try:
        from face_race import ensure_ready

        return bool(ensure_ready(download_weights=True, log=log))
    except Exception as e:
        _log(log, f"FairFace weight warm failed: {e}")
        return False


def ensure_fairface(
    *,
    auto_install: bool = True,
    warm: bool = True,
    log: Optional[Callable[[str], None]] = None,
    force_reinstall: bool = False,
) -> bool:
    """
    Make FairFace usable: face-race on path/installed, torch stack, weights.

    Safe to call repeatedly (install attempted at most once per process unless
    *force_reinstall*).
    """
    global _install_attempted, _install_ok

    ok, detail = fairface_runtime_ok()
    if ok and not force_reinstall:
        if warm:
            return _warm_weights(log)
        return True

    if not auto_install:
        _log(log, f"FairFace not ready: {detail}")
        return False

    skip = os.environ.get("SOR_SKIP_FAIRFACE_INSTALL", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if skip:
        _log(log, "SOR_SKIP_FAIRFACE_INSTALL set — skipping FairFace auto-install")
        return False

    with _install_lock:
        if _install_attempted and not force_reinstall:
            ok2 = bool(_install_ok and fairface_runtime_ok()[0])
            if ok2 and warm:
                return _warm_weights(log)
            return ok2

        _install_attempted = True
        _log(log, f"FairFace setup — interpreter: {_pip_python()}")
        if detail and detail != "ok":
            _log(log, f"Pre-install: {detail}")

        root = ensure_face_race_on_path()
        if not fairface_package_importable():
            if root is not None:
                req = root / "requirements.txt"
                if req.is_file():
                    _pip_install(["-r", str(req)], log=log)
                else:
                    _pip_install(list(_DEPS), log=log)
                ok_pkg = _pip_install(["-e", str(root)], log=log)
                if not ok_pkg:
                    _log(log, f"Could not pip install -e {root}")
            else:
                _log(
                    log,
                    "face-race package not found. Clone/install sibling:\n"
                    f"  {_ROOT.parent / 'face-race'}\n"
                    "  or  ~/face-race\n"
                    "  then: pip install -e .",
                )
                _install_ok = False
                return False

        ok, detail = fairface_runtime_ok()
        if not ok:
            root = ensure_face_race_on_path()
            req = (root / "requirements.txt") if root else None
            if req is not None and req.is_file():
                _pip_install(["-r", str(req)], log=log)
            else:
                _pip_install(list(_DEPS), log=log)
            ok, detail = fairface_runtime_ok()

        if not ok:
            _log(log, f"FairFace still not ready: {detail}")
            _install_ok = False
            return False

        _install_ok = True
        _log(log, "FairFace runtime OK")
        if warm:
            return _warm_weights(log)
        return True


def ensure_fairface_background(
    *,
    log: Optional[Callable[[str], None]] = None,
) -> threading.Thread:
    """Daemon thread: install FairFace + download race-7 weights."""

    def _run() -> None:
        try:
            ensure_fairface(auto_install=True, warm=True, log=log)
        except Exception as e:
            _log(log, f"Background FairFace setup error: {e}")

    t = threading.Thread(target=_run, name="fairface-setup", daemon=True)
    t.start()
    return t
