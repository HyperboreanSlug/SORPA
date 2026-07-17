"""Ensure local race models are installed and ready.

Primary: FairFace via ``setup_fairface.ensure_fairface`` (face-race package).
Legacy: DeepFace from ``requirements-vision.txt`` + ``~/.deepface/weights/``.

Hardening:
  * pip always targets *this* process's site-packages (pythonw → python.exe)
  * cross-process file lock so two GUIs cannot fight over WinError 32
  * retries on file-lock / permission pip failures
  * detects numpy ABI mismatches and force-repairs the vision stack
  * sets TF_USE_LEGACY_KERAS so RetinaFace works with TF 2.16+/Keras 3
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Package roots (repo root = parents[2] from this file)
_ROOT = Path(__file__).resolve().parents[2]
_VISION_REQ = _ROOT / "requirements-vision.txt"
_LOCK_PATH = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "SORPA" / "deepface_pip.lock"

# pip names if requirements-vision.txt is missing
_FALLBACK_PACKAGES = [
    "numpy>=1.26.0,<2.3",
    "deepface>=0.0.93",
    "tensorflow>=2.15.0",
    "tf-keras>=2.15.0",
    "opencv-python>=4.8.0",
    "pillow>=10.0.0",
]

# Packages reinstalled on ABI / binary-incompatibility repair
_REPAIR_PACKAGES = [
    "numpy>=1.26.0,<2.3",
    "pandas",
    "h5py",
    "ml_dtypes",
    "keras",
    "tensorflow>=2.15.0",
    "tf-keras>=2.15.0",
    "deepface>=0.0.93",
    "opencv-python>=4.8.0",
    "pillow>=10.0.0",
]


def configure_tf_keras_env() -> None:
    """Must run before any tensorflow/keras import.

    RetinaFace (retina-face package) builds a Functional model with tf.keras /
    tf_keras. Standalone Keras 3 leaves symbolic KerasTensors that cannot be
    fed to TF ops — classic error:

        A KerasTensor cannot be used as input to a TensorFlow function

    TF_USE_LEGACY_KERAS=1 forces TensorFlow to use tf_keras (Keras 2 API).
    """
    os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    # Avoid oneDNN noise / rare numeric issues on CPU
    os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")


# Apply as soon as this module loads (safe if already set)
configure_tf_keras_env()

_install_lock = threading.Lock()
_install_attempted = False
_install_ok: Optional[bool] = None
_warm_attempted = False

# Cache expensive keras/TF probe so the GUI never blocks on every tab click
_runtime_cache_lock = threading.Lock()
_runtime_cache: Optional[Tuple[float, bool, str]] = None
_RUNTIME_CACHE_TTL = 120.0  # seconds


def _log(log: Optional[Callable[[str], None]], msg: str) -> None:
    if log:
        try:
            log(msg)
        except Exception:
            pass
    else:
        print(msg, flush=True)


def _pip_python() -> str:
    """Interpreter for ``-m pip`` (prefer python.exe over pythonw.exe on Windows)."""
    exe = sys.executable or "python"
    try:
        p = Path(exe)
        name = p.name.lower()
        if name == "pythonw.exe":
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


def deepface_importable() -> bool:
    """True if ``import deepface`` would succeed (module present on path)."""
    return importlib.util.find_spec("deepface") is not None


def deepface_available() -> bool:
    """True if the deepface package is on sys.path (fast; no keras/TF load)."""
    return deepface_importable()


def invalidate_runtime_cache() -> None:
    """Clear cached runtime probe (call after install/repair)."""
    global _runtime_cache
    with _runtime_cache_lock:
        _runtime_cache = None


def deepface_runtime_ok(*, force: bool = False) -> Tuple[bool, str]:
    """
    Deeper check: numpy + tensorflow/keras path used by DeepFace race models.

    Returns (ok, detail). Catches the common ``numpy.dtype size changed`` ABI
    break that still allows bare ``import deepface`` to succeed.

    Results are cached briefly — keras/TF import can freeze the UI for many
    seconds if called on the main thread.
    """
    global _runtime_cache
    now = time.time()
    if not force:
        with _runtime_cache_lock:
            if _runtime_cache is not None:
                ts, ok, detail = _runtime_cache
                if now - ts < _RUNTIME_CACHE_TTL:
                    return ok, detail

    if not deepface_importable():
        result = (False, "deepface package not installed")
    else:
        try:
            import numpy as np  # noqa: F401
        except Exception as e:
            result = (False, f"numpy import failed: {e}")
        else:
            try:
                # keras/TF is what actually fails on ABI mismatch (SLOW first time)
                import keras  # noqa: F401
            except Exception as e:
                msg = str(e)
                if "numpy.dtype size changed" in msg or "binary incompatibility" in msg:
                    result = (False, f"numpy ABI mismatch (keras): {msg}")
                else:
                    try:
                        import tensorflow as tf  # noqa: F401
                    except Exception as e2:
                        msg2 = str(e2)
                        if "numpy.dtype size changed" in msg2 or "binary incompatibility" in msg2:
                            result = (False, f"numpy ABI mismatch (tensorflow): {msg2}")
                        else:
                            result = (False, f"tensorflow/keras import failed: {e2}")
                    else:
                        result = (True, "ok")
            else:
                try:
                    import deepface  # noqa: F401
                    result = (True, "ok")
                except Exception as e:
                    result = (False, f"deepface import failed: {e}")

    with _runtime_cache_lock:
        _runtime_cache = (time.time(), result[0], result[1])
    return result


def _clear_ml_modules() -> None:
    """Drop cached ML imports so a reinstall is visible in this process."""
    importlib.invalidate_caches()
    prefixes = (
        "deepface",
        "tensorflow",
        "keras",
        "tf_keras",
        "h5py",
        "pandas",
        "cv2",
        "numpy",
        "ml_dtypes",
        "retinaface",
        "mtcnn",
        "gdown",
    )
    for mod in list(sys.modules):
        if mod == "numpy" or any(
            mod == p or mod.startswith(p + ".") for p in prefixes
        ):
            try:
                del sys.modules[mod]
            except KeyError:
                pass


class _ProcessFileLock:
    """Best-effort exclusive lock across processes (Windows + POSIX)."""

    def __init__(self, path: Path, *, timeout: float = 900.0):
        self.path = path
        self.timeout = timeout
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        start = time.time()
        self._fh = open(self.path, "a+", encoding="utf-8")
        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fh.seek(0)
                self._fh.truncate()
                self._fh.write(f"pid={os.getpid()} exe={sys.executable}\n")
                self._fh.flush()
                return self
            except OSError:
                if time.time() - start >= self.timeout:
                    raise TimeoutError(f"Timed out waiting for DeepFace pip lock: {self.path}")
                time.sleep(1.5)

    def __exit__(self, *exc):
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass
        self._fh = None


def _is_lock_error(text: str) -> bool:
    t = (text or "").lower()
    return any(
        s in t
        for s in (
            "winerror 32",
            "being used by another process",
            "cannot access the file",
            "permission denied",
            "[errno 13]",
            "temporarily unavailable",
        )
    )


def _is_abi_error(text: str) -> bool:
    t = (text or "").lower()
    return "numpy.dtype size changed" in t or "binary incompatibility" in t


def _no_window_flags() -> int:
    """Hide console windows when pip is spawned from pythonw GUI."""
    if sys.platform == "win32":
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return 0


def _pip_install(
    packages_or_req: List[str],
    *,
    log: Optional[Callable[[str], None]],
    force_reinstall: bool = False,
    no_cache: bool = False,
    retries: int = 3,
) -> bool:
    """Run pip install into *this* interpreter's environment."""
    py = _pip_python()
    cmd = [py, "-m", "pip", "install", "--upgrade"]
    if force_reinstall:
        cmd.append("--force-reinstall")
    if no_cache:
        cmd.append("--no-cache-dir")
    if not _in_venv():
        cmd.append("--user")
    cmd.extend(packages_or_req)

    for attempt in range(1, max(1, retries) + 1):
        _log(log, f"Installing DeepFace stack (attempt {attempt}/{retries}):\n  {' '.join(cmd)}")
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=int(os.environ.get("SOR_DEEPFACE_PIP_TIMEOUT", "1800")),
                creationflags=_no_window_flags(),
            )
        except subprocess.TimeoutExpired:
            _log(log, "DeepFace pip install timed out")
            return False
        except Exception as e:
            _log(log, f"DeepFace pip install failed to start: {e}")
            return False

        out = (proc.stderr or "") + "\n" + (proc.stdout or "")
        if proc.returncode == 0:
            _log(log, "DeepFace packages installed OK")
            return True

        tail = out[-1800:]
        _log(log, f"DeepFace pip install failed (exit {proc.returncode}):\n{tail}")
        if attempt < retries and _is_lock_error(out):
            wait = 4.0 * attempt
            _log(log, f"File lock / permission conflict — retrying in {wait:.0f}s …")
            time.sleep(wait)
            continue
        return False
    return False


