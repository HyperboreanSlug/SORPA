"""Download and apply pending delta packs during public DB sync."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from scraper.db_sync_common import *  # noqa: F401,F403
from scraper.db_sync_part1 import _http_download_file, _log


def apply_pending_deltas(
    dest: Path,
    pending: List[Dict[str, Any]],
    *,
    extra_urls: Dict[str, str],
    base: str,
    tmp_dir: Path,
    apply_delta_zip: Callable,
    log: Optional[Callable[[str], None]],
) -> Tuple[int, int, List[str], Optional[SyncResult]]:
    """
    Returns (deltas_applied, bytes_written, applied_names, error_or_None).
    """
    deltas_applied = 0
    bytes_written = 0
    applied: List[str] = []
    for spec in pending:
        name = str(spec["name"])
        url = extra_urls.get(name) or f"{base}/{name}"
        part = tmp_dir / name
        _log(log, f"Downloading delta {name} …")
        try:
            _http_download_file(
                url,
                part,
                timeout=600.0,
                expected_sha256=spec.get("sha256"),
                log=log,
                label=name,
            )
        except Exception as e:
            return (
                deltas_applied,
                bytes_written,
                applied,
                SyncResult(False, "error", f"Delta download failed ({name}): {e}"),
            )
        bytes_written += part.stat().st_size
        try:
            up, de, err = apply_delta_zip(dest, part)
            _log(log, f"  Applied {name}: upserts={up} deletes={de} errors={err}")
        except Exception as e:
            return (
                deltas_applied,
                bytes_written,
                applied,
                SyncResult(False, "error", f"Delta apply failed ({name}): {e}"),
            )
        applied.append(name)
        deltas_applied += 1
        try:
            part.unlink()
        except OSError:
            pass
    return deltas_applied, bytes_written, applied, None
