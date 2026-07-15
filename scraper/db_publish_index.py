"""Local publisher index: key → content hash of last published rows."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

STATE_DIR_REL = Path("releases") / "publish_state"
INDEX_NAME = "row_index.db"
META_NAME = "state.json"

# Rebuild full base when a delta would be this large (ops count).
DEFAULT_MAX_DELTA_OPS = 40_000


def state_dir(root: Path) -> Path:
    return root / STATE_DIR_REL


def index_path(root: Path) -> Path:
    return state_dir(root) / INDEX_NAME


def meta_path(root: Path) -> Path:
    return state_dir(root) / META_NAME


def load_meta(root: Path) -> Dict[str, Any]:
    p = meta_path(root)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_meta(root: Path, meta: Dict[str, Any]) -> None:
    d = state_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    meta_path(root).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def open_index(root: Path, *, write: bool = False) -> sqlite3.Connection:
    d = state_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    path = index_path(root)
    conn = sqlite3.connect(str(path))
    if write or not path.is_file() or path.stat().st_size < 32:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS rows ("
            "key TEXT PRIMARY KEY NOT NULL, chash TEXT NOT NULL)"
        )
        conn.commit()
    return conn


def load_key_hashes(root: Path) -> Dict[str, str]:
    p = index_path(root)
    if not p.is_file():
        return {}
    conn = open_index(root, write=False)
    try:
        return {str(k): str(h) for k, h in conn.execute("SELECT key, chash FROM rows")}
    finally:
        conn.close()


def replace_index(root: Path, pairs: Iterable[Tuple[str, str]]) -> int:
    """Replace entire index with (key, content_hash) pairs. Returns count."""
    conn = open_index(root, write=True)
    try:
        conn.execute("DELETE FROM rows")
        n = 0
        batch = []
        for key, chash in pairs:
            batch.append((key, chash))
            if len(batch) >= 5000:
                conn.executemany(
                    "INSERT OR REPLACE INTO rows(key, chash) VALUES (?, ?)", batch
                )
                n += len(batch)
                batch.clear()
        if batch:
            conn.executemany(
                "INSERT OR REPLACE INTO rows(key, chash) VALUES (?, ?)", batch
            )
            n += len(batch)
        conn.commit()
        return n
    finally:
        conn.close()


def apply_index_ops(
    root: Path,
    *,
    upserts: Dict[str, str],
    deletes: Iterable[str],
) -> None:
    """Patch index after a successful delta publish."""
    conn = open_index(root, write=True)
    try:
        dels = list(deletes)
        if dels:
            conn.executemany("DELETE FROM rows WHERE key=?", [(k,) for k in dels])
        if upserts:
            conn.executemany(
                "INSERT OR REPLACE INTO rows(key, chash) VALUES (?, ?)",
                list(upserts.items()),
            )
        conn.commit()
    finally:
        conn.close()
