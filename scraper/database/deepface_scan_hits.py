"""Recompute and list DeepFace hits against current offender race."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


class DeepfaceScanHitsMixin:
    """Hit flag recompute + Reports listing for ``deepface_scans``."""

    def recompute_deepface_hits(
        self,
        *,
        recorded_races: Optional[Iterable[str]] = None,
        face_labels: Optional[Iterable[str]] = None,
        min_confidence: float = 0.85,
    ) -> Dict[str, int]:
        """
        Recompute ``is_hit`` from *current* ``offenders.race`` + stored scores.

        Needed when race is edited after a scan (e.g. multi-source
        ``Black | White`` later collapses to ``White ✓``) — frozen ``is_hit``
        would otherwise hide high-confidence face conflicts.
        """
        from scraper.mugshot_ethnicity.labels import normalize_face_label
        from scraper.mugshot_ethnicity.scanner import _is_hit, _race_is_target
        from scraper.searcher import _canonical_race_key, format_race_label

        self._ensure_deepface_scans_table()
        rec_raw = list(recorded_races) if recorded_races is not None else ["WHITE"]
        face_raw = (
            list(face_labels)
            if face_labels is not None
            else ["black", "indian", "asian"]
        )
        targets = {
            _canonical_race_key(r) or str(r).strip().upper()
            for r in rec_raw
            if str(r).strip()
        }
        want = {normalize_face_label(f) for f in face_raw if str(f).strip()}
        want.discard("")
        want.discard("unknown")
        min_c = float(min_confidence or 0.0)

        rows = self._conn.execute(
            """
            SELECT s.offender_id, s.top_label, s.top_confidence, s.is_hit,
                   s.predicted_label, o.race
            FROM deepface_scans s
            JOIN offenders o ON o.id = s.offender_id
            """
        ).fetchall()
        promoted = demoted = unchanged = 0
        for r in rows:
            try:
                oid = int(r[0])
            except (TypeError, ValueError):
                continue
            lab = normalize_face_label(r[1] or r[4] or "")
            conf = float(r[2] or 0.0)
            was = bool(r[3])
            race = (r[5] or "").strip()
            now = bool(
                targets
                and want
                and _race_is_target(race, targets)
                and _is_hit(
                    lab,
                    conf,
                    race,
                    want_faces=want,
                    min_confidence=min_c,
                )
            )
            if now == was:
                unchanged += 1
                continue
            if now:
                severity = "high" if conf >= 0.9 else "medium"
                reason = (
                    f"Face scores {lab} at {conf:.0%} but registry race is "
                    f"{format_race_label(race) if race else race or '—'}"
                )
                self._conn.execute(
                    """
                    UPDATE deepface_scans SET
                        is_hit = 1,
                        predicted_label = COALESCE(NULLIF(predicted_label, ''), ?),
                        severity = ?,
                        reason = ?,
                        recorded_race = ?,
                        scan_min_conf = ?,
                        error = NULL
                    WHERE offender_id = ?
                    """,
                    (
                        lab,
                        severity,
                        reason,
                        format_race_label(race) if race else race,
                        min_c,
                        oid,
                    ),
                )
                promoted += 1
            else:
                self._conn.execute(
                    """
                    UPDATE deepface_scans SET
                        is_hit = 0,
                        severity = NULL,
                        reason = NULL
                    WHERE offender_id = ?
                    """,
                    (oid,),
                )
                demoted += 1
        self._conn.commit()
        hits_now = int(
            self._conn.execute(
                "SELECT COUNT(*) FROM deepface_scans WHERE is_hit = 1"
            ).fetchone()[0]
            or 0
        )
        return {
            "promoted": promoted,
            "demoted": demoted,
            "unchanged": unchanged,
            "hits": hits_now,
        }

    def list_deepface_hits(
        self,
        *,
        limit: int = 0,
        min_confidence: float = 0.0,
        state: Optional[str] = None,
        recompute: bool = True,
    ) -> List[Dict[str, Any]]:
        """Return hit rows joined with offender records for Reports.

        When *recompute* is True (default), refresh ``is_hit`` from the current
        offender race before listing so post-scan race edits are not missed.
        """
        self._ensure_deepface_scans_table()
        if recompute:
            try:
                from scraper.app_settings import load_settings

                sett = load_settings()
                raw_r = str(sett.get("deepface_scan_recorded") or "WHITE")
                raw_f = str(sett.get("deepface_scan_faces") or "black,indian,asian")
                recorded = [
                    p.strip().upper()
                    for p in raw_r.replace(";", ",").split(",")
                    if p.strip()
                ] or ["WHITE"]
                faces = [
                    p.strip().lower()
                    for p in raw_f.replace(";", ",").split(",")
                    if p.strip()
                ] or ["black", "indian", "asian"]
                conf = max(float(min_confidence or 0.0), 0.0)
                if conf <= 0:
                    try:
                        conf = float(str(sett.get("deepface_scan_min_conf") or "0.85"))
                    except ValueError:
                        conf = 0.85
                self.recompute_deepface_hits(
                    recorded_races=recorded,
                    face_labels=faces,
                    min_confidence=conf if conf > 0 else 0.85,
                )
            except Exception:
                pass
        sql = """
            SELECT s.offender_id
            FROM deepface_scans s
            JOIN offenders o ON o.id = s.offender_id
            WHERE s.is_hit = 1
              AND COALESCE(s.top_confidence, 0) >= ?
        """
        params: list = [float(min_confidence or 0.0)]
        if state:
            sql += " AND (UPPER(o.state) = UPPER(?) OR UPPER(o.source_state) = UPPER(?))"
            params.extend([state, state])
        sql += " ORDER BY s.top_confidence DESC, s.scanned_at DESC"
        if limit and int(limit) > 0:
            sql += " LIMIT ?"
            params.append(int(limit))
        ids = [int(r[0]) for r in self._conn.execute(sql, params).fetchall()]
        out: List[Dict[str, Any]] = []
        try:
            from scraper.mugshot_ethnicity.photo_resolve import photo_usable_for_scan
        except Exception:
            photo_usable_for_scan = None  # type: ignore[assignment]
        for oid in ids:
            scan = self.get_deepface_scan(oid)
            rec = self.get_offender_by_id(oid)
            if not scan or not rec:
                continue
            rec = dict(rec)
            photo = (rec.get("photo_path") or "").strip()
            scan_photo = (scan.get("photo_path") or "").strip()
            if photo_usable_for_scan is not None:
                if not photo_usable_for_scan(photo):
                    continue
                if scan_photo and not photo_usable_for_scan(scan_photo):
                    continue
            elif not photo:
                continue
            rec["_deepface"] = {
                "top_label": scan.get("top_label"),
                "top_confidence": scan.get("top_confidence"),
                "scores": scan.get("scores") or {},
                "backend": scan.get("backend"),
                "detector": scan.get("detector"),
                "is_hit": scan.get("is_hit"),
                "severity": scan.get("severity"),
                "reason": scan.get("reason"),
                "scanned_at": scan.get("scanned_at"),
                "predicted_label": scan.get("predicted_label"),
                "recorded_race_scan": scan.get("recorded_race"),
                "scan_photo_path": scan_photo or None,
            }
            rec["_deepface_is_hit"] = True
            out.append(rec)
        return out
