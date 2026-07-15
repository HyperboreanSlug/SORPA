"""Package scrubbed DB (+ optional delta) and MANIFEST for GitHub Releases."""
from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from scraper.db_publish_diff import build_delta_ops, should_publish_full_base
from scraper.db_publish_index import (
    apply_index_ops,
    index_path,
    load_meta,
    replace_index,
    save_meta,
)
from scraper.db_publish_manifest import build_manifest, sha256_file
from scraper.db_sync_delta_io import delta_asset_name, write_delta_zip
from scraper.db_sync_keys import SYNC_ROW_COLUMNS, row_content_hash, sync_record_key

DELTA_PREFIX = "offenders.delta."


def _utc() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def zip_database(scrubbed_db: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as zf:
        zf.write(scrubbed_db, arcname="offenders.db")


def _record_count(db_path: Path) -> tuple[int, int]:
    conn = sqlite3.connect(str(db_path))
    try:
        n = int(conn.execute("SELECT COUNT(*) FROM offenders").fetchone()[0])
        with_photo = int(
            conn.execute(
                "SELECT COUNT(*) FROM offenders "
                "WHERE photo_path IS NOT NULL AND TRIM(photo_path) != ''"
            ).fetchone()[0]
        )
        return n, with_photo
    finally:
        conn.close()


def _rebuild_full_index(root: Path, db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(offenders)")]
        want = [c for c in SYNC_ROW_COLUMNS if c in cols]
        pairs = []
        for rec in conn.execute("SELECT " + ", ".join(want) + " FROM offenders"):
            d = {c: rec[c] for c in want}
            pairs.append((sync_record_key(d), row_content_hash(d)))
        return replace_index(root, pairs)
    finally:
        conn.close()


def package_db_release(
    root: Path,
    scrubbed_db: Path,
    *,
    photo_parts: Optional[List[Dict[str, Any]]] = None,
    full_base: bool = False,
    max_delta_ops: int = 40_000,
) -> Dict[str, Any]:
    """Write base zip and/or delta; refresh MANIFEST.json + publisher index."""
    root = Path(root)
    out_dir = root / "releases"
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / "offenders.db.zip"
    man_path = out_dir / "MANIFEST.json"
    photo_parts = list(photo_parts or [])

    nrec, with_photo = _record_count(scrubbed_db)
    meta = load_meta(root)
    has_index = index_path(root).is_file() and index_path(root).stat().st_size > 64
    ops, upsert_hashes, deleted, cur_n = build_delta_ops(scrubbed_db, root)
    use_base = should_publish_full_base(
        len(ops),
        cur_n,
        force=full_base,
        has_prior_index=has_index and bool(meta.get("base_sha256")),
        max_ops=max_delta_ops,
    )

    prev_man: Dict[str, Any] = {}
    if man_path.is_file():
        try:
            prev_man = json.loads(man_path.read_text(encoding="utf-8"))
        except Exception:
            prev_man = {}
    if not photo_parts and isinstance(prev_man.get("photos"), list):
        photo_parts = [p for p in prev_man["photos"] if isinstance(p, dict)]

    result: Dict[str, Any] = {
        "mode": "base" if use_base else ("delta" if ops else "noop"),
        "ops": len(ops),
        "record_count": nrec,
    }
    if not use_base and not ops:
        result["message"] = "No DB changes since last publish"
        return result

    if use_base:
        for old in out_dir.glob(f"{DELTA_PREFIX}*.zip"):
            try:
                old.unlink()
            except OSError:
                pass
        zip_database(scrubbed_db, zip_path)
        base_sha = sha256_file(zip_path)
        base_id = _utc().replace(":", "").replace("-", "")
        _rebuild_full_index(root, scrubbed_db)
        save_meta(
            root,
            {
                "base_id": base_id,
                "base_sha256": base_sha,
                "next_delta_seq": 1,
                "record_count": nrec,
                "updated_at_utc": _utc(),
            },
        )
        result.update({"base_id": base_id, "zip_path": str(zip_path), "sha256": base_sha})
        man = build_manifest(
            zip_path=zip_path,
            sha=base_sha,
            nrec=nrec,
            with_photo=with_photo,
            photo_parts=photo_parts,
            base_id=base_id,
            deltas=[],
        )
    else:
        seq = int(meta.get("next_delta_seq") or 1)
        dname = delta_asset_name(seq)
        dpath = out_dir / dname
        write_delta_zip(dpath, ops)
        dsha = sha256_file(dpath)
        if not zip_path.is_file() and not meta.get("base_sha256"):
            raise RuntimeError(
                "Delta publish needs a prior base (run once with --full-base)."
            )
        base_sha = str(meta.get("base_sha256") or sha256_file(zip_path))
        base_id = str(meta.get("base_id") or prev_man.get("base_id") or "legacy")
        prior = prev_man.get("deltas") if isinstance(prev_man.get("deltas"), list) else []
        deltas = [d for d in prior if isinstance(d, dict)]
        deltas.append(
            {
                "name": dname,
                "sha256": dsha,
                "size_bytes": dpath.stat().st_size,
                "ops": len(ops),
                "record_count_after": nrec,
                "created_at_utc": _utc(),
                "seq": seq,
            }
        )
        apply_index_ops(root, upserts=upsert_hashes, deletes=deleted)
        save_meta(
            root,
            {
                "base_id": base_id,
                "base_sha256": base_sha,
                "next_delta_seq": seq + 1,
                "record_count": nrec,
                "updated_at_utc": _utc(),
                "last_delta": dname,
            },
        )
        result.update(
            {"delta_path": str(dpath), "delta_name": dname, "sha256": base_sha}
        )
        man = build_manifest(
            zip_path=zip_path if zip_path.is_file() else None,
            sha=base_sha,
            nrec=nrec,
            with_photo=with_photo,
            photo_parts=photo_parts,
            base_id=base_id,
            deltas=deltas,
            size_bytes=int(prev_man.get("size_bytes") or 0)
            if not zip_path.is_file()
            else None,
        )

    man_path.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    result["manifest_path"] = str(man_path)
    result["manifest"] = man
    return result
