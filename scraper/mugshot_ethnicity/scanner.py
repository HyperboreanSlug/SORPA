"""Independent mugshot scan for gross race misclassifications."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from scraper.database import Database
from scraper.mugshot_ethnicity.labels import (
    face_contradicts_recorded,
    is_gross_face_vs_white,
    normalize_face_label,
)
from scraper.mugshot_ethnicity.models import FaceEthnicityScore, GrossMisclassHit
from scraper.mugshot_ethnicity.scorer import BackendUnavailableError, MugshotEthnicityScorer
from scraper.searcher import Misclassification, _canonical_race_key, format_race_label


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


def _is_hit(
    lab: str,
    conf: float,
    race: str,
    *,
    want_faces: Set[str],
    min_confidence: float,
) -> bool:
    if conf < float(min_confidence):
        return False
    if lab not in want_faces:
        return False
    if face_contradicts_recorded(lab, race):
        return True
    if _canonical_race_key(race) == "WHITE" and is_gross_face_vs_white(lab):
        return True
    return False


def _store_scan(
    db: Database,
    rec: Dict[str, Any],
    face: FaceEthnicityScore,
    *,
    is_hit: bool,
    recorded_race: str,
    predicted_label: str,
    severity: str,
    reason: str,
    min_confidence: float,
    detector: str = "",
) -> None:
    oid = rec.get("id")
    if oid is None:
        return
    try:
        oid_i = int(oid)
    except (TypeError, ValueError):
        return
    try:
        db.upsert_deepface_scan(
            oid_i,
            photo_path=face.photo_path or rec.get("photo_path"),
            top_label=face.top_label,
            top_confidence=float(face.top_confidence or 0.0),
            scores=dict(face.scores or {}),
            backend=face.backend or "",
            detector=detector,
            face_detected=bool(face.face_detected),
            error=face.error,
            is_hit=is_hit,
            recorded_race=recorded_race,
            predicted_label=predicted_label,
            severity=severity,
            reason=reason,
            scan_min_conf=float(min_confidence),
        )
    except Exception:
        pass


def deepface_hit_to_misclassification(rec: Dict[str, Any]) -> Misclassification:
    """Convert a DB offender row with ``_deepface`` payload into a Reports row."""
    df = rec.get("_deepface") or {}
    race = (
        format_race_label(rec.get("race") or "")
        or (df.get("recorded_race_scan") or rec.get("race") or "—")
    )
    lab = normalize_face_label(df.get("predicted_label") or df.get("top_label") or "")
    conf = float(df.get("top_confidence") or 0.0)
    # Display face label as "likely ethnicity" for Reports cards
    eth = (lab or "unknown").replace("_", " ").title()
    if eth.lower() == "indian":
        eth = "Indian (South Asian)"
    names = ["deepface"]
    if lab:
        names.append(f"face:{lab}@{conf:.2f}")
    if df.get("severity"):
        names.append(f"severity:{df.get('severity')}")
    if df.get("reason"):
        names.append(str(df.get("reason"))[:80])
    out_rec = dict(rec)
    out_rec["_deepface"] = df
    out_rec["_deepface_is_hit"] = True
    out_rec["_source"] = "deepface"
    return Misclassification(
        record=out_rec,
        expected_race=str(race),
        likely_ethnicity=eth,
        confidence=conf,
        matching_names=names,
    )


def load_deepface_hits_as_misclass(
    db: Optional[Database] = None,
    *,
    db_path: Optional[str] = None,
    min_confidence: float = 0.0,
    state: Optional[str] = None,
    limit: int = 0,
) -> List[Misclassification]:
    """Load stored DeepFace hits for Reports."""
    own = False
    if db is None:
        db = Database(db_path or "data/offenders.db")
        own = True
    try:
        rows = db.list_deepface_hits(
            limit=limit,
            min_confidence=min_confidence,
            state=state,
        )
        return [deepface_hit_to_misclassification(r) for r in rows]
    finally:
        if own:
            db.close()


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
    cancel: Optional[Callable[[], bool]] = None,
    skip_scanned: bool = True,
    force_rescan: bool = False,
    persist: bool = True,
    detector: str = "",
) -> List[GrossMisclassHit]:
    """
    Scan mugshots for high-confidence face ethnicity that grossly contradicts
    the registry race (default: Black / Indian / Asian face vs race=White).

    Does **not** use surname lists — pure vision filter for gross errors.

    *skip_scanned* / *force_rescan*: by default, offenders already stored in
    ``deepface_scans`` (same photo fingerprint) are skipped. Pass
    ``force_rescan=True`` (or ``skip_scanned=False``) to score them again.

    *persist*: write every scored result to ``deepface_scans`` (hits and non-hits).
    """
    def _log(msg: str) -> None:
        if log:
            log(msg)

    def _cancelled() -> bool:
        if not cancel:
            return False
        try:
            return bool(cancel())
        except Exception:
            return False

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
    want_faces = {
        normalize_face_label(x)
        for x in (face_labels or ("black", "indian", "asian"))
    }
    want_faces.discard("unknown")

    already: Set[int] = set()
    if skip_scanned and not force_rescan:
        try:
            already = db.get_deepface_scanned_ids(current_photo_only=True)
        except Exception as e:
            _log(f"Could not load prior DeepFace scans (continuing): {e}")
            already = set()

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
        params.append(int(limit) * 8 if int(limit) < 50000 else int(limit))

    rows = [dict(r) for r in db._conn.execute(sql, params).fetchall()]
    candidates = []
    skipped = 0
    for rec in rows:
        race = (rec.get("race") or "").strip()
        if not _race_is_target(race, targets):
            continue
        photo = (rec.get("photo_path") or "").strip()
        if require_photo and (not photo or not Path(photo).is_file()):
            continue
        try:
            oid = int(rec["id"]) if rec.get("id") is not None else None
        except (TypeError, ValueError):
            oid = None
        if oid is not None and oid in already:
            skipped += 1
            continue
        candidates.append(rec)
        if limit and int(limit) > 0 and len(candidates) >= int(limit):
            break

    _log(
        f"Mugshot gross-scan: {len(candidates)} to score"
        + (f", skipped {skipped} already scanned" if skipped else "")
        + f" (recorded∈{sorted(targets)}, face∈{sorted(want_faces)}, "
        f"min_conf={min_confidence}, backend={sc.backend_name}"
        f"{', rescan' if force_rescan or not skip_scanned else ''})"
    )

    # Also surface prior hits when skipping (so UI list is complete)
    hits: List[GrossMisclassHit] = []
    if skip_scanned and not force_rescan and skipped:
        try:
            for rec in db.list_deepface_hits(
                min_confidence=float(min_confidence),
                state=state,
            ):
                if limit and int(limit) > 0 and len(hits) >= int(limit) * 2:
                    break
                df = rec.get("_deepface") or {}
                lab = normalize_face_label(
                    df.get("predicted_label") or df.get("top_label") or ""
                )
                conf = float(df.get("top_confidence") or 0.0)
                race = (rec.get("race") or "").strip()
                if not _race_is_target(race, targets):
                    continue
                if not _is_hit(
                    lab, conf, race, want_faces=want_faces, min_confidence=min_confidence
                ):
                    continue
                face = FaceEthnicityScore(
                    photo_path=(rec.get("photo_path") or ""),
                    top_label=lab,
                    top_confidence=conf,
                    scores=dict(df.get("scores") or {}),
                    backend=str(df.get("backend") or "deepface"),
                    face_detected=True,
                )
                hits.append(
                    GrossMisclassHit(
                        record=rec,
                        recorded_race=format_race_label(race) if race else race,
                        face=face,
                        predicted_label=lab,
                        confidence=conf,
                        severity=str(df.get("severity") or ("high" if conf >= 0.9 else "medium")),
                        reason=str(df.get("reason") or ""),
                    )
                )
        except Exception as e:
            _log(f"Could not load prior DeepFace hits: {e}")

    total = len(candidates)
    scored = 0
    for i, rec in enumerate(candidates):
        if _cancelled():
            _log(
                f"Mugshot gross-scan cancelled after {i}/{total} new candidates "
                f"({len(hits)} hits total)"
            )
            break
        if progress and (i % 10 == 0 or i + 1 == total):
            try:
                progress(i + 1, total)
            except Exception:
                pass
        face = sc.score_record(rec)
        scored += 1
        race = (rec.get("race") or "").strip()
        race_disp = format_race_label(race) if race else race
        lab = normalize_face_label(face.top_label) if face.ok else "unknown"
        conf = float(face.top_confidence or 0.0) if face.ok else 0.0
        hit = bool(
            face.ok
            and _is_hit(
                lab, conf, race, want_faces=want_faces, min_confidence=min_confidence
            )
        )
        severity = ""
        reason = ""
        if hit:
            severity = "high" if conf >= 0.9 else "medium"
            reason = (
                f"Face scores {lab} at {conf:.0%} but registry race is "
                f"{race_disp or race}"
            )
            hits.append(
                GrossMisclassHit(
                    record=rec,
                    recorded_race=race_disp or race,
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

        if persist:
            _store_scan(
                db,
                rec,
                face,
                is_hit=hit,
                recorded_race=race_disp or race,
                predicted_label=lab if face.ok else "",
                severity=severity,
                reason=reason,
                min_confidence=min_confidence,
                detector=detector,
            )

    # Dedupe hits by offender id (prior + new)
    by_id: Dict[Any, GrossMisclassHit] = {}
    for h in hits:
        rid = (h.record or {}).get("id")
        key = rid if rid is not None else id(h)
        prev = by_id.get(key)
        if prev is None or h.confidence > prev.confidence:
            by_id[key] = h
    hits = list(by_id.values())
    hits.sort(key=lambda h: (-h.confidence, h.predicted_label))
    _log(
        f"Mugshot gross-scan done: {len(hits)} hits "
        f"({scored} newly scored, {skipped} skipped already scanned)"
    )
    if own_db:
        db.close()
    return hits
