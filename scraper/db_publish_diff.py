"""Diff scrubbed DB vs publish index → delta ops."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple

from scraper.db_publish_index import DEFAULT_MAX_DELTA_OPS, load_key_hashes
from scraper.db_sync_keys import (
    SYNC_ROW_COLUMNS,
    row_content_hash,
    row_to_sync_dict,
    sync_record_key,
)


def scan_db_rows(db_path: Path) -> Dict[str, Tuple[str, Dict[str, Any]]]:
    """
    key → (content_hash, row_dict) for all offenders.

    If multiple rows share a key, last wins (dedupe at publish time).
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(offenders)")]
        want = [c for c in SYNC_ROW_COLUMNS if c in cols]
        if not want:
            return {}
        sel = ", ".join(want)
        out: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        for rec in conn.execute(f"SELECT {sel} FROM offenders"):
            d = {c: rec[c] for c in want}
            key = sync_record_key(d)
            out[key] = (row_content_hash(d), row_to_sync_dict(d))
        return out
    finally:
        conn.close()


def build_delta_ops(
    db_path: Path,
    root: Path,
) -> Tuple[List[Dict[str, Any]], Dict[str, str], List[str], int]:
    """
    Returns (ops, new_upsert_hashes, deleted_keys, current_record_count).

    ops ready for write_delta_zip.
    """
    current = scan_db_rows(db_path)
    old = load_key_hashes(root)
    ops: List[Dict[str, Any]] = []
    upsert_hashes: Dict[str, str] = {}
    deleted: List[str] = []

    for key, (chash, row) in current.items():
        if old.get(key) != chash:
            ops.append({"op": "upsert", "key": key, "row": row})
            upsert_hashes[key] = chash

    for key in old:
        if key not in current:
            ops.append({"op": "delete", "key": key})
            deleted.append(key)

    return ops, upsert_hashes, deleted, len(current)


def should_publish_full_base(
    ops_count: int,
    record_count: int,
    *,
    force: bool = False,
    has_prior_index: bool = True,
    max_ops: int = DEFAULT_MAX_DELTA_OPS,
) -> bool:
    if force or not has_prior_index:
        return True
    if ops_count <= 0:
        return False
    if ops_count >= max_ops:
        return True
    if record_count > 0 and ops_count >= max(5000, int(record_count * 0.20)):
        return True
    return False
