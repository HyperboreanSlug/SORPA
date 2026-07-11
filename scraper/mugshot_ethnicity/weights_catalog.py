"""Selectable DeepFace weight / detector options with accurate guidance.

Pipeline (mugshot race tools)
  1. Face detector  — finds/crops the face in the photo (pick ONE)
  2. Race weights   — classifies the crop into ethnicity labels (required)

One vs multiple downloads
  • Default: download Race only + one detector (RetinaFace). That is enough.
  • Detectors: mutually exclusive — only the selected one runs; do not download all.
  • Attribute models (Age/Gender/Emotion): separate nets; do NOT improve race.
  • Recognition models (VGG-Face, ArcFace, …): identity match only; not race.
    If you ever need identity, download ONE recognition model, not several.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Filenames DeepFace stores under ~/.deepface/weights/ (see deepface sources).
# Empty list = built-in / no cache file required.
WEIGHT_CACHE_FILES: Dict[str, List[str]] = {
    "Race": ["race_model_single_batch.h5"],
    "Age": ["age_model_weights.h5"],
    "Gender": ["gender_model_weights.h5"],
    "Emotion": ["facial_expression_model_weights.h5"],
    "VGG-Face": ["vgg_face_weights.h5"],
    "Facenet": ["facenet_weights.h5"],
    "Facenet512": ["facenet512_weights.h5"],
    "ArcFace": ["arcface_weights.h5"],
    "OpenFace": ["openface_weights.h5"],
    "SFace": ["face_recognition_sface_2021dec.onnx"],
}

DETECTOR_CACHE_FILES: Dict[str, List[str]] = {
    "retinaface": ["retinaface.h5"],
    "opencv": [],  # Haar cascades ship with OpenCV
    "ssd": [
        "deploy.prototxt",
        "res10_300x300_ssd_iter_140000.caffemodel",
    ],
    "mtcnn": [],  # weights live inside the mtcnn package, not ~/.deepface
    "yunet": ["face_detection_yunet_2023mar.onnx"],
}

# Incomplete downloads are often tiny stubs
_MIN_BYTES = 8_000

# Approximate VRAM when the model is loaded on GPU (TensorFlow/CUDA).
# CPU-only runs use system RAM instead (often 1.5–3× the weight file size).
# ``vram_short`` is shown in dropdowns / compact UI; ``vram`` is full detail.
DETECTOR_OPTIONS: List[Dict[str, Any]] = [
    {
        "id": "retinaface",
        "label": "RetinaFace (recommended)",
        "size": "~100 MB disk",
        "vram_short": "~0.5–1.0 GB VRAM",
        "vram": "~0.5–1.0 GB GPU (or ~1–2 GB RAM on CPU)",
        "speed": "Medium",
        "summary": "Best face boxes for mugshots and low-quality registry photos.",
        "detail": (
            "Neural face detector. Locates the face crop before Race runs. "
            "Misses fewer faces on angled/compressed mugshots than Haar. "
            "Slightly slower than OpenCV; worth it for batch accuracy. "
            "Only one detector is active at a time — pick this OR another, not both."
        ),
    },
    {
        "id": "opencv",
        "label": "OpenCV Haar (fast / no download)",
        "size": "Built-in (no extra file)",
        "vram_short": "~0 GB VRAM",
        "vram": "~0 GB dedicated (CPU; negligible GPU)",
        "speed": "Fastest",
        "summary": "Classic cascade detector; no weight download.",
        "detail": (
            "CPU Haar cascade shipped with OpenCV — no detector file to download. "
            "Lowest memory cost. Can miss rotated faces, tight crops, or heavy "
            "compression. Use if GPU/disk is tight or you only need a quick smoke test."
        ),
    },
    {
        "id": "ssd",
        "label": "SSD (OpenCV DNN)",
        "size": "~10 MB disk",
        "vram_short": "~0.2–0.4 GB VRAM",
        "vram": "~0.2–0.4 GB GPU (or ~0.5 GB RAM on CPU)",
        "speed": "Fast",
        "summary": "Small neural detector; better than Haar, lighter than RetinaFace.",
        "detail": (
            "OpenCV DNN SSD face detector. Middle ground: better boxes than Haar, "
            "less VRAM and disk than RetinaFace. Fine for mostly frontal photos."
        ),
    },
    {
        "id": "mtcnn",
        "label": "MTCNN",
        "size": "~2 MB disk",
        "vram_short": "~0.3–0.6 GB VRAM",
        "vram": "~0.3–0.6 GB GPU (or ~1 GB RAM on CPU)",
        "speed": "Medium–slow",
        "summary": "Multi-stage CNN; handles pose variation better than Haar.",
        "detail": (
            "Three cascaded networks (propose → refine → landmarks). More robust "
            "to pose than Haar; slower on CPU. Moderate VRAM if GPU-accelerated."
        ),
    },
    {
        "id": "yunet",
        "label": "YuNet",
        "size": "~1 MB disk",
        "vram_short": "~0.1–0.3 GB VRAM",
        "vram": "~0.1–0.3 GB GPU (or ~0.3 GB RAM on CPU)",
        "speed": "Fast",
        "summary": "Tiny modern detector (OpenCV zoo).",
        "detail": (
            "Very small OpenCV zoo model. Low disk and VRAM. Prefer when resources "
            "are limited and photos are mostly frontal."
        ),
    },
]


def weights_dir() -> Path:
    """DeepFace local weight cache directory."""
    home = os.environ.get("DEEPFACE_HOME") or str(Path.home() / ".deepface")
    return Path(home) / "weights"


def _files_present(names: Sequence[str], *, min_bytes: int = _MIN_BYTES) -> Tuple[bool, int]:
    """Return (all present & large enough, total bytes)."""
    if not names:
        return True, 0
    root = weights_dir()
    total = 0
    for name in names:
        p = root / name
        try:
            if not p.is_file():
                return False, total
            sz = p.stat().st_size
            if sz < min_bytes:
                return False, total
            total += sz
        except OSError:
            return False, total
    return True, total


def _fmt_mb(nbytes: int) -> str:
    if nbytes <= 0:
        return ""
    mb = nbytes / (1024 * 1024)
    if mb >= 100:
        return f"{mb:.0f} MB"
    if mb >= 10:
        return f"{mb:.1f} MB"
    if mb >= 1:
        return f"{mb:.1f} MB"
    kb = nbytes / 1024
    return f"{kb:.0f} KB"


def weight_local_status(model_id: str) -> Dict[str, Any]:
    """
    Local download status for a weight model id (Race, ArcFace, …).

    Keys: downloaded (bool), builtin (bool), bytes (int), files (list),
    label (short UI string), detail (longer).
    """
    names = list(WEIGHT_CACHE_FILES.get(model_id) or [])
    if not names:
        return {
            "downloaded": False,
            "builtin": False,
            "bytes": 0,
            "files": [],
            "label": "Unknown model",
            "detail": f"No cache mapping for {model_id}",
        }
    ok, total = _files_present(names)
    size_s = _fmt_mb(total)
    if ok:
        return {
            "downloaded": True,
            "builtin": False,
            "bytes": total,
            "files": names,
            "label": f"Downloaded ({size_s})" if size_s else "Downloaded",
            "detail": f"Present in {weights_dir()}: {', '.join(names)}",
        }
    return {
        "downloaded": False,
        "builtin": False,
        "bytes": total,
        "files": names,
        "label": "Not downloaded",
        "detail": f"Missing under {weights_dir()}: {', '.join(names)}",
    }


def detector_local_status(det_id: str) -> Dict[str, Any]:
    """Local download status for a face detector id."""
    det = (det_id or "").strip().lower()
    if det not in DETECTOR_CACHE_FILES:
        return {
            "downloaded": False,
            "builtin": False,
            "bytes": 0,
            "files": [],
            "label": "Unknown detector",
            "detail": f"No cache mapping for {det_id}",
        }
    names = list(DETECTOR_CACHE_FILES[det])
    if not names:
        # Built-in (opencv) or package-bundled (mtcnn)
        if det == "opencv":
            return {
                "downloaded": True,
                "builtin": True,
                "bytes": 0,
                "files": [],
                "label": "Built-in",
                "detail": "No DeepFace weight file; uses OpenCV Haar cascades",
            }
        return {
            "downloaded": True,
            "builtin": True,
            "bytes": 0,
            "files": [],
            "label": "Package-bundled",
            "detail": "Weights ship with the Python package (not in ~/.deepface/weights)",
        }
    ok, total = _files_present(names)
    size_s = _fmt_mb(total)
    if ok:
        return {
            "downloaded": True,
            "builtin": False,
            "bytes": total,
            "files": names,
            "label": f"Downloaded ({size_s})" if size_s else "Downloaded",
            "detail": f"Present in {weights_dir()}: {', '.join(names)}",
        }
    return {
        "downloaded": False,
        "builtin": False,
        "bytes": total,
        "files": names,
        "label": "Not downloaded",
        "detail": f"Missing under {weights_dir()}: {', '.join(names)}",
    }


def all_weight_local_status() -> Dict[str, Dict[str, Any]]:
    return {mid: weight_local_status(mid) for mid in WEIGHT_CACHE_FILES}


def all_detector_local_status() -> Dict[str, Dict[str, Any]]:
    return {did: detector_local_status(did) for did in DETECTOR_CACHE_FILES}


def detector_dropdown_label(d: Dict[str, Any], *, include_status: bool = True) -> str:
    """Compact combo-box line: name + VRAM + download status."""
    short = d.get("vram_short") or d.get("vram") or ""
    base = d.get("label") or d.get("id") or ""
    parts = [base]
    if short:
        parts.append(short)
    if include_status:
        try:
            st = detector_local_status(str(d.get("id") or ""))
            if st.get("downloaded"):
                if st.get("builtin"):
                    parts.append(str(st.get("label") or "Built-in"))
                else:
                    parts.append("✓ " + str(st.get("label") or "Downloaded"))
            else:
                parts.append("not downloaded")
        except Exception:
            pass
    return "  ·  ".join(parts)


# Attribute + recognition models DeepFace.build_model can fetch.
WEIGHT_MODELS: List[Dict[str, Any]] = [
    {
        "id": "Race",
        "label": "Race / ethnicity (required)",
        "size": "~500 MB disk",
        "vram_short": "~1.0–1.5 GB VRAM",
        "vram": "~1.0–1.5 GB GPU when loaded (or ~2–3 GB RAM on CPU)",
        "category": "attribute",
        "required": True,
        "summary": "Only model required for mugshot race / ethnicity scoring.",
        "detail": (
            "Softmax over White, Black, Asian, Indian, Middle Eastern, Latino Hispanic. "
            "This is the sole attribute net mugshot-verify / mugshot-scan load for race. "
            "Downloading Age, Gender, Emotion, or any recognition model does not change "
            "these probabilities — they are different networks with different heads."
        ),
    },
    {
        "id": "Age",
        "label": "Age (optional — not for race)",
        "size": "~500 MB disk",
        "vram_short": "~1.0–1.5 GB VRAM",
        "vram": "~1.0–1.5 GB GPU if loaded",
        "category": "attribute",
        "required": False,
        "summary": "Separate network that estimates apparent age only.",
        "detail": (
            "Independent VGG-Face-based age head. Shares no training objective with "
            "Race. Skip for misclass tools; only download if you will analyze age."
        ),
    },
    {
        "id": "Gender",
        "label": "Gender (optional — not for race)",
        "size": "~500 MB disk",
        "vram_short": "~1.0–1.5 GB VRAM",
        "vram": "~1.0–1.5 GB GPU if loaded",
        "category": "attribute",
        "required": False,
        "summary": "Separate network for apparent gender only.",
        "detail": (
            "Independent gender classifier. Not used by the race mismatch pipeline. "
            "Same disk/VRAM cost class as Race if loaded — skip unless needed."
        ),
    },
    {
        "id": "Emotion",
        "label": "Emotion (optional — not for race)",
        "size": "~50 MB disk",
        "vram_short": "~0.3–0.5 GB VRAM",
        "vram": "~0.3–0.5 GB GPU if loaded",
        "category": "attribute",
        "required": False,
        "summary": "Expression classes (happy, sad, angry, …).",
        "detail": (
            "Smaller attribute net for facial expression. Unused by race tools. "
            "Safe to leave unchecked."
        ),
    },
    {
        "id": "VGG-Face",
        "label": "VGG-Face identity (optional)",
        "size": "~500 MB disk",
        "vram_short": "~1.0–1.5 GB VRAM",
        "vram": "~1.0–1.5 GB GPU if loaded",
        "category": "recognition",
        "required": False,
        "summary": "Identity embeddings (“same person?”) — not race labels.",
        "detail": (
            "Produces a face embedding for identity comparison (verify/find). "
            "Cannot output ethnicity. Large classic model. Skip for race tools; "
            "if you need identity later, prefer ArcFace or FaceNet512 instead of "
            "downloading several recognition models."
        ),
    },
    {
        "id": "Facenet",
        "label": "FaceNet 128d identity (optional)",
        "size": "~90 MB disk",
        "vram_short": "~0.5–0.8 GB VRAM",
        "vram": "~0.5–0.8 GB GPU if loaded",
        "category": "recognition",
        "required": False,
        "summary": "Google FaceNet 128-D identity embeddings.",
        "detail": (
            "Smaller identity embedding (128 dims). Not used for race. "
            "If you pick FaceNet, prefer Facenet512 over this — do not need both."
        ),
    },
    {
        "id": "Facenet512",
        "label": "FaceNet 512d identity (optional)",
        "size": "~90 MB disk",
        "vram_short": "~0.6–1.0 GB VRAM",
        "vram": "~0.6–1.0 GB GPU if loaded",
        "category": "recognition",
        "required": False,
        "summary": "FaceNet 512-D (richer identity vector than 128d).",
        "detail": (
            "Higher-dimension FaceNet embedding. Alternative to VGG-Face/ArcFace for "
            "same-person match. Download at most one recognition model."
        ),
    },
    {
        "id": "ArcFace",
        "label": "ArcFace identity (optional)",
        "size": "~130 MB disk",
        "vram_short": "~0.6–1.0 GB VRAM",
        "vram": "~0.6–1.0 GB GPU if loaded",
        "category": "recognition",
        "required": False,
        "summary": "Strong modern identity model; alternative to VGG-Face/FaceNet.",
        "detail": (
            "Often best same-person accuracy among DeepFace options. Mutually "
            "alternative to VGG-Face/FaceNet — pick one recognition stack, not all. "
            "Still unused by race scoring."
        ),
    },
    {
        "id": "OpenFace",
        "label": "OpenFace identity (optional)",
        "size": "~15 MB disk",
        "vram_short": "~0.2–0.4 GB VRAM",
        "vram": "~0.2–0.4 GB GPU if loaded",
        "category": "recognition",
        "required": False,
        "summary": "Lightweight identity embeddings.",
        "detail": (
            "Smallest recognition option; lower accuracy than ArcFace/VGG-Face. "
            "Not for race labels."
        ),
    },
    {
        "id": "SFace",
        "label": "SFace identity (optional)",
        "size": "~40 MB disk",
        "vram_short": "~0.3–0.5 GB VRAM",
        "vram": "~0.3–0.5 GB GPU if loaded",
        "category": "recognition",
        "required": False,
        "summary": "OpenCV zoo identity model.",
        "detail": "Optional recognition weights; not used for race scoring.",
    },
]

DOWNLOAD_GUIDANCE = (
    "Download guidance — one vs many:\n"
    "• Race tools need ONE weight: “Race / ethnicity” (checked by default).\n"
    "• Plus ONE face detector (dropdown above). Detectors are alternatives, not a set to collect.\n"
    "• Do NOT multi-select Age/Gender/Emotion for better race scores — they are unrelated nets.\n"
    "• Do NOT multi-select recognition models (VGG-Face, ArcFace, FaceNet…). Those are for "
    "“same person?” only; if you ever need that, pick a single recognition model.\n"
    "• Extra downloads only cost disk now; VRAM rises when a model is actually loaded."
)


def detector_ids() -> List[str]:
    return [d["id"] for d in DETECTOR_OPTIONS]


def weight_model_ids() -> List[str]:
    return [m["id"] for m in WEIGHT_MODELS]


def get_detector(det_id: str) -> Optional[Dict[str, Any]]:
    for d in DETECTOR_OPTIONS:
        if d["id"] == det_id:
            return d
    return None


def get_weight_model(model_id: str) -> Optional[Dict[str, Any]]:
    for m in WEIGHT_MODELS:
        if m["id"] == model_id:
            return m
    return None


def default_selected_weights() -> List[str]:
    return ["Race"]


def explain_detector(det_id: str) -> str:
    d = get_detector(det_id)
    if not d:
        return ""
    st = detector_local_status(det_id)
    local = st.get("label") or "Unknown"
    return (
        f"{d['summary']}\n\n{d['detail']}\n\n"
        f"Disk: {d['size']}  ·  Speed: {d['speed']}\n"
        f"VRAM / memory: {d['vram']}\n"
        f"Local cache: {local}\n"
        f"Only one detector runs at a time — switching reuses that choice for scans."
    )


def explain_weight(model_id: str) -> str:
    m = get_weight_model(model_id)
    if not m:
        return DOWNLOAD_GUIDANCE
    if m.get("required"):
        req = "Required — download this for race tools"
    elif m.get("category") == "recognition":
        req = "Optional identity model — not used for race; pick at most one if needed"
    else:
        req = "Optional attribute — not used for race tools"
    st = weight_local_status(model_id)
    local = st.get("label") or "Unknown"
    return (
        f"{m['summary']}\n\n{m['detail']}\n\n"
        f"Category: {m['category']}  ·  {req}\n"
        f"Disk: {m['size']}\n"
        f"VRAM / memory when loaded: {m['vram']}\n"
        f"Local cache: {local}\n\n"
        f"{DOWNLOAD_GUIDANCE}"
    )
