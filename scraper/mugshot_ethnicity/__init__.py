"""Mugshot-based ethnicity / race scoring for high-confidence verification.

Uses **local open-source** face models — nothing is sent to a cloud API.

Primary backend: **FairFace** via the standalone ``face-race`` package
(shared with MAPA). Falls back to DeepFace, then CLIP.

Install::

    cd face-race && pip install -e .
    # optional legacy: pip install -r requirements-vision.txt

Two workflows:

1. **Verify** — combine face scores with surname-based ethnicity to confirm or
   reject a registry race label for a single person (or a name-misclass list).
2. **Scan** — walk archived mugshots independently and flag gross mismatches
   (e.g. face scores Black/Indian at high confidence while race is White).

``auto`` never falls back to mock. Unit tests pass ``backend='mock'`` only.
"""
from __future__ import annotations

from scraper.mugshot_ethnicity.models import (
    FaceEthnicityScore,
    GrossMisclassHit,
    VerifyResult,
)
from scraper.mugshot_ethnicity.scorer import (
    BackendUnavailableError,
    MugshotEthnicityScorer,
    get_available_backends,
)
from scraper.mugshot_ethnicity.setup import (
    deepface_available,
    deepface_runtime_ok,
    download_selected_weights,
    ensure_deepface,
    ensure_deepface_background,
    ensure_fairface,
    ensure_fairface_background,
    fairface_available,
    fairface_runtime_ok,
    warm_deepface_models,
)

from scraper.mugshot_ethnicity.verify import verify_record, verify_misclassifications
from scraper.mugshot_ethnicity.scanner import scan_gross_misclassifications

__all__ = [
    "FaceEthnicityScore",
    "GrossMisclassHit",
    "VerifyResult",
    "BackendUnavailableError",
    "MugshotEthnicityScorer",
    "get_available_backends",
    "deepface_available",
    "deepface_runtime_ok",
    "download_selected_weights",
    "ensure_deepface",
    "ensure_deepface_background",
    "ensure_fairface",
    "ensure_fairface_background",
    "fairface_available",
    "fairface_runtime_ok",
    "warm_deepface_models",
    "verify_record",
    "verify_misclassifications",
    "scan_gross_misclassifications",
]