def _repair_numpy_stack(*, log: Optional[Callable[[str], None]]) -> bool:
    """Force-reinstall numpy + dependents to fix ABI mismatches."""
    _log(
        log,
        "Repairing vision stack (numpy ABI / binary incompatibility). "
        "This reinstalls numpy, pandas, keras, tensorflow, deepface …",
    )
    ok = _pip_install(
        list(_REPAIR_PACKAGES),
        log=log,
        force_reinstall=True,
        no_cache=True,
        retries=3,
    )
    _clear_ml_modules()
    invalidate_runtime_cache()
    return ok


def ensure_deepface(
    *,
    auto_install: bool = True,
    warm: bool = True,
    log: Optional[Callable[[str], None]] = None,
    force_reinstall: bool = False,
) -> bool:
    """
    Make DeepFace usable in this process.

    1. If runtime-ok → optionally warm race model → True
    2. Else if auto_install → pip install (with lock + ABI repair) → re-check
    3. Else False

    Safe to call repeatedly (install attempted at most once per process unless
    *force_reinstall*).
    """
    global _install_attempted, _install_ok, _warm_attempted

    runtime_ok, detail = deepface_runtime_ok()
    if runtime_ok and not force_reinstall:
        if warm:
            warm_deepface_models(log=log)
        return True

    # Importable but ABI-broken — still need repair even if "available"
    needs_repair = _is_abi_error(detail) or (
        deepface_importable() and not runtime_ok and "ABI" in detail
    )

    if not auto_install:
        if not runtime_ok:
            _log(log, f"DeepFace not ready: {detail}")
        return False

    with _install_lock:
        if _install_attempted and not force_reinstall and not needs_repair:
            ok = bool(_install_ok and deepface_runtime_ok()[0])
            if ok and warm:
                warm_deepface_models(log=log)
            return ok

        _install_attempted = True

        runtime_ok, detail = deepface_runtime_ok()
        if runtime_ok and not force_reinstall:
            _install_ok = True
            if warm:
                warm_deepface_models(log=log)
            return True

        env_skip = os.environ.get("SOR_SKIP_DEEPFACE_INSTALL", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if env_skip:
            _log(log, "SOR_SKIP_DEEPFACE_INSTALL set — not auto-installing DeepFace")
            _install_ok = False
            return False

        _log(log, f"Interpreter: {sys.executable}")
        _log(log, f"Pip target:  {_pip_python()}")
        if detail and detail != "ok":
            _log(log, f"Pre-install status: {detail}")

        try:
            with _ProcessFileLock(_LOCK_PATH, timeout=900.0):
                if needs_repair or force_reinstall or _is_abi_error(detail):
                    ok = _repair_numpy_stack(log=log)
                else:
                    if _VISION_REQ.is_file():
                        ok = _pip_install(
                            ["-r", str(_VISION_REQ)],
                            log=log,
                            force_reinstall=force_reinstall,
                            retries=3,
                        )
                    else:
                        ok = _pip_install(
                            list(_FALLBACK_PACKAGES),
                            log=log,
                            force_reinstall=force_reinstall,
                            retries=3,
                        )
                    _clear_ml_modules()
                    invalidate_runtime_cache()
                    runtime_ok, detail = deepface_runtime_ok(force=True)
                    if ok and not runtime_ok and (
                        _is_abi_error(detail) or "ABI" in detail or not deepface_importable()
                    ):
                        _log(log, f"Post-install check failed ({detail}) — running ABI repair")
                        ok = _repair_numpy_stack(log=log)
        except TimeoutError as e:
            _log(log, str(e))
            ok = False
        except Exception as e:
            _log(log, f"DeepFace install lock error: {e}")
            ok = False

        _clear_ml_modules()
        invalidate_runtime_cache()
        runtime_ok, detail = deepface_runtime_ok(force=True)
        _install_ok = bool(ok and runtime_ok)
        if not _install_ok:
            _log(
                log,
                "DeepFace still not ready after install.\n"
                f"  Detail: {detail}\n"
                f"  Interpreter: {sys.executable}\n"
                "Try manually (close other Python apps first):\n"
                f"  {_pip_python()} -m pip install --user --force-reinstall "
                f"--no-cache-dir -r {_VISION_REQ if _VISION_REQ.is_file() else 'requirements-vision.txt'}",
            )
            return False

        _log(log, "DeepFace runtime OK")
        if warm:
            warm_deepface_models(log=log)
        return True


def _model_task(model_id: str) -> str:
    """DeepFace ≥0.0.95 requires an explicit task for build_model."""
    mid = (model_id or "").strip()
    if mid in ("Age", "Gender", "Emotion", "Race"):
        return "facial_attribute"
    detectors = {
        "opencv",
        "ssd",
        "dlib",
        "mtcnn",
        "retinaface",
        "mediapipe",
        "yunet",
        "fastmtcnn",
        "centerface",
        "yolov8",
        "yolov11",
        "yolov12",
    }
    low = mid.lower()
    if low in detectors or low.startswith("yolo"):
        return "face_detector"
    if low in ("fasnet",):
        return "spoofing"
    return "facial_recognition"


def _build_one_model(DeepFace: Any, model_id: str, log: Optional[Callable[[str], None]]) -> bool:
    """Download/build a single DeepFace model by name."""
    _log(log, f"Downloading / building weights: {model_id} …")
    try:
        if not hasattr(DeepFace, "build_model"):
            _log(log, f"  build_model unavailable for {model_id}")
            return False
        task = _model_task(model_id)
        # New API: build_model(model_name, task=...)
        try:
            DeepFace.build_model(model_id, task=task)
            _log(log, f"  OK: {model_id} (task={task})")
            return True
        except TypeError:
            pass
        # Older API: build_model(model_name) only
        try:
            DeepFace.build_model(model_id)
            _log(log, f"  OK: {model_id}")
            return True
        except TypeError:
            DeepFace.build_model(model_name=model_id)
            _log(log, f"  OK: {model_id}")
            return True
    except Exception as e:
        msg = str(e)
        _log(log, f"  FAIL {model_id}: {e}")
        if _is_abi_error(msg):
            raise
        return False


def download_selected_weights(
    model_ids: Optional[List[str]] = None,
    *,
    detector_backend: str = "retinaface",
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, bool]:
    """
    Download selected DeepFace model weights into ``~/.deepface/weights/``.

    Always attempts Race if list is empty. Detectors are exercised via a tiny
    analyze() call so their weights are also fetched when needed.
    """
    from scraper.mugshot_ethnicity.weights_catalog import default_selected_weights

    ok, detail = deepface_runtime_ok()
    if not ok:
        _log(log, f"DeepFace not ready — cannot download weights ({detail})")
        return {}

    models = list(model_ids or default_selected_weights())
    if "Race" not in models:
        models.insert(0, "Race")

    configure_tf_keras_env()
    from deepface import DeepFace

    results: Dict[str, bool] = {}
    for mid in models:
        results[mid] = _build_one_model(DeepFace, mid, log)

    # Warm selected detector: download weights + one analyze pass
    det = (detector_backend or "opencv").strip().lower() or "opencv"
    results[f"detector:{det}"] = _warm_detector(DeepFace, det, log=log)

    ok_n = sum(1 for v in results.values() if v)
    _log(log, f"Weight download finished: {ok_n}/{len(results)} succeeded")
    return results


def _short_err(exc: BaseException, limit: int = 220) -> str:
    msg = str(exc).replace("\n", " ").strip()
    if len(msg) > limit:
        return msg[: limit - 1] + "…"
    return msg


def _warm_detector(
    DeepFace: Any,
    det: str,
    *,
    log: Optional[Callable[[str], None]] = None,
) -> bool:
    """Download detector weights and smoke-test with a tiny image.

    RetinaFace requires TF_USE_LEGACY_KERAS (set by configure_tf_keras_env).
    """
    configure_tf_keras_env()
    det = (det or "opencv").strip().lower() or "opencv"
    _log(log, f"Warming detector backend: {det} …")

    # 1) Prefer explicit build_model (downloads weights without full analyze)
    if det != "opencv":
        try:
            if hasattr(DeepFace, "build_model"):
                try:
                    DeepFace.build_model(det, task="face_detector")
                except TypeError:
                    DeepFace.build_model(det)
            _log(log, f"  OK detector weights: {det}")
        except Exception as e:
            _log(log, f"  Detector build note ({det}): {_short_err(e)}")
            # Still try analyze — some backends only load on first use

    # 2) Smoke-test analyze (proves end-to-end path for race tools)
    try:
        import tempfile

        import numpy as np
        from PIL import Image

        # Slightly larger than 96px — some detectors reject tiny inputs
        arr = np.zeros((160, 160, 3), dtype=np.uint8)
        arr[:] = (180, 140, 120)
        # Draw a simple face-like blob so Haar/SSD have something to find
        arr[40:120, 40:120] = (210, 180, 160)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            Image.fromarray(arr).save(f.name, format="JPEG")
            path = f.name
        try:
            DeepFace.analyze(
                img_path=path,
                actions=["race"],
                enforce_detection=False,
                detector_backend=det,
                silent=True,
            )
            _log(log, f"  OK detector analyze: {det}")
            return True
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    except Exception as e:
        msg = _short_err(e)
        # Keras 3 / RetinaFace mismatch — give a clear fix hint
        if "KerasTensor" in str(e) or "legacy" in msg.lower():
            _log(
                log,
                f"  Detector warm failed ({det}): Keras 3 incompatibility. "
                "Restart the app so TF_USE_LEGACY_KERAS=1 is set before TensorFlow loads. "
                f"Detail: {msg}",
            )
        else:
            _log(log, f"  Detector warm note ({det}): {msg}")
        # Race weights alone are still usable with another detector
        if det != "opencv":
            _log(log, "  Tip: try detector “OpenCV Haar” or “SSD” if RetinaFace keeps failing.")
        return False


def warm_deepface_models(
    *,
    log: Optional[Callable[[str], None]] = None,
    model_ids: Optional[List[str]] = None,
    detector_backend: str = "retinaface",
) -> bool:
    """
    Download / load selected models into local cache (default: Race).

    First run may take a few minutes; later runs are fast.
    """
    global _warm_attempted
    ok, detail = deepface_runtime_ok()
    if not ok:
        # Attempt one ABI repair if that is the problem
        if _is_abi_error(detail) or "ABI" in detail:
            _log(log, f"Warm-up blocked ({detail}) — repairing stack first")
            try:
                with _ProcessFileLock(_LOCK_PATH, timeout=900.0):
                    _repair_numpy_stack(log=log)
            except Exception as e:
                _log(log, f"Repair failed: {e}")
                return False
            ok, detail = deepface_runtime_ok()
            if not ok:
                _log(log, f"Still not ready after repair: {detail}")
                return False
        else:
            return False

    # Allow re-warm when explicit model list provided
    if _warm_attempted and not model_ids:
        return True
    if not model_ids:
        _warm_attempted = True
    try:
        results = download_selected_weights(
            model_ids or ["Race"],
            detector_backend=detector_backend,
            log=log,
        )
        ok = bool(results.get("Race") or any(results.values()))
        if ok:
            _log(log, "DeepFace weights ready under ~/.deepface/weights/")
        return ok
    except Exception as e:
        msg = str(e)
        _log(log, f"DeepFace warm-up failed: {e}")
        if _is_abi_error(msg):
            try:
                with _ProcessFileLock(_LOCK_PATH, timeout=900.0):
                    if _repair_numpy_stack(log=log):
                        _warm_attempted = False
                        return warm_deepface_models(
                            log=log,
                            model_ids=model_ids or ["Race"],
                            detector_backend=detector_backend,
                        )
            except Exception as e2:
                _log(log, f"Repair after warm-up failure failed: {e2}")
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

# FairFace re-exports (primary backend auto-setup)
from scraper.mugshot_ethnicity.setup_fairface import (
    ensure_fairface,
    ensure_fairface_background,
    fairface_available,
    fairface_runtime_ok,
)
