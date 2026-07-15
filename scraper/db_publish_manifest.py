"""Build MANIFEST.json for public DB releases."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(
    *,
    zip_path: Optional[Path],
    sha: str,
    nrec: int,
    with_photo: int,
    photo_parts: List[Dict[str, Any]],
    base_id: str,
    deltas: List[Dict[str, Any]],
    size_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    if zip_path and zip_path.is_file():
        size_bytes = zip_path.stat().st_size
        if not sha:
            sha = sha256_file(zip_path)
    photo_bytes = sum(int(p.get("size_bytes") or 0) for p in photo_parts)
    photo_files_n = sum(int(p.get("file_count") or 0) for p in photo_parts)
    return {
        "format": 2,
        "asset": "offenders.db.zip",
        "db_name": "offenders.db",
        "sha256": sha,
        "size_bytes": int(size_bytes or 0),
        "created_at_utc": _utc(),
        "record_count": nrec,
        "records_with_photo_path": with_photo,
        "base_id": base_id,
        "deltas": deltas,
        "includes_photos": bool(photo_parts),
        "photos": photo_parts,
        "photo_part_count": len(photo_parts),
        "photo_file_count": photo_files_n,
        "photo_size_bytes": photo_bytes,
        "notes": (
            "Public U.S. sex offender registry archive. "
            "Clients apply offenders.delta.NNNN.zip after the base zip when present. "
            "Mugshots ship as offenders.photos.NNN.zip under data/report_pages/*/photos/."
        ),
    }
