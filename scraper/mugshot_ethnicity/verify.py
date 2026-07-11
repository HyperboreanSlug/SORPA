"""Combine name-based ethnicity with mugshot scores (verify workflow)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from scraper.ethnic_names import EthnicNameDatabase, get_ethnic_database
from scraper.mugshot_ethnicity.labels import (
    face_contradicts_recorded,
    name_ethnicity_to_face_labels,
    normalize_face_label,
    registry_race_to_face_labels,
)
from scraper.mugshot_ethnicity.models import FaceEthnicityScore, VerifyResult
from scraper.mugshot_ethnicity.scorer import BackendUnavailableError, MugshotEthnicityScorer
from scraper.searcher import (
    Misclassification,
    _canonical_race_key,
    _first_name_from_record,
    _is_compatible,
    _last_name_from_record,
    _middle_name_from_record,
    format_race_label,
)


def _name_classify(
    record: Dict[str, Any],
    ethnic_db: EthnicNameDatabase,
) -> tuple:
    last = _last_name_from_record(record)
    first = _first_name_from_record(record)
    middle = _middle_name_from_record(record)
    if not last:
        return "Unknown", 0.0, []
    return ethnic_db.classify_by_name(
        last,
        first_name=first or None,
        middle_name=middle or None,
    )


def verify_record(
    record: Dict[str, Any],
    *,
    scorer: Optional[MugshotEthnicityScorer] = None,
    ethnic_db: Optional[EthnicNameDatabase] = None,
    name_ethnicity: Optional[str] = None,
    name_confidence: Optional[float] = None,
    face_min_conf: float = 0.75,
    name_min_conf: float = 0.5,
    combined_min_conf: float = 0.8,
    backend: str = "auto",
) -> VerifyResult:
    """
    Verify one offender using surname ethnicity + mugshot score.

    High-confidence **disagree** with recorded race when both name and face
    point away from the registry race (``confirms_misclass=True``).
    """
    rec = dict(record or {})
    eth_db = ethnic_db or get_ethnic_database()
    recorded_race = (rec.get("race") or "").strip()
    recorded_eth = (rec.get("ethnicity") or "").strip() or None

    if name_ethnicity is None or name_confidence is None:
        ne, nc, _ = _name_classify(rec, eth_db)
        name_ethnicity = name_ethnicity if name_ethnicity is not None else ne
        name_confidence = float(
            name_confidence if name_confidence is not None else nc
        )
    else:
        name_confidence = float(name_confidence)
        name_ethnicity = str(name_ethnicity)

    photo = (rec.get("photo_path") or "").strip()
    reasons: List[str] = []

    if not photo or not Path(photo).is_file():
        return VerifyResult(
            record=rec,
            recorded_race=format_race_label(recorded_race) if recorded_race else "—",
            name_ethnicity=name_ethnicity or "Unknown",
            name_confidence=name_confidence,
            face=None,
            verdict="no_photo",
            combined_confidence=0.0,
            reasons=["no local mugshot"],
        )

    try:
        sc = scorer or MugshotEthnicityScorer(backend=backend)
    except BackendUnavailableError as e:
        return VerifyResult(
            record=rec,
            recorded_race=format_race_label(recorded_race) if recorded_race else "—",
            name_ethnicity=name_ethnicity or "Unknown",
            name_confidence=name_confidence,
            face=None,
            verdict="error",
            combined_confidence=0.0,
            reasons=[str(e)],
        )

    face = sc.score_path(photo)
    if face.error and not face.ok:
        return VerifyResult(
            record=rec,
            recorded_race=format_race_label(recorded_race) if recorded_race else "—",
            name_ethnicity=name_ethnicity or "Unknown",
            name_confidence=name_confidence,
            face=face,
            verdict="no_face" if not face.face_detected else "error",
            combined_confidence=0.0,
            reasons=[face.error or "face analysis failed"],
        )

    face_lab = normalize_face_label(face.top_label)
    face_conf = float(face.top_confidence or 0.0)
    name_ok = (
        name_confidence >= name_min_conf
        and (name_ethnicity or "Unknown") != "Unknown"
    )
    face_ok = face.ok and face_conf >= face_min_conf

    expected_from_name = name_ethnicity_to_face_labels(name_ethnicity or "")
    expected_from_race = registry_race_to_face_labels(recorded_race)
    name_vs_race_mismatch = not _is_compatible(
        name_ethnicity or "Unknown",
        recorded_race,
        recorded_ethnicity=recorded_eth,
    )
    face_vs_race = face_contradicts_recorded(face_lab, recorded_race)
    face_supports_name = bool(expected_from_name) and face_lab in expected_from_name
    face_supports_race = bool(expected_from_race) and face_lab in expected_from_race

    if name_ok:
        reasons.append(
            f"name→{name_ethnicity} ({name_confidence:.2f})"
            + (" mismatches race" if name_vs_race_mismatch else " ok vs race")
        )
    else:
        reasons.append(f"name signal weak/unknown ({name_confidence:.2f})")

    reasons.append(f"face→{face_lab} ({face_conf:.2f}, {face.backend})")

    confirms = False
    supports_rec = False
    verdict = "weak"
    combined = 0.0

    if face_ok and name_ok and name_vs_race_mismatch and face_supports_name and face_vs_race:
        # Both name and face contradict registry race
        confirms = True
        verdict = "disagree"
        combined = min(1.0, 0.45 * name_confidence + 0.55 * face_conf)
        reasons.append("name+face contradict recorded race (high conf)")
    elif face_ok and face_supports_race and not face_supports_name and name_vs_race_mismatch:
        supports_rec = True
        verdict = "agree"
        combined = face_conf
        reasons.append("face supports recorded race (name disagreed)")
    elif face_ok and face_supports_race:
        supports_rec = True
        verdict = "agree"
        combined = face_conf
        reasons.append("face consistent with recorded race")
    elif face_ok and face_vs_race and face_conf >= combined_min_conf:
        # Face alone strongly contradicts race (name optional)
        verdict = "disagree"
        combined = face_conf
        if name_ok and face_supports_name:
            confirms = True
            combined = min(1.0, 0.4 * name_confidence + 0.6 * face_conf)
            reasons.append("face contradicts race; name aligns with face")
        else:
            reasons.append("face alone contradicts recorded race")
    elif face_ok and face_supports_name and name_ok:
        verdict = "agree" if not name_vs_race_mismatch else "weak"
        combined = min(face_conf, name_confidence)
        reasons.append("face aligns with name ethnicity")
    else:
        verdict = "weak"
        combined = max(face_conf * 0.5, name_confidence * 0.3)
        reasons.append("insufficient agreement for high-confidence call")

    if combined < combined_min_conf and verdict == "disagree" and not confirms:
        # Downgrade low-confidence face-only disagreements
        if not (face_ok and face_conf >= combined_min_conf):
            verdict = "weak"
            reasons.append("below combined confidence threshold")

    return VerifyResult(
        record=rec,
        recorded_race=format_race_label(recorded_race) if recorded_race else "—",
        name_ethnicity=name_ethnicity or "Unknown",
        name_confidence=name_confidence,
        face=face,
        verdict=verdict,
        combined_confidence=float(combined),
        reasons=reasons,
        confirms_misclass=confirms and combined >= combined_min_conf,
        supports_recorded=supports_rec and face_conf >= face_min_conf,
    )


def verify_misclassifications(
    items: Sequence[Union[Misclassification, Dict[str, Any]]],
    *,
    scorer: Optional[MugshotEthnicityScorer] = None,
    ethnic_db: Optional[EthnicNameDatabase] = None,
    face_min_conf: float = 0.75,
    name_min_conf: float = 0.5,
    combined_min_conf: float = 0.8,
    backend: str = "auto",
    only_with_photo: bool = True,
    progress: Optional[Any] = None,
) -> List[VerifyResult]:
    """
    Run mugshot verify on a list of misclassification hits or raw records.

    Designed to sit on top of ``SexOffenderSearcher.analyze_ethnicities``.
    """
    try:
        sc = scorer or MugshotEthnicityScorer(backend=backend)
    except BackendUnavailableError:
        raise

    eth_db = ethnic_db or get_ethnic_database()
    out: List[VerifyResult] = []
    n = len(items)
    for i, item in enumerate(items):
        if isinstance(item, Misclassification):
            rec = dict(item.record or {})
            ne = item.likely_ethnicity
            nc = float(item.confidence or 0.0)
        else:
            rec = dict(item or {})
            ne = None
            nc = None
        if only_with_photo:
            p = (rec.get("photo_path") or "").strip()
            if not p or not Path(p).is_file():
                continue
        result = verify_record(
            rec,
            scorer=sc,
            ethnic_db=eth_db,
            name_ethnicity=ne,
            name_confidence=nc,
            face_min_conf=face_min_conf,
            name_min_conf=name_min_conf,
            combined_min_conf=combined_min_conf,
        )
        out.append(result)
        if progress and (i + 1) % 25 == 0:
            try:
                progress(i + 1, n)
            except Exception:
                pass
    # Prefer confirmed misclass first
    out.sort(
        key=lambda r: (
            0 if r.confirms_misclass else 1,
            0 if r.verdict == "disagree" else 1,
            -r.combined_confidence,
        )
    )
    return out
