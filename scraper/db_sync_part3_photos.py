"""Selective mugshot part download for public DB sync."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from scraper.db_sync_common import *  # noqa: F401,F403
from scraper.db_sync_part1 import _http_download_file, _log
from scraper.db_sync_part2 import _extract_photo_zip


def download_needed_photos(
    need_photos: List[Dict[str, Any]],
    *,
    extra_urls: Dict[str, str],
    base: str,
    tmp_dir: Path,
    project_root: Path,
    local_photo_parts: Dict[str, str],
    log: Optional[Callable[[str], None]],
) -> Tuple[int, int, Optional[SyncResult]]:
    """
    Download/extract photo parts. Returns (extracted_count, bytes_written, error_or_None).
    """
    photos_extracted = 0
    bytes_written = 0
    for spec in need_photos:
        name = str(spec["name"])
        url = extra_urls.get(name) or f"{base}/{name}"
        part_path = tmp_dir / name
        _log(log, f"Downloading mugshots {name} …")
        try:
            digest = _http_download_file(
                url,
                part_path,
                timeout=1800.0,
                expected_sha256=spec.get("sha256"),
                log=log,
                label=name,
            )
        except HTTPError as e:
            if e.code == 404:
                _log(log, f"  Skipping missing photo asset {name}")
                continue
            return (
                photos_extracted,
                bytes_written,
                SyncResult(
                    False, "error", f"HTTP {e.code} downloading {name}: {e.reason}"
                ),
            )
        except Exception as e:
            return (
                photos_extracted,
                bytes_written,
                SyncResult(False, "error", f"Photo download failed ({name}): {e}"),
            )
        bytes_written += part_path.stat().st_size
        try:
            photos_extracted += _extract_photo_zip(part_path, project_root, log=log)
        except Exception as e:
            return (
                photos_extracted,
                bytes_written,
                SyncResult(False, "error", f"Photo extract failed ({name}): {e}"),
            )
        local_photo_parts[name] = str(spec.get("sha256") or digest)
        try:
            part_path.unlink()
        except OSError:
            pass
    return photos_extracted, bytes_written, None
