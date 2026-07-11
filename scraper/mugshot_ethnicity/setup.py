"""Ensure DeepFace (local race model) is installed and ready.

Called automatically when mugshot scoring starts with backend auto/deepface.
Installs from ``requirements-vision.txt`` into the current interpreter, then
optionally warms the race model (downloads weights to ``~/.deepface/weights/``).
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, List, Optional

# Package roots (repo root = parents[2] from this file)
_ROOT = Path(__file__).resolve().parents[2]
_VISION_REQ = _ROOT / "requirements-vision.txt"

# pip names if requirements-vision.txt is missing
_FALLBACK_PACKAGES = [
    "deepface>=0.0.93",
    "tensorflow>=2.13.0",
    "tf-keras>=2.15.0",
    "opencv-python-headless>=4.8.0",
    "pillow>=10.0.0",
]

_install_lock = threading.Lock()
_install_attempted = False
_install_ok: Optional[bool] = None
_warm_attempted = False


def _log(log: Optional[Callable[[str], None]], msg: str) -> None:
    if log:
        try:
            log(msg)
        except Exception:
            pass
    else:
        print(msg, flush=True)


def deepface_importable() -> bool:
    """True if ``import deepface`` would succeed."""
    return importlib.util.find_spec("deepface") is not None


def deepface_available() -> bool:
    """True if DeepFace can be imported (module present)."""
    if not deepface_importable():
        return False
    try:
        import deepface  # noqa: F401
        return True
    except Exception:
        return False


def _pip_install(packages_or_req: List[str], *, log: Optional[Callable[[str], None]]) -> bool:
    """Run pip install into *this* interpreter. Returns True on exit 0."""
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade"]
    # Prefer --user when not in a venv (matches gui.py bootstrap)
    in_venv = getattr(sys, "base_prefix", sys.prefix) != sys.prefix or bool(
        os.environ.get("VIRTUAL_ENV")
    )
    if not in_venv:
        cmd.append("--user")
    cmd.extend(packages_or_req)
    _log(log, f"Installing DeepFace stack:\n  {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("SOR_DEEPFACE_PIP_TIMEOUT", "1200")),
        )
    except subprocess.TimeoutExpired:
        _log(log, "DeepFace pip install timed out")
        return False
    except Exception as e:
        _log(log, f"DeepFace pip install failed to start: {e}")
        return False
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-1500:]
        _log(log, f"DeepFace pip install failed (exit {proc.returncode}):\n{tail}")
        return False
    _log(log, "DeepFace packages installed OK")
    return True


def ensure_deepface(
    *,
    auto_install: bool = True,
    warm: bool = True,
    log: Optional[Callable[[str], None]] = None,
    force_reinstall: bool = False,
) -> bool:
    """
    Make DeepFace usable in this process.

    1. If already importable → optionally warm race model → True
    2. Else if auto_install → pip install requirements-vision.txt → re-check
    3. Else False

    Safe to call repeatedly (install attempted at most once per process unless
    *force_reinstall*).
    """
    global _install_attempted, _install_ok, _warm_attempted

    if deepface_available() and not force_reinstall:
        if warm:
            warm_deepface_models(log=log)
        return True

    if not auto_install:
        return False

    with _install_lock:
        if _install_attempted and not force_reinstall:
            ok = bool(_install_ok and deepface_available())
            if ok and warm:
                warm_deepface_models(log=log)
            return ok

        _install_attempted = True
        if deepface_available() and not force_reinstall:
            _install_ok = True
            if warm:
                warm_deepface_models(log=log)
            return True

        env_skip = os.environ.get("SOR_SKIP_DEEPFACE_INSTALL", "").strip() in (
            "1", "true", "yes",
        )
        if env_skip:
            _log(log, "SOR_SKIP_DEEPFACE_INSTALL set — not auto-installing DeepFace")
            _install_ok = False
            return False

        if _VISION_REQ.is_file():
            ok = _pip_install(["-r", str(_VISION_REQ)], log=log)
        else:
            ok = _pip_install(list(_FALLBACK_PACKAGES), log=log)

        # Invalidate import caches after install
        importlib.invalidate_caches()
        # Drop partial imports if any
        for mod in list(sys.modules):
            if mod == "deepface" or mod.startswith("deepface."):
                del sys.modules[mod]

        _install_ok = bool(ok and deepface_available())
        if not _install_ok:
            _log(
                log,
                "DeepFace still not importable after install. "
                f"Interpreter: {sys.executable}\n"
                "Try manually:\n"
                f"  {sys.executable} -m pip install -r requirements-vision.txt",
            )
            return False

        _log(log, "DeepFace import OK")
        if warm:
            warm_deepface_models(log=log)
        return True


def warm_deepface_models(
    *,
    log: Optional[Callable[[str], None]] = None,
) -> bool:
    """
    Download / load the race attribute model into local cache.

    First run may take a few minutes; later runs are fast.
    """
    global _warm_attempted
    if _warm_attempted:
        return True
    if not deepface_available():
        return False
    _warm_attempted = True
    try:
        # Quiet TF logs
        os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
        from deepface import DeepFace

        _log(log, "Warming DeepFace race model (first run downloads weights)…")
        # Prefer build_model when available
        built = False
        if hasattr(DeepFace, "build_model"):
            try:
                DeepFace.build_model("Race")
                built = True
            except Exception:
                try:
                    DeepFace.build_model(model_name="Race")
                    built = True
                except Exception as e:
                    _log(log, f"build_model(Race) note: {e}")
        if not built:
            # Fallback: tiny analyze on a generated solid image triggers download
            try:
                import numpy as np
                from PIL import Image
                import tempfile

                arr = np.zeros((64, 64, 3), dtype=np.uint8)
                arr[:] = (180, 140, 120)
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                    Image.fromarray(arr).save(f.name, format="JPEG")
                    path = f.name
                try:
                    DeepFace.analyze(
                        img_path=path,
                        actions=["race"],
                        enforce_detection=False,
                        detector_backend="opencv",
                        silent=True,
                    )
                finally:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
            except Exception as e:
                _log(log, f"DeepFace warm-up analyze skipped: {e}")
                return False
        _log(log, "DeepFace race model ready (local weights cached)")
        return True
    except Exception as e:
        _log(log, f"DeepFace warm-up failed: {e}")
        return False


def ensure_deepface_background(
    *,
    log: Optional[Callable[[str], None]] = None,
) -> threading.Thread:
    """Start ensure_deepface in a daemon thread (non-blocking GUI startup)."""
    def _run() -> None:
        try:
            ensure_deepface(auto_install=True, warm=True, log=log)
        except Exception as e:
            _log(log, f"Background DeepFace setup error: {e}")

    t = threading.Thread(target=_run, name="deepface-setup", daemon=True)
    t.start()
    return t
