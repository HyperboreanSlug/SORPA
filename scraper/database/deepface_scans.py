"""Persist DeepFace mugshot scan results so photos are not rescanned by default."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from scraper.database.constants import _utc_now_iso
from scraper.database.deepface_scan_hits import DeepfaceScanHitsMixin


def photo_fingerprint(photo_path: Optional[str]) -> Optional[str]:
    """Stable fingerprint for a local photo (path + size + mtime)."""
    raw = (photo_path or "").strip()
    if not raw:
        return None
    p = Path(raw)
    try:
        if not p.is_file():
            return None
        st = p.stat()
        try:
            resolved = str(p.resolve())
        except OSError:
            resolved = str(p)
        return f"{resolved}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        return None


class DeepfaceScanMixin(DeepfaceScanHitsMixin):
    """CRUD for ``deepface_scans`` table (one latest row per offender)."""

    def _ensure_deepface_scans_table(self, cursor: Optional[sqlite3.Cursor] = None) -> None:
        cur = cursor or self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS deepface_scans (
                offender_id INTEGER PRIMARY KEY,
                photo_path TEXT,
                photo_fingerprint TEXT,
                scanned_at TEXT NOT NULL,
                top_label TEXT,
                top_confidence REAL,
                scores_json TEXT,
                backend TEXT,
                detector TEXT,
                face_detected INTEGER DEFAULT 1,
                error TEXT,
                is_hit INTEGER DEFAULT 0,
                recorded_race TEXT,
                predicted_label TEXT,
                severity TEXT,
                reason TEXT,
                scan_min_conf REAL,
                FOREIGN KEY (offender_id) REFERENCES offenders(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_deepface_scans_hit "
            "ON deepface_scans(is_hit) WHERE is_hit = 1"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_deepface_scans_scanned_at "
            "ON deepface_scans(scanned_at)"
        )
        if cursor is None:
            self._conn.commit()

    def get_deepface_scanned_ids(
        self,
        *,
        current_photo_only: bool = True,
    ) -> Set[int]:
        """
        Offender ids that already have a DeepFace scan.

        If *current_photo_only*, exclude rows whose stored fingerprint no longer
        matches the offender's current photo (file changed → eligible to rescan).
        """
        self._ensure_deepface_scans_table()
        rows = self._conn.execute(
            """
            SELECT s.offender_id, s.photo_fingerprint, o.photo_path
            FROM deepface_scans s
            LEFT JOIN offenders o ON o.id = s.offender_id
            """
        ).fetchall()
        out: Set[int] = set()
        for r in rows:
            try:
                oid = int(r[0])
            except (TypeError, ValueError):
                continue
            if not current_photo_only:
                out.add(oid)
                continue
            stored_fp = (r[1] or "").strip()
            cur_fp = photo_fingerprint(r[2] if len(r) > 2 else None)
            if stored_fp and cur_fp and stored_fp == cur_fp:
                out.add(oid)
            elif stored_fp and not cur_fp:
                # Photo missing now — still treat as scanned (nothing to score)
                out.add(oid)
            elif not stored_fp:
                # Legacy/incomplete row: treat as scanned until force rescan
                out.add(oid)
        return out

    def count_deepface_scans(self) -> Dict[str, int]:
        self._ensure_deepface_scans_table()
        total = self._conn.execute("SELECT COUNT(*) FROM deepface_scans").fetchone()[0]
        hits = self._conn.execute(
            "SELECT COUNT(*) FROM deepface_scans WHERE is_hit = 1"
        ).fetchone()[0]
        return {"total": int(total or 0), "hits": int(hits or 0)}

    def upsert_deepface_scan(
        self,
        offender_id: int,
        *,
        photo_path: Optional[str] = None,
        top_label: Optional[str] = None,
        top_confidence: float = 0.0,
        scores: Optional[Dict[str, float]] = None,
        backend: str = "",
        detector: str = "",
        face_detected: bool = True,
        error: Optional[str] = None,
        is_hit: bool = False,
        recorded_race: str = "",
        predicted_label: str = "",
        severity: str = "",
        reason: str = "",
        scan_min_conf: Optional[float] = None,
        scanned_at: Optional[str] = None,
    ) -> None:
        self._ensure_deepface_scans_table()
        fp = photo_fingerprint(photo_path)
        scores_json = json.dumps(scores or {}, ensure_ascii=False)
        self._conn.execute(
            """
            INSERT INTO deepface_scans (
                offender_id, photo_path, photo_fingerprint, scanned_at,
                top_label, top_confidence, scores_json, backend, detector,
                face_detected, error, is_hit, recorded_race, predicted_label,
                severity, reason, scan_min_conf
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(offender_id) DO UPDATE SET
                photo_path=excluded.photo_path,
                photo_fingerprint=excluded.photo_fingerprint,
                scanned_at=excluded.scanned_at,
                top_label=excluded.top_label,
                top_confidence=excluded.top_confidence,
                scores_json=excluded.scores_json,
                backend=excluded.backend,
                detector=excluded.detector,
                face_detected=excluded.face_detected,
                error=excluded.error,
                is_hit=excluded.is_hit,
                recorded_race=excluded.recorded_race,
                predicted_label=excluded.predicted_label,
                severity=excluded.severity,
                reason=excluded.reason,
                scan_min_conf=excluded.scan_min_conf
            """,
            (
                int(offender_id),
                (photo_path or "").strip() or None,
                fp,
                scanned_at or _utc_now_iso(),
                (top_label or "").strip() or None,
                float(top_confidence or 0.0),
                scores_json,
                (backend or "").strip() or None,
                (detector or "").strip() or None,
                1 if face_detected else 0,
                (error or None),
                1 if is_hit else 0,
                (recorded_race or "").strip() or None,
                (predicted_label or top_label or "").strip() or None,
                (severity or "").strip() or None,
                (reason or "").strip() or None,
                float(scan_min_conf) if scan_min_conf is not None else None,
            ),
        )
        self._conn.commit()

    def get_deepface_scan(self, offender_id: int) -> Optional[Dict[str, Any]]:
        self._ensure_deepface_scans_table()
        row = self._conn.execute(
            "SELECT * FROM deepface_scans WHERE offender_id = ?",
            (int(offender_id),),
        ).fetchone()
        if not row:
            return None
        return self._deepface_scan_row_to_dict(row)

    def get_deepface_scans_map(
        self,
        offender_ids: Iterable[int],
    ) -> Dict[int, Dict[str, Any]]:
        """Bulk lookup: offender_id → scan dict (only ids that have a row)."""
        ids = sorted({int(x) for x in offender_ids if x is not None})
        if not ids:
            return {}
        self._ensure_deepface_scans_table()
        out: Dict[int, Dict[str, Any]] = {}
        # Chunk to stay under SQLite variable limits
        chunk = 400
        for i in range(0, len(ids), chunk):
            part = ids[i : i + chunk]
            placeholders = ",".join("?" * len(part))
            rows = self._conn.execute(
                f"SELECT * FROM deepface_scans WHERE offender_id IN ({placeholders})",
                part,
            ).fetchall()
            for row in rows:
                d = self._deepface_scan_row_to_dict(row)
                try:
                    oid = int(d.get("offender_id"))
                except (TypeError, ValueError):
                    continue
                out[oid] = d
        return out

    def clear_deepface_scans(
        self,
        *,
        offender_ids: Optional[Iterable[int]] = None,
        hits_only: bool = False,
    ) -> int:
        """Delete scan rows. Returns number of deleted rows."""
        self._ensure_deepface_scans_table()
        if offender_ids is not None:
            ids = [int(x) for x in offender_ids]
            if not ids:
                return 0
            placeholders = ",".join("?" * len(ids))
            sql = f"DELETE FROM deepface_scans WHERE offender_id IN ({placeholders})"
            if hits_only:
                sql += " AND is_hit = 1"
            cur = self._conn.execute(sql, ids)
        else:
            sql = "DELETE FROM deepface_scans"
            if hits_only:
                sql += " WHERE is_hit = 1"
            cur = self._conn.execute(sql)
        self._conn.commit()
        return int(cur.rowcount or 0)

    @staticmethod
    def _deepface_scan_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        raw = d.get("scores_json") or "{}"
        try:
            scores = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (TypeError, json.JSONDecodeError):
            scores = {}
        d["scores"] = scores if isinstance(scores, dict) else {}
        d["is_hit"] = bool(d.get("is_hit"))
        d["face_detected"] = bool(d.get("face_detected", 1))
        return d
