"""Map registry race strings and face-backend labels into shared buckets."""
from __future__ import annotations

import re
from typing import Optional, Set

from scraper.searcher import _canonical_race_key


def normalize_face_label(label: str) -> str:
    """Normalize backend-specific labels → FACE_LABELS buckets."""
    t = re.sub(r"[^a-z]+", " ", (label or "").lower()).strip()
    aliases = {
        "white": "white",
        "caucasian": "white",
        "black": "black",
        "african": "black",
        "african american": "black",
        "asian": "asian",
        "east asian": "asian",
        "southeast asian": "asian",
        "indian": "indian",
        "south asian": "indian",
        "asian indian": "indian",
        "latino": "hispanic",
        "latina": "hispanic",
        "hispanic": "hispanic",
        "latino hispanic": "hispanic",
        "middle eastern": "middle_eastern",
        "middle_eastern": "middle_eastern",
        "arab": "middle_eastern",
        "other": "other",
        "unknown": "unknown",
    }
    if t in aliases:
        return aliases[t]
    if "indian" in t or "south asian" in t:
        return "indian"
    if "black" in t or "african" in t:
        return "black"
    if "hispanic" in t or "latino" in t:
        return "hispanic"
    if "middle" in t and "east" in t:
        return "middle_eastern"
    if "asian" in t:
        return "asian"
    if "white" in t or "caucasian" in t:
        return "white"
    return "unknown"


def registry_race_to_face_labels(recorded_race: str) -> Set[str]:
    """Which face buckets are consistent with a registry race string."""
    key = _canonical_race_key(recorded_race or "")
    if key in ("WHITE", "WHITE HISPANIC"):
        # White Hispanic may also match hispanic face bucket
        if key == "WHITE HISPANIC":
            return {"white", "hispanic"}
        return {"white"}
    if key == "BLACK":
        return {"black"}
    if key in ("ASIAN", "ASIAN / PACIFIC ISLANDER", "PACIFIC ISLANDER", "OTHER ASIAN"):
        return {"asian", "indian"}
    if key in ("HISPANIC",):
        return {"hispanic", "white"}  # many Latino faces score white or hispanic
    if "INDIAN" in key or "SOUTH ASIAN" in key:
        return {"indian", "asian"}
    if key in ("UNKNOWN", "OTHER", ""):
        return set()
    # Fallback: loose token match
    low = key.lower()
    out: Set[str] = set()
    for token, lab in (
        ("white", "white"),
        ("black", "black"),
        ("asian", "asian"),
        ("indian", "indian"),
        ("hispanic", "hispanic"),
        ("latino", "hispanic"),
    ):
        if token in low:
            out.add(lab)
    return out


def name_ethnicity_to_face_labels(name_ethnicity: str) -> Set[str]:
    """Map surname-based ethnicity family → expected face buckets."""
    eth = (name_ethnicity or "").strip().lower()
    if not eth or eth == "unknown":
        return set()
    if (
        eth == "indian"
        or eth.startswith("indian")
        or eth in ("mena", "arabic")
        or eth.startswith("arabic")
        or eth.startswith("mena")
    ):
        # Arabic/MENA branch vs Indic (labels still Indian/MENA (…))
        if (
            "(arabic)" in eth
            or eth in ("arabic", "mena")
            or eth.startswith("arabic")
            or eth.startswith("mena")
        ):
            return {"middle_eastern", "white", "indian"}
        return {"indian", "asian", "middle_eastern"}
    if eth.startswith("asian"):
        return {"asian"}
    if eth == "hispanic":
        return {"hispanic", "white"}
    if eth in ("african_american", "african american", "african"):
        return {"black"}
    if eth in ("european", "jewish", "portuguese"):
        return {"white"}
    if eth in ("native_american", "native american"):
        return {"other", "white"}
    return set()


def is_gross_face_vs_white(face_label: str) -> bool:
    """Face labels that are grossly inconsistent with race=White."""
    return normalize_face_label(face_label) in ("black", "indian", "asian")


def face_contradicts_recorded(face_label: str, recorded_race: str) -> bool:
    """True if top face label is outside the registry race's expected buckets."""
    expected = registry_race_to_face_labels(recorded_race)
    if not expected:
        return False
    lab = normalize_face_label(face_label)
    if lab in ("unknown", "other"):
        return False
    return lab not in expected
