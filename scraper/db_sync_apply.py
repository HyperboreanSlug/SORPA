"""Apply public delta ops onto a local offenders SQLite database."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from scraper.db_sync_keys import SYNC_ROW_COLUMNS, sync_record_key

_INSERT_COLS = list(SYNC_ROW_COLUMNS)
_INSERT_SQL = (
    "INSERT INTO offenders ("
    + ", ".join(_INSERT_COLS)
    + ") VALUES ("
    + ", ".join("?" * len(_INSERT_COLS))
    + ")"
)


def _row_tuple(row: Dict[str, Any]) -> tuple:
    return tuple(row.get(c) for c in _INSERT_COLS)


def build_key_index(conn: sqlite3.Connection) -> Dict[str, List[int]]:
    """Map sync key → list of offender ids (usually one)."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(offenders)")]
    want = [c for c in SYNC_ROW_COLUMNS if c in cols]
    if "id" not in cols:
        return {}
    sel = "id, " + ", ".join(want)
    out: Dict[str, List[int]] = {}
    for rec in conn.execute(f"SELECT {sel} FROM offenders"):
        d = {"id": rec[0]}
        for i, c in enumerate(want, start=1):
            d[c] = rec[i]
        key = sync_record_key(d)
        out.setdefault(key, []).append(int(rec[0]))
    return out


def apply_delta_ops(
    db_path: Path,
    ops: Iterable[Dict[str, Any]],
    *,
    key_index: Optional[Dict[str, List[int]]] = None,
) -> Tuple[int, int, int]:
    """
    Apply upsert/delete ops. Returns (upserts, deletes, errors).

    Rebuilds FTS is not required (FTS unused). Uses one connection.
    """
    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        idx = key_index if key_index is not None else build_key_index(conn)
        n_up = n_del = n_err = 0
        cur = conn.cursor()
        batch = 0
        for op in ops:
            try:
                kind = str(op.get("op") or "").lower()
                key = str(op.get("key") or "")
                if not key:
                    n_err += 1
                    continue
                if kind == "delete":
                    for rid in idx.pop(key, []):
                        cur.execute("DELETE FROM offenders WHERE id=?", (rid,))
                    n_del += 1
                elif kind == "upsert":
                    row = op.get("row")
                    if not isinstance(row, dict):
                        n_err += 1
                        continue
                    for rid in idx.pop(key, []):
                        cur.execute("DELETE FROM offenders WHERE id=?", (rid,))
                    cur.execute(_INSERT_SQL, _row_tuple(row))
                    new_id = int(cur.lastrowid)
                    idx[key] = [new_id]
                    n_up += 1
                else:
                    n_err += 1
                    continue
                batch += 1
                if batch % 2000 == 0:
                    conn.commit()
            except Exception:
                n_err += 1
        conn.commit()
        return n_up, n_del, n_err
    finally:
        conn.close()


def apply_delta_zip(db_path: Path, zip_path: Path) -> Tuple[int, int, int]:
    from scraper.db_sync_delta_io import iter_delta_ops

    return apply_delta_ops(db_path, iter_delta_ops(zip_path))
