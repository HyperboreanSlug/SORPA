"""Independent mugshot scan for gross race misclassifications."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set

from scraper.database import Database
from scraper.mugshot_ethnicity.labels import (
    face_contradicts_recorded,
    is_gross_face_vs_white,
    normalize_face_label,
    registry_race_to_face_labels,
)
from scraper.mugshot_ethnicity.models import GrossMisclassHit
from scraper.mugshot_ethnicity.scorer import BackendUnavailableError, MugshotEthnicityScorer
from scraper.searcher import _canonical_race_key, format_race_label


# Recorded races we treat as "should not look Black/Indian/Asian"
_DEFAULT_RECORDED_TARGETS = frozenset({"WHITE"})


def _race_is_target(recorded_race: str, targets: Set[str]) -> bool:
    key = _canonical_race_key(recorded_race or "")
    if key in targets:
        return True
    # Also match multi-source displays containing White without Asian/Black
    raw = (recorded_race or "").upper()
    if "WHITE" in targets or "W" in targets:
        if "WHITE" in raw or raw.strip() in ("W",):
            # multi: "W [FL] | Asian" — still has white; scan if white present
            # For gross scan we want pure white listings primarily
            if "BLACK" in raw or "ASIAN" in raw or "INDIAN" in raw:
                return False
            return True
    return False


def scan_gross_misclassifications(
    db: Optional[Database] = None,
    *,
    db_path: Optional[str] = None,
    scorer: Optional[MugshotEthnicityScorer] = None,
    backend: str = "auto",
    recorded_races: Optional[Sequence[str]] = None,
    # Face labels that trigger a hit when race is in recorded_races
    face_labels: Optional[Sequence[str]] = None,
    min_confidence: float = 0.85,
    limit: int = 0,
    state: Optional[str] = None,
    require_photo: bool = True,
    progress: Optional[Callable[[int, int], None]] = None,
    log: Optional[Callable[[str], None]] = None,
) -> List[GrossMisclassHit]:
    """
    Scan mugshots for high-confidence face ethnicity that grossly contradicts
    the registry race (default: Black / Indian / Asian face vs race=White).

    Does **not** use surname lists — pure vision filter for gross errors.
    """
    def _log(msg: str) -> None:
        if log:
            log(msg)

    own_db = False
    if db is None:
        db = Database(db_path or "data/offenders.db")
        own_db = True

    try:
        sc = scorer or MugshotEthnicityScorer(backend=backend)
    except BackendUnavailableError:
        if own_db:
            db.close()
        raise

    targets = {
        _canonical_race_key(r) for r in (recorded_races or list(_DEFAULT_RECORDED_TARGETS))
    }
    # normalize W → WHITE already via canonical
    want_faces = {
        normalize_face_label(x)
        for x in (face_labels or ("black", "indian", "asian"))
    }
    want_faces.discard("unknown")

    # Collect candidates with photos
    sql = (
        "SELECT * FROM offenders "
        "WHERE photo_path IS NOT NULL AND TRIM(photo_path) != '' "
        "AND race IS NOT NULL AND TRIM(race) != ''"
    )
    params: list = []
    if state:
        sql += " AND (UPPER(state) = UPPER(?) OR UPPER(source_state) = UPPER(?))"
        params.extend([state, state])
    sql += " ORDER BY id ASC"
    if limit and int(limit) > 0:
        # Over-fetch then filter — race text matching is in Python
        sql += " LIMIT ?"
        params.append(int(limit) * 5 if int(limit) < 50000 else int(limit))

    rows = [dict(r) for r in db._conn.execute(sql, params).fetchall()]
    candidates = []
    for rec in rows:
        race = (rec.get("race") or "").strip()
        if not _race_is_target(race, targets):
            continue
        photo = (rec.get("photo_path") or "").strip()
        if require_photo and (not photo or not Path(photo).is_file()):
            continue
        candidates.append(rec)
        if limit and int(limit) > 0 and len(candidates) >= int(limit):
            break

    _log(
        f"Mugshot gross-scan: {len(candidates)} candidates "
        f"(recorded∈{sorted(targets)}, face∈{sorted(want_faces)}, "
        f"min_conf={min_confidence}, backend={sc.backend_name})"
    )

    hits: List[GrossMisclassHit] = []
    total = len(candidates)
    for i, rec in enumerate(candidates):
        if progress and (i % 10 == 0 or i + 1 == total):
            try:
                progress(i + 1, total)
            except Exception:
                pass
        face = sc.score_record(rec)
        if not face.ok:
            continue
        lab = normalize_face_label(face.top_label)
        conf = float(face.top_confidence or 0.0)
        if conf < float(min_confidence):
            continue
        if lab not in want_faces:
            continue
        race = (rec.get("race") or "").strip()
        if not face_contradicts_recorded(lab, race) and not (
            _canonical_race_key(race) == "WHITE" and is_gross_face_vs_white(lab)
        ):
            continue

        severity = "high" if conf >= 0.9 else "medium"
        reason = (
            f"Face scores {lab} at {conf:.0%} but registry race is "
            f"{format_race_label(race) or race}"
        )
        hits.append(
            GrossMisclassHit(
                record=rec,
                recorded_race=format_race_label(race) if race else race,
                face=face,
                predicted_label=lab,
                confidence=conf,
                severity=severity,
                reason=reason,
            )
        )
        _log(
            f"  HIT id={rec.get('id')} "
            f"{rec.get('first_name')} {rec.get('last_name')} "
            f"race={race} face={lab}@{conf:.2f}"
        )

    hits.sort(key=lambda h: (-h.confidence, h.predicted_label))
    _log(f"Mugshot gross-scan done: {len(hits)} hits / {total} scored candidates")
    if own_db:
        db.close()
    return hits
