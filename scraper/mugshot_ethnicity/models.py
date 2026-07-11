"""Dataclasses for mugshot ethnicity scoring."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# Canonical face-predicted buckets used across backends
FACE_LABELS = (
    "white",
    "black",
    "asian",
    "indian",  # South Asian
    "hispanic",
    "middle_eastern",
    "other",
    "unknown",
)


@dataclass
class FaceEthnicityScore:
    """One mugshot analysis result."""

    photo_path: str
    top_label: str
    top_confidence: float
    scores: Dict[str, float] = field(default_factory=dict)
    backend: str = ""
    face_detected: bool = True
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return (
            self.face_detected
            and not self.error
            and bool(self.top_label)
            and self.top_label != "unknown"
        )

    def score_for(self, label: str) -> float:
        return float(self.scores.get((label or "").lower(), 0.0) or 0.0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "photo_path": self.photo_path,
            "top_label": self.top_label,
            "top_confidence": self.top_confidence,
            "scores": dict(self.scores),
            "backend": self.backend,
            "face_detected": self.face_detected,
            "error": self.error,
        }


@dataclass
class VerifyResult:
    """Name scoring + mugshot scoring combined for one offender."""

    record: Dict[str, Any]
    recorded_race: str
    name_ethnicity: str
    name_confidence: float
    face: Optional[FaceEthnicityScore]
    # combined
    verdict: str  # agree | disagree | weak | no_photo | no_face | error
    combined_confidence: float
    reasons: List[str] = field(default_factory=list)
    # True when face + name both contradict recorded race at high conf
    confirms_misclass: bool = False
    # True when face supports recorded race against name signal
    supports_recorded: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.record.get("id"),
            "name": (
                f"{self.record.get('first_name') or ''} "
                f"{self.record.get('last_name') or ''}"
            ).strip(),
            "recorded_race": self.recorded_race,
            "name_ethnicity": self.name_ethnicity,
            "name_confidence": self.name_confidence,
            "face": self.face.to_dict() if self.face else None,
            "verdict": self.verdict,
            "combined_confidence": self.combined_confidence,
            "confirms_misclass": self.confirms_misclass,
            "supports_recorded": self.supports_recorded,
            "reasons": list(self.reasons),
        }


@dataclass
class GrossMisclassHit:
    """Independent mugshot scan hit (no name signal required)."""

    record: Dict[str, Any]
    recorded_race: str
    face: FaceEthnicityScore
    predicted_label: str
    confidence: float
    severity: str  # high | medium
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.record.get("id"),
            "name": (
                f"{self.record.get('first_name') or ''} "
                f"{self.record.get('last_name') or ''}"
            ).strip(),
            "state": self.record.get("state"),
            "recorded_race": self.recorded_race,
            "predicted_label": self.predicted_label,
            "confidence": self.confidence,
            "severity": self.severity,
            "reason": self.reason,
            "photo_path": self.face.photo_path,
            "face": self.face.to_dict(),
        }
