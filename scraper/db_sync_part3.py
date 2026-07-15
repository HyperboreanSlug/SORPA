from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from scraper.db_sync_common import (
    DEFAULT_DB_REL,
    DEFAULT_GITHUB_REPO,
    DEFAULT_RELEASE_TAG,
    SyncResult,
)
from scraper.db_sync_part1 import (
    _log,
    fetch_remote_manifest,
    local_db_fingerprint,
    project_root_for_db,
)
from scraper.db_sync_part2 import needs_update


def download_and_install_db(
    dest: Optional[Path] = None,
    *,
    repo: str = DEFAULT_GITHUB_REPO,
    tag: str = DEFAULT_RELEASE_TAG,
    force: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> SyncResult:
    """
    Sync public DB from GitHub Releases into *dest*.

    Full base zip when local base is missing/stale; otherwise only new
    ``offenders.delta.NNNN.zip`` packs. Photo parts download only when SHA changes.
    """
    from scraper.db_sync_apply import apply_delta_zip
    from scraper.db_sync_part3b import run_db_sync

    dest = Path(dest) if dest else DEFAULT_DB_REL
    dest = dest if dest.is_absolute() else (Path.cwd() / dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    project_root = project_root_for_db(dest)

    _log(log, f"Checking remote database ({repo} @ {tag})…")
    remote = fetch_remote_manifest(repo=repo, tag=tag)
    if remote:
        dn = (
            len(remote.get("deltas") or [])
            if isinstance(remote.get("deltas"), list)
            else 0
        )
        _log(
            log,
            f"Remote: records={remote.get('record_count')} "
            f"sha={str(remote.get('sha256') or '')[:12]}… "
            f"deltas={dn} photos={int(remote.get('photo_file_count') or 0)}",
        )
    else:
        _log(log, "Remote MANIFEST not available — will try asset URL directly")

    if not force and not needs_update(dest, remote):
        fp = local_db_fingerprint(dest)
        return SyncResult(
            ok=True,
            action="skipped",
            message="Local database is up to date",
            record_count=fp.get("record_count"),
            sha256=(remote or {}).get("sha256"),
        )

    return run_db_sync(
        dest,
        remote=remote,
        repo=repo,
        tag=tag,
        project_root=project_root,
        log=log,
        apply_delta_zip=apply_delta_zip,
    )
