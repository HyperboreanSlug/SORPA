"""Pluggable *local* mugshot ethnicity backends (lazy imports).

Production default is **DeepFace** (https://github.com/serengil/deepface):
open-source, runs entirely on-machine, downloads model weights once into the
user cache. It exposes a dedicated race attribute model (White / Black / Asian /
Indian / Middle Eastern / Latino Hispanic) which is what we need for
high-confidence gross misclassification checks.

Optional fallbacks:
  - CLIP zero-shot (local transformers + torch) if DeepFace is not installed
  - MockBackend for unit tests only (never selected by ``auto``)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from scraper.mugshot_ethnicity.labels import normalize_face_label
from scraper.mugshot_ethnicity.models import FACE_LABELS, FaceEthnicityScore


class EthnicityBackend(ABC):
    """Score a face image → ethnicity distribution (local models only)."""

    name: str = "base"
    # True for real vision stacks; False for test doubles
    is_production: bool = True

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def analyze(self, photo_path: str) -> FaceEthnicityScore:
        ...


class MockBackend(EthnicityBackend):
    """Deterministic backend for tests (path stem encodes label__conf)."""

    name = "mock"
    is_production = False

    def __init__(self, fixed: Optional[Dict[str, float]] = None):
        self.fixed = fixed

    def is_available(self) -> bool:
        return True

    def analyze(self, photo_path: str) -> FaceEthnicityScore:
        if self.fixed:
            scores = {k: float(v) for k, v in self.fixed.items()}
            top = max(scores, key=scores.get)
            return FaceEthnicityScore(
                photo_path=photo_path,
                top_label=top,
                top_confidence=float(scores[top]),
                scores=scores,
                backend=self.name,
                face_detected=True,
            )
        stem = Path(photo_path).stem.lower()
        label = "unknown"
        conf = 0.9
        if "__" in stem:
            lab, conf_s = stem.rsplit("__", 1)
            label = normalize_face_label(lab)
            try:
                conf = float(conf_s.replace("_", "."))
            except ValueError:
                conf = 0.9
        else:
            for cand in FACE_LABELS:
                if cand in stem.replace("-", "_"):
                    label = cand
                    break
        scores = {
            lab: (conf if lab == label else (1.0 - conf) / max(1, len(FACE_LABELS) - 1))
            for lab in FACE_LABELS
            if lab != "unknown"
        }
        if label == "unknown":
            scores = {"unknown": 1.0}
        return FaceEthnicityScore(
            photo_path=photo_path,
            top_label=label,
            top_confidence=conf if label != "unknown" else 0.0,
            scores=scores,
            backend=self.name,
            face_detected=label != "unknown",
        )


class DeepFaceBackend(EthnicityBackend):
    """
    Local DeepFace race attribute model.

    Install::

        pip install -r requirements-vision.txt
        # or: pip install deepface tensorflow

    First ``analyze()`` downloads weights into ``~/.deepface/weights/`` (offline
    afterward). Detector default ``retinaface`` is accurate for mugshots;
    falls back to ``opencv`` if retinaface weights fail.
    """

    name = "deepface"
    is_production = True

    def __init__(
        self,
        detector_backend: Optional[str] = None,
        *,
        enforce_detection: bool = False,
    ):
        # enforce_detection=False: still score when face box is weak (common on
        # low-res registry thumbs); confidence will reflect model uncertainty.
        det = (detector_backend or "").strip()
        if not det:
            try:
                from scraper.app_settings import load_settings

                det = str(load_settings().get("deepface_detector") or "retinaface")
            except Exception:
                det = "retinaface"
        self.detector_backend = det or "retinaface"
        self.enforce_detection = bool(enforce_detection)
        self._detectors_tried: List[str] = []

    def is_available(self) -> bool:
        try:
            import deepface  # noqa: F401
            return True
        except Exception:
            return False

    def analyze(self, photo_path: str) -> FaceEthnicityScore:
        try:
            # Before any TF/keras import — required for RetinaFace on TF 2.16+
            from scraper.mugshot_ethnicity.setup import configure_tf_keras_env

            configure_tf_keras_env()
            from deepface import DeepFace
        except Exception as e:
            return FaceEthnicityScore(
                photo_path=photo_path,
                top_label="unknown",
                top_confidence=0.0,
                backend=self.name,
                face_detected=False,
                error=f"deepface import failed: {e}",
            )

        detectors = [self.detector_backend]
        # Prefer SSD/YuNet over broken OpenCV Haar (headless wheels lack cascades)
        for alt in ("retinaface", "ssd", "yunet", "opencv", "mtcnn"):
            if alt not in detectors:
                detectors.append(alt)

        last_err: Optional[str] = None
        for det in detectors:
            try:
                results = DeepFace.analyze(
                    img_path=str(photo_path),
                    actions=["race"],
                    detector_backend=det,
                    enforce_detection=self.enforce_detection,
                    silent=True,
                )
                if isinstance(results, list):
                    result = results[0] if results else {}
                else:
                    result = results or {}
                race_scores = result.get("race") or {}
                if not isinstance(race_scores, dict) or not race_scores:
                    last_err = "empty race scores"
                    continue

                scores: Dict[str, float] = {}
                for k, v in race_scores.items():
                    lab = normalize_face_label(str(k))
                    try:
                        val = float(v)
                    except (TypeError, ValueError):
                        continue
                    # DeepFace returns 0–100 percentages
                    if val > 1.5:
                        val = val / 100.0
                    scores[lab] = max(scores.get(lab, 0.0), val)
                if not scores:
                    last_err = "unparseable race scores"
                    continue
                total = sum(scores.values()) or 1.0
                scores = {k: v / total for k, v in scores.items()}
                top = max(scores, key=scores.get)
                self._detectors_tried.append(det)
                return FaceEthnicityScore(
                    photo_path=photo_path,
                    top_label=top,
                    top_confidence=float(scores[top]),
                    scores=scores,
                    backend=f"{self.name}:{det}",
                    face_detected=True,
                    raw={
                        "dominant_race": result.get("dominant_race"),
                        "race": race_scores,
                        "detector": det,
                        "region": result.get("region"),
                    },
                )
            except Exception as e:
                last_err = str(e)
                continue

        return FaceEthnicityScore(
            photo_path=photo_path,
            top_label="unknown",
            top_confidence=0.0,
            backend=self.name,
            face_detected=False,
            error=last_err or "deepface analyze failed",
        )


class ClipBackend(EthnicityBackend):
    """Local CLIP zero-shot prompts (torch + transformers). Heavier fallback."""

    name = "clip"
    is_production = True
    PROMPTS = {
        "white": "a frontal mugshot of a white caucasian person",
        "black": "a frontal mugshot of a black african american person",
        "asian": "a frontal mugshot of an east asian person",
        "indian": "a frontal mugshot of a south asian indian person",
        "hispanic": "a frontal mugshot of a hispanic or latino person",
        "middle_eastern": "a frontal mugshot of a middle eastern person",
    }

    def __init__(self):
        self._model = None
        self._processor = None
        self._device = "cpu"

    def is_available(self) -> bool:
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
            from PIL import Image  # noqa: F401
            return True
        except Exception:
            return False

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import CLIPModel, CLIPProcessor

        model_id = "openai/clip-vit-base-patch32"
        self._processor = CLIPProcessor.from_pretrained(model_id)
        self._model = CLIPModel.from_pretrained(model_id)
        self._model.eval()
        if torch.cuda.is_available():
            self._device = "cuda"
            self._model.to(self._device)

    def analyze(self, photo_path: str) -> FaceEthnicityScore:
        try:
            import torch
            from PIL import Image

            self._load()
            assert self._model is not None and self._processor is not None
            image = Image.open(photo_path).convert("RGB")
            labels = list(self.PROMPTS.keys())
            texts = [self.PROMPTS[k] for k in labels]
            inputs = self._processor(
                text=texts, images=image, return_tensors="pt", padding=True
            )
            if self._device == "cuda":
                inputs = {k: v.to(self._device) for k, v in inputs.items()}
            with torch.no_grad():
                outputs = self._model(**inputs)
                logits = outputs.logits_per_image[0]
                probs = logits.softmax(dim=0).detach().cpu().tolist()
            scores = {lab: float(p) for lab, p in zip(labels, probs)}
            top = max(scores, key=scores.get)
            return FaceEthnicityScore(
                photo_path=photo_path,
                top_label=top,
                top_confidence=float(scores[top]),
                scores=scores,
                backend=self.name,
                face_detected=True,
            )
        except Exception as e:
            return FaceEthnicityScore(
                photo_path=photo_path,
                top_label="unknown",
                top_confidence=0.0,
                backend=self.name,
                face_detected=False,
                error=str(e),
            )


# Production backends — mock is never in auto chain.
# Prefer FairFace (standalone face-race package) over DeepFace race.
_BACKEND_CLASSES: List[Type[EthnicityBackend]] = [
    # FairFace registered dynamically in create_backend
    DeepFaceBackend,
    ClipBackend,
]


def list_backend_status() -> Dict[str, bool]:
    """Fast presence check via find_spec — never import torch/keras on the UI thread."""
    import importlib.util

    out: Dict[str, bool] = {"mock": True, "fairface": False}
    try:
        from scraper.mugshot_ethnicity.backends_fairface import FairFaceBackend

        out["fairface"] = FairFaceBackend().is_available()
    except Exception:
        out["fairface"] = False
    try:
        out["deepface"] = importlib.util.find_spec("deepface") is not None
    except Exception:
        out["deepface"] = False
    try:
        out["clip"] = (
            importlib.util.find_spec("torch") is not None
            and importlib.util.find_spec("transformers") is not None
        )
    except Exception:
        out["clip"] = False
    return out


def create_backend(
    name: str = "auto",
    *,
    auto_install: bool = True,
    log=None,
) -> EthnicityBackend:
    """
    Create a backend by name.

    ``auto`` → FairFace (face-race) if available, else DeepFace, else CLIP.
    ``fairface`` → standalone FairFace package only.
    ``deepface`` → DeepFace race head (legacy).
    ``mock`` → tests only.
    """
    from scraper.mugshot_ethnicity.setup import ensure_deepface

    key = (name or "auto").strip().lower()
    if key == "mock":
        return MockBackend()

    if key in ("auto", "fairface"):
        try:
            from scraper.mugshot_ethnicity.backends_fairface import FairFaceBackend

            fb = FairFaceBackend(log=log)
            if fb.is_available():
                try:
                    # Touch scorer so weights download early when auto_install
                    if auto_install:
                        fb._get_scorer()
                    return fb
                except Exception as e:
                    if key == "fairface":
                        raise RuntimeError(
                            "FairFace (face-race) setup failed.\n"
                            "  cd face-race && pip install -e .\n"
                            f"Detail: {e}"
                        ) from e
                    if log:
                        try:
                            log(f"FairFace unavailable ({e}); trying DeepFace…")
                        except Exception:
                            pass
            elif key == "fairface":
                raise RuntimeError(
                    "FairFace backend not available.\n"
                    "Install the standalone package:\n"
                    "  cd face-race && pip install -e .\n"
                    "Weights download on first score into ~/.face_race/weights/"
                )
        except RuntimeError:
            raise
        except Exception as e:
            if key == "fairface":
                raise RuntimeError(f"FairFace backend error: {e}") from e
        if key == "fairface":
            raise RuntimeError("FairFace backend not available")

    if key in ("auto", "deepface"):
        if auto_install:
            ensure_deepface(auto_install=True, warm=True, log=log)
        b = DeepFaceBackend()
        if b.is_available():
            return b
        if key == "deepface":
            raise RuntimeError(
                "DeepFace could not be set up automatically.\n"
                f"Interpreter: {__import__('sys').executable}\n"
                "Try:\n"
                "  python -m pip install -r requirements-vision.txt\n"
                "Or set SOR_SKIP_DEEPFACE_INSTALL=1 only to disable auto-install."
            )
        try:
            c = ClipBackend()
            if c.is_available():
                return c
        except Exception:
            pass
        status = list_backend_status()
        raise RuntimeError(
            "No local vision backend available.\n"
            f"Status: {status}\n"
            "  cd face-race && pip install -e .\n"
            "  python -m pip install -r requirements-vision.txt"
        )

    if key == "clip":
        b = ClipBackend()
        if not b.is_available():
            raise RuntimeError(
                "CLIP backend not installed:\n"
                "  pip install torch transformers pillow"
            )
        return b
    raise ValueError(f"Unknown mugshot backend: {name!r}")
