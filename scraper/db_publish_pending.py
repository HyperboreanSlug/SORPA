"""Track how many listings changed since the last public DB publish."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from scraper.paths import project_root

_LOCK = threading.Lock()
PENDING_REL = Path("releases") / "publish_state" / "pending.json"
DEFAULT_THRESHOLD = 2500


def pending_path(root: Optional[Path] = None) -> Path:
    return (Path(root) if root else project_root()) / PENDING_REL


def _load(root: Optional[Path] = None) -> Dict[str, Any]:
    p = pending_path(root)
    if not p.is_file():
        return {"pending_listings": 0}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"pending_listings": 0}
    except Exception:
        return {"pending_listings": 0}


def _save(data: Dict[str, Any], root: Optional[Path] = None) -> None:
    p = pending_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def get_pending_listings(root: Optional[Path] = None) -> int:
    with _LOCK:
        try:
            return max(0, int(_load(root).get("pending_listings") or 0))
        except (TypeError, ValueError):
            return 0


def add_pending_listings(n: int, root: Optional[Path] = None) -> int:
    """Add *n* changed listings; return new total."""
    if not n:
        return get_pending_listings(root)
    with _LOCK:
        data = _load(root)
        try:
            cur = max(0, int(data.get("pending_listings") or 0))
        except (TypeError, ValueError):
            cur = 0
        cur += max(0, int(n))
        data["pending_listings"] = cur
        _save(data, root)
        return cur


def clear_pending_listings(root: Optional[Path] = None) -> None:
    with _LOCK:
        data = _load(root)
        data["pending_listings"] = 0
        _save(data, root)


def should_publish(
    threshold: int = DEFAULT_THRESHOLD,
    root: Optional[Path] = None,
) -> bool:
    thr = max(1, int(threshold or DEFAULT_THRESHOLD))
    return get_pending_listings(root) >= thr
