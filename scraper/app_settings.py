"""
Persistent app settings for the SOR Public Archiver GUI.

Stored as JSON under data/app_settings.json (next to the default DB).
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_SETTINGS_PATH = Path("data/app_settings.json")

DEFAULTS: Dict[str, Any] = {
    # Database
    "db_path": "data/offenders.db",
    # Backups (optional — off by default; use Settings → Backup now or enable on-close)
    "backup_on_close": False,
    "backup_dir": "data/backups",
    "max_backups": 10,
    # NSOPW: shortest partials (min first+last = 3) → max coverage per search.
    # e.g. M+AH covers Ahmed/Ahmad; API extras (aliases) are still scraped.
    "nsopw_compact_prefixes": True,
    "nsopw_min_combined_len": 3,
    # DeepFace (local mugshot race model) — controlled on DeepFace tab
    "deepface_auto_setup": True,
    "deepface_auto_warm": True,
    "deepface_detector": "retinaface",
    # Comma-separated DeepFace.build_model names (Race required for mugshots)
    "deepface_weight_models": "Race",
    # DeepFace Scan sub-tab options
    "deepface_scan_state": "",
    "deepface_scan_min_conf": "0.85",
    "deepface_scan_limit": "0",
    "deepface_scan_recorded": "WHITE",
    "deepface_scan_faces": "black,indian,asian",

    # Public database sync from GitHub Releases (no local user PII in archives)
    "db_sync_enabled": False,
    "db_sync_prompted": False,
    "db_sync_on_startup": True,
    "db_sync_repo": "HyperboreanSlug/sor-public-archiver",
    "db_sync_tag": "database-latest",
}


def load_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load settings from disk, merging with defaults."""
    p = Path(path) if path else DEFAULT_SETTINGS_PATH
    settings = deepcopy(DEFAULTS)
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k in DEFAULTS:
                        settings[k] = v
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return normalize_settings(settings)


def save_settings(settings: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Write settings to disk (only known keys). Returns path written."""
    p = Path(path) if path else DEFAULT_SETTINGS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    clean = normalize_settings({**DEFAULTS, **(settings or {})})
    # Only persist known keys
    out = {k: clean[k] for k in DEFAULTS}
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def normalize_settings(s: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(DEFAULTS)
    out.update({k: s[k] for k in DEFAULTS if k in s})

    out["db_path"] = str(out.get("db_path") or DEFAULTS["db_path"]).strip() or DEFAULTS["db_path"]
    out["backup_dir"] = (
        str(out.get("backup_dir") or DEFAULTS["backup_dir"]).strip() or DEFAULTS["backup_dir"]
    )
    out["backup_on_close"] = bool(out.get("backup_on_close", False))
    out["nsopw_compact_prefixes"] = bool(out.get("nsopw_compact_prefixes", True))
    out["deepface_auto_setup"] = bool(out.get("deepface_auto_setup", True))
    out["deepface_auto_warm"] = bool(out.get("deepface_auto_warm", True))
    det = str(out.get("deepface_detector") or "retinaface").strip().lower()
    allowed_det = {
        "retinaface", "opencv", "ssd", "mtcnn", "yunet", "mediapipe", "centerface",
    }
    out["deepface_detector"] = det if det in allowed_det else "retinaface"
    wm = str(out.get("deepface_weight_models") or "Race").strip()
    parts = [p.strip() for p in wm.replace(";", ",").split(",") if p.strip()]
    if "Race" not in parts:
        parts.insert(0, "Race")
    out["deepface_weight_models"] = ",".join(parts)
    out["db_sync_enabled"] = bool(out.get("db_sync_enabled", False))
    out["db_sync_prompted"] = bool(out.get("db_sync_prompted", False))
    out["db_sync_on_startup"] = bool(out.get("db_sync_on_startup", True))
    out["db_sync_repo"] = (
        str(out.get("db_sync_repo") or DEFAULTS["db_sync_repo"]).strip()
        or DEFAULTS["db_sync_repo"]
    )
    out["db_sync_tag"] = (
        str(out.get("db_sync_tag") or DEFAULTS["db_sync_tag"]).strip()
        or DEFAULTS["db_sync_tag"]
    )

    try:
        mb = int(out.get("max_backups", 10))
    except (TypeError, ValueError):
        mb = 10
    out["max_backups"] = max(0, min(mb, 500))

    try:
        mcl = int(out.get("nsopw_min_combined_len", 3))
    except (TypeError, ValueError):
        mcl = 3
    # NSOPW requires >= 3; allow 3–10 for safety
    out["nsopw_min_combined_len"] = max(3, min(mcl, 10))

    return out
