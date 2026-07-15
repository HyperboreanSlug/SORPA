"""
Persistent app settings for the SOR Public Archiver GUI.

Stored as JSON under ``<install>/data/app_settings.json`` (next to the default DB).
Paths are install-root-relative so another machine or folder move still loads.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

from scraper.paths import (
    DEFAULT_DB_REL,
    DEFAULT_SETTINGS_REL,
    project_root,
    sanitize_db_path,
    settings_path,
)

# Backward-compatible name (relative); load/save resolve against install root.
DEFAULT_SETTINGS_PATH = Path(DEFAULT_SETTINGS_REL)

DEFAULTS: Dict[str, Any] = {
    # Database
    "db_path": DEFAULT_DB_REL,
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
    "deepface_scan_force_rescan": False,

    # Public database sync from GitHub Releases (no local user PII in archives)
    "db_sync_enabled": False,
    "db_sync_prompted": False,
    "db_sync_on_startup": True,
    "db_sync_repo": "HyperboreanSlug/SORPA",
    "db_sync_tag": "database-latest",
    # Publisher: auto-upload when this many listings changed since last publish
    "db_publish_change_threshold": 2500,
    "db_auto_publish_enabled": True,
    # App code auto-update from GitHub (git fetch + ff-only pull on open)
    "auto_update_enabled": True,
}


def load_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load settings from disk, merging with defaults."""
    p = Path(path) if path else settings_path()
    # Legacy: relative path next to old cwd-based installs
    if not p.is_file() and path is None:
        legacy = Path.cwd() / DEFAULT_SETTINGS_REL
        if legacy.is_file() and legacy.resolve() != p.resolve():
            p = legacy
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
    p = Path(path) if path else settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    clean = normalize_settings({**DEFAULTS, **(settings or {})})
    out = {k: clean[k] for k in DEFAULTS}
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def normalize_settings(s: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(DEFAULTS)
    out.update({k: s[k] for k in DEFAULTS if k in s})

    root = project_root()
    out["db_path"] = sanitize_db_path(
        out.get("db_path") or DEFAULTS["db_path"],
        default=DEFAULTS["db_path"],
        root=root,
    )
    # backup_dir: portable under root; drop dead foreign absolutes
    bdir_raw = str(out.get("backup_dir") or DEFAULTS["backup_dir"]).strip() or DEFAULTS[
        "backup_dir"
    ]
    bdir_p = Path(bdir_raw).expanduser()
    if bdir_p.is_absolute():
        try:
            br = bdir_p.resolve()
            try:
                out["backup_dir"] = br.relative_to(root.resolve()).as_posix()
            except ValueError:
                if br.is_dir() or br.parent.is_dir():
                    out["backup_dir"] = str(br)
                else:
                    out["backup_dir"] = DEFAULTS["backup_dir"]
        except OSError:
            out["backup_dir"] = DEFAULTS["backup_dir"]
    else:
        out["backup_dir"] = Path(bdir_raw).as_posix()

    out["backup_on_close"] = bool(out.get("backup_on_close", False))
    out["nsopw_compact_prefixes"] = bool(out.get("nsopw_compact_prefixes", True))
    out["deepface_auto_setup"] = bool(out.get("deepface_auto_setup", True))
    out["deepface_auto_warm"] = bool(out.get("deepface_auto_warm", True))
    out["deepface_scan_force_rescan"] = bool(out.get("deepface_scan_force_rescan", False))
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
    out["auto_update_enabled"] = bool(out.get("auto_update_enabled", True))
    out["db_sync_repo"] = (
        str(out.get("db_sync_repo") or DEFAULTS["db_sync_repo"]).strip()
        or DEFAULTS["db_sync_repo"]
    )
    out["db_sync_tag"] = (
        str(out.get("db_sync_tag") or DEFAULTS["db_sync_tag"]).strip()
        or DEFAULTS["db_sync_tag"]
    )
    out["db_auto_publish_enabled"] = bool(out.get("db_auto_publish_enabled", True))
    try:
        thr = int(out.get("db_publish_change_threshold", 2500))
    except (TypeError, ValueError):
        thr = 2500
    # 1–1_000_000; default 2500 listings
    out["db_publish_change_threshold"] = max(1, min(thr, 1_000_000))

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


def resolved_db_path(settings: Optional[Dict[str, Any]] = None) -> Path:
    """Absolute SQLite path for the active settings (install-root anchored)."""
    from scraper.paths import resolve_under_root

    s = settings if settings is not None else load_settings()
    return resolve_under_root(s.get("db_path") or DEFAULTS["db_path"])
