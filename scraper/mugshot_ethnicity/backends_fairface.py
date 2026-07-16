"""FairFace race backend via standalone ``face_race`` package."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

from scraper.mugshot_ethnicity.labels import normalize_face_label
from scraper.mugshot_ethnicity.models import FaceEthnicityScore


def _ensure_face_race_on_path() -> None:
    """Allow sibling install at ~/face-race or <user>/face-race next to SORPA."""
    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "face-race",  # .../Zero/face-race
        Path.home() / "face-race",
    ]
    for root in candidates:
        if (root / "face_race").is_dir():
            s = str(root)
            if s not in sys.path:
                sys.path.insert(0, s)
            return


class FairFaceBackend:
    """Local FairFace ResNet-34 race-7 (standalone face-race package)."""

    name = "fairface"
    is_production = True

    def __init__(self, *, device: Optional[str] = None, log=None):
        self._device = device
        self._log = log
        self._scorer = None

    def is_available(self) -> bool:
        _ensure_face_race_on_path()
        try:
            from face_race import fairface_available

            return bool(fairface_available())
        except Exception:
            return False

    def _get_scorer(self):
        if self._scorer is not None:
            return self._scorer
        _ensure_face_race_on_path()
        from face_race import FairFaceScorer, ensure_ready

        ensure_ready(download_weights=True, log=self._log)
        self._scorer = FairFaceScorer(device=self._device, log=self._log)
        return self._scorer

    def analyze(self, photo_path: str) -> FaceEthnicityScore:
        try:
            scorer = self._get_scorer()
            r = scorer.score_path(photo_path)
        except Exception as e:
            return FaceEthnicityScore(
                photo_path=photo_path or "",
                top_label="unknown",
                top_confidence=0.0,
                backend=self.name,
                face_detected=False,
                error=f"fairface failed: {e}",
            )
        scores: Dict[str, float] = {}
        for k, v in (r.scores or {}).items():
            lab = normalize_face_label(str(k))
            if lab in ("", "unknown"):
                continue
            try:
                scores[lab] = max(scores.get(lab, 0.0), float(v))
            except (TypeError, ValueError):
                continue
        top = normalize_face_label(r.top_label) if r.top_label else "unknown"
        conf = float(r.top_confidence or 0.0)
        if scores and top == "unknown":
            top = max(scores, key=scores.get)
            conf = float(scores[top])
        return FaceEthnicityScore(
            photo_path=photo_path or "",
            top_label=top if r.ok else "unknown",
            top_confidence=conf if r.ok else 0.0,
            scores=scores,
            backend=str(r.backend or self.name),
            face_detected=bool(r.face_detected and r.ok),
            error=r.error,
            raw=dict(r.raw or {}),
        )
