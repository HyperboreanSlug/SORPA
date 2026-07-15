"""Gate: only the designated publisher machine may upload public DB assets."""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# data/ is gitignored — this file never ships with clones or releases.
ALLOW_REL = Path("data") / "db_publish.allow"


def allow_path(root: Optional[Path] = None) -> Path:
    base = Path(root) if root else Path.cwd()
    return (base / ALLOW_REL).resolve()


def is_publish_allowed(root: Optional[Path] = None) -> bool:
    p = allow_path(root)
    if not p.is_file():
        return False
    try:
        if p.stat().st_size <= 0:
            return False
    except OSError:
        return False
    return True


def require_publish_allowed(root: Optional[Path] = None) -> Path:
    p = allow_path(root)
    if not is_publish_allowed(root):
        raise SystemExit(
            "Public DB upload is disabled on this machine.\n"
            f"Only the publisher instance may upload. Missing: {p}\n"
            "On the publisher machine run:\n"
            "  python scripts/enable_db_publish.py\n"
            "Clients download only — never create this file on other installs."
        )
    return p


def enable_publish(
    root: Optional[Path] = None,
    *,
    note: str = "",
) -> Path:
    """Create the local allow file for this machine only."""
    p = allow_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "enabled": True,
        "hostname": socket.gethostname(),
        "username": os.environ.get("USERNAME") or os.environ.get("USER") or "",
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": (note or "Local publisher only — do not copy to other machines."),
    }
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return p


def read_allow_meta(root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    p = allow_path(root)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"raw": True}
    except Exception:
        return {"exists": True}
