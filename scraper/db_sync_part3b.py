"""Body of public DB sync: base install, deltas, selective photos."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from scraper.db_sync_common import *  # noqa: F401,F403
from scraper.db_sync_part1 import _http_download_file, _log, _utc_now, resolve_release_urls
from scraper.db_sync_part2 import (
    _load_stamp,
    _photo_fingerprint,
    _photos_present_locally,
    pending_delta_specs,
    photo_parts_needing_download,
)
from scraper.db_sync_part3_deltas import apply_pending_deltas
from scraper.db_sync_part3_photos import download_needed_photos


def _write_stamp(
    dest: Path,
    *,
    remote: Optional[Dict[str, Any]],
    repo: str,
    tag: str,
    record_count: int,
    project_root: Path,
    applied_deltas: List[str],
    local_photo_parts: Dict[str, str],
    photos_extracted: int,
) -> None:
    stamp = {
        "format": 2,
        "remote_sha256": (remote or {}).get("sha256"),
        "base_id": (remote or {}).get("base_id"),
        "remote_record_count": (remote or {}).get("record_count") or record_count,
        "remote_photos_fingerprint": _photo_fingerprint(remote),
        "local_photo_parts": local_photo_parts,
        "applied_deltas": applied_deltas,
        "photos_extracted": photos_extracted,
        "synced_at_utc": _utc_now(),
        "repo": repo,
        "tag": tag,
        "local_record_count": record_count,
        "project_root": str(project_root),
    }
    dest.with_suffix(dest.suffix + ".sync.json").write_text(
        json.dumps(stamp, indent=2) + "\n", encoding="utf-8"
    )


def _install_base_from_zip(zip_path: Path, dest: Path, log: Optional[Callable]) -> int:
    extract_dir = zip_path.parent / "out"
    extract_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        member = None
        for n in zf.namelist():
            if n.replace("\\", "/").endswith("offenders.db") and not n.endswith("/"):
                member = n
                break
        if not member:
            raise ValueError("Zip does not contain offenders.db")
        zf.extract(member, extract_dir)
        extracted = extract_dir / member
        if not extracted.is_file():
            candidates = list(extract_dir.rglob("offenders.db"))
            if not candidates:
                raise ValueError("Failed to extract offenders.db")
            extracted = candidates[0]
    conn = sqlite3.connect(str(extracted))
    n = int(conn.execute("SELECT COUNT(*) FROM offenders").fetchone()[0])
    conn.close()
    if dest.is_file():
        bak = dest.with_suffix(
            dest.suffix + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        try:
            shutil.copy2(dest, bak)
            _log(log, f"Backed up previous DB → {bak.name}")
        except Exception as e:
            _log(log, f"Could not backup previous DB: {e}")
    tmp_dest = dest.with_suffix(dest.suffix + ".download")
    if tmp_dest.exists():
        tmp_dest.unlink()
    shutil.copy2(extracted, tmp_dest)
    os.replace(str(tmp_dest), str(dest))
    return n


def _count_offenders(db_path: Path) -> Optional[int]:
    try:
        conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM offenders").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return None


def run_db_sync(
    dest: Path,
    *,
    remote: Optional[Dict[str, Any]],
    repo: str,
    tag: str,
    project_root: Path,
    log: Optional[Callable[[str], None]],
    apply_delta_zip: Callable,
) -> SyncResult:
    stamp = _load_stamp(dest)
    zip_url, _, extra_urls = resolve_release_urls(repo=repo, tag=tag)
    base = f"https://github.com/{repo}/releases/download/{tag}"

    photo_specs: List[Dict[str, Any]] = []
    if remote and isinstance(remote.get("photos"), list):
        photo_specs = [
            p for p in remote["photos"] if isinstance(p, dict) and p.get("name")
        ]
    if not photo_specs:
        for name in sorted(extra_urls):
            if name.startswith(PHOTO_ASSET_PREFIX):
                photo_specs.append({"name": name, "sha256": None})
    for spec in photo_specs:
        name = str(spec["name"])
        if name not in extra_urls:
            extra_urls[name] = f"{base}/{name}"

    remote_sha = (remote or {}).get("sha256")
    base_matches = bool(
        dest.is_file()
        and dest.stat().st_size > 1000
        and stamp.get("remote_sha256")
        and remote_sha
        and stamp.get("remote_sha256") == remote_sha
    )
    if base_matches and stamp.get("base_id") and (remote or {}).get("base_id"):
        base_matches = stamp.get("base_id") == (remote or {}).get("base_id")

    pending = pending_delta_specs(remote, stamp) if base_matches else []
    need_base = not base_matches
    need_photos = photo_parts_needing_download(remote, stamp) if photo_specs else []
    if (
        not need_base
        and remote
        and remote.get("includes_photos")
        and not _photos_present_locally(dest)
        and photo_specs
    ):
        need_photos = photo_specs

    existed = dest.is_file() and dest.stat().st_size > 1000
    tmp_dir = Path(tempfile.mkdtemp(prefix="sor_db_sync_"))
    photos_extracted = 0
    bytes_written = 0
    deltas_applied = 0
    applied_deltas: List[str] = (
        list(stamp.get("applied_deltas") or []) if base_matches else []
    )
    local_photo_parts: Dict[str, str] = dict(stamp.get("local_photo_parts") or {})
    if not isinstance(local_photo_parts, dict):
        local_photo_parts = {}
    n = 0

    try:
        if need_base:
            zip_path = tmp_dir / "offenders.db.zip"
            _log(log, f"Downloading base database {zip_url} …")
            try:
                _http_download_file(
                    zip_url,
                    zip_path,
                    timeout=600.0,
                    expected_sha256=remote_sha,
                    log=log,
                    label="database zip",
                )
            except Exception as e:
                return SyncResult(False, "error", f"Base download failed: {e}")
            bytes_written += zip_path.stat().st_size
            try:
                n = _install_base_from_zip(zip_path, dest, log)
            except Exception as e:
                return SyncResult(False, "error", f"Base install failed: {e}")
            applied_deltas = []
            pending = []
            if remote and isinstance(remote.get("deltas"), list):
                pending = [
                    d
                    for d in remote["deltas"]
                    if isinstance(d, dict) and d.get("name")
                ]
        else:
            n = _count_offenders(dest) or int(stamp.get("local_record_count") or 0)

        da, db, names, derr = apply_pending_deltas(
            dest,
            pending,
            extra_urls=extra_urls,
            base=base,
            tmp_dir=tmp_dir,
            apply_delta_zip=apply_delta_zip,
            log=log,
        )
        if derr is not None:
            return derr
        deltas_applied = da
        bytes_written += db
        applied_deltas.extend(names)

        if deltas_applied or need_base:
            n = _count_offenders(dest) or n

        pe, pb, err = download_needed_photos(
            need_photos,
            extra_urls=extra_urls,
            base=base,
            tmp_dir=tmp_dir,
            project_root=project_root,
            local_photo_parts=local_photo_parts,
            log=log,
        )
        if err is not None:
            return err
        photos_extracted += pe
        bytes_written += pb

        _write_stamp(
            dest,
            remote=remote,
            repo=repo,
            tag=tag,
            record_count=n,
            project_root=project_root,
            applied_deltas=applied_deltas,
            local_photo_parts=local_photo_parts,
            photos_extracted=photos_extracted,
        )

        action = "updated" if existed else "downloaded"
        bits = [f"{n:,} records"]
        if deltas_applied:
            bits.append(f"{deltas_applied} delta(s)")
        if photos_extracted:
            bits.append(f"{photos_extracted:,} mugshots")
        msg = f"{'Updated' if existed else 'Downloaded'} database ({', '.join(bits)})"
        if not need_base and not deltas_applied and not photos_extracted:
            msg = "Local database is up to date"
            action = "skipped"
        _log(log, msg)
        return SyncResult(
            ok=True,
            action=action,
            message=msg,
            record_count=n,
            sha256=(remote or {}).get("sha256"),
            bytes_written=bytes_written,
            photos_extracted=photos_extracted,
            deltas_applied=deltas_applied,
        )
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
