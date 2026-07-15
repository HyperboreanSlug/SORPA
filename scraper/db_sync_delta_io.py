"""Read/write public DB delta zip assets (JSONL of upsert/delete ops)."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

DELTA_MEMBER = "delta.jsonl"
DELTA_PREFIX = "offenders.delta."


def delta_asset_name(seq: int) -> str:
    return f"{DELTA_PREFIX}{int(seq):04d}.zip"


def parse_delta_seq(name: str) -> Optional[int]:
    n = (name or "").strip()
    if not n.startswith(DELTA_PREFIX) or not n.endswith(".zip"):
        return None
    mid = n[len(DELTA_PREFIX) : -4]
    if not mid.isdigit():
        return None
    return int(mid)


def write_delta_zip(path: Path, ops: Iterable[Dict[str, Any]]) -> int:
    """Write *ops* as JSONL into a zip at *path*. Returns op count."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    if tmp.exists():
        tmp.unlink()
    n = 0
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        buf: List[str] = []
        for op in ops:
            if not isinstance(op, dict) or not op.get("op"):
                continue
            buf.append(json.dumps(op, ensure_ascii=False, separators=(",", ":")))
            n += 1
        zf.writestr(DELTA_MEMBER, "\n".join(buf) + ("\n" if buf else ""))
    tmp.replace(path)
    return n


def iter_delta_ops(zip_path: Path) -> Iterator[Dict[str, Any]]:
    """Yield op dicts from a delta zip."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        member = DELTA_MEMBER if DELTA_MEMBER in names else None
        if member is None:
            for n in names:
                if n.replace("\\", "/").endswith("delta.jsonl"):
                    member = n
                    break
        if not member:
            raise ValueError(f"{zip_path.name} has no delta.jsonl")
        with zf.open(member) as fh:
            for raw in fh:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and obj.get("op"):
                    yield obj


def load_delta_ops(zip_path: Path) -> List[Dict[str, Any]]:
    return list(iter_delta_ops(zip_path))
