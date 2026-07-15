"""Misclassify table labels, verification status, actual-race helpers."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from scraper.ethnicity_review import ethnicity_review_verdict

MISCLASS_COLS = [
    "name",
    "recorded_race",
    "likely_ethnicity",
    "confidence",
    "deepface",
    "crime",
    "confirmation",
]
MISCLASS_LABELS = {
    "name": "Name",
    "recorded_race": "Recorded race",
    "likely_ethnicity": "Likely ethnicity",
    "confidence": "Confidence",
    "deepface": "DeepFace",
    "crime": "Crime",
    "confirmation": "Confirmation",
}


def format_deepface_cell(scan: Optional[Dict[str, Any]]) -> str:
    """Compact DeepFace score for the Misclassify tree (label + conf)."""
    if not scan:
        return "—"
    lab = (
        str(scan.get("predicted_label") or scan.get("top_label") or "").strip()
    )
    try:
        conf = float(scan.get("top_confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    if not lab and conf <= 0:
        err = str(scan.get("error") or "").strip()
        return (err[:18] + "…") if len(err) > 18 else (err or "—")
    lab_s = lab.replace("_", " ")[:12] if lab else "?"
    return f"{lab_s} {conf:.2f}"

# Sidebar actual-race picker (coarse + Indian/MENA for SOR work)
MISCLASS_ACTUAL_RACES = [
    "White",
    "Black",
    "Hispanic",
    "Indian/MENA",
    "Asian",
    "Other",
    "Unknown",
]


def verification_label(record: Optional[Dict[str, Any]]) -> str:
    verdict = ethnicity_review_verdict(record)
    if verdict == "correct":
        return "Confirmed correct"
    if verdict == "incorrect":
        return "Confirmed incorrect"
    return "Unverified"


def actual_from_stated_race(recorded_race: Optional[str]) -> Optional[str]:
    """Map stated race → actual-race picker value when marked classified correctly."""
    from scraper.searcher import format_race_label

    raw = str(recorded_race or "").strip()
    if not raw:
        return None
    label = format_race_label(raw)
    low = label.lower()
    if "hispanic" in low or "latino" in low:
        return "Hispanic"
    if "black" in low or "african" in low:
        return "Black"
    if "white" in low or "caucasian" in low:
        return "White"
    if (
        "indian" in low
        or "mena" in low
        or "arab" in low
        or "middle east" in low
        or "south asian" in low
    ):
        return "Indian/MENA"
    if "asian" in low:
        return "Asian"
    if label and label not in ("Other/Unknown", "—", ""):
        return label
    return None


def picker_actual_race(label: Optional[str], options: List[str]) -> str:
    """Value to show in the actual-race combo (never inject free-text junk)."""
    raw = " ".join(str(label or "").split()).strip() or "Unknown"
    opts = list(options or [])
    if raw in opts:
        return raw
    low = raw.lower()
    for opt in opts:
        if opt.lower() in low or low.startswith(opt.lower()):
            return opt
    if "indian" in low or "mena" in low or "arab" in low:
        for opt in opts:
            if "indian" in opt.lower() or "mena" in opt.lower():
                return opt
    for fallback in ("Unknown", "Other", "White"):
        if fallback in opts:
            return fallback
    return opts[0] if opts else "Unknown"
