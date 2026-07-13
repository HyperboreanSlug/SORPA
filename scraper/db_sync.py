"""Download / update the public offenders SQLite archive from GitHub.

The archive is published as Release assets:
  - ``offenders.db.zip`` (+ ``MANIFEST.json``)
  - ``offenders.photos.NNN.zip`` (mugshots under ``data/report_pages/*/photos/``)

Paths inside the DB are project-relative; photos extract next to the DB's
``data/`` folder so ``photo_path`` resolves for Browse / detail views.

Default source: ``HyperboreanSlug/sor-public-archiver`` release tag
``database-latest``.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Public GitHub repository that hosts the database release asset (not a person).
DEFAULT_GITHUB_REPO = "HyperboreanSlug/sor-public-archiver"
DEFAULT_RELEASE_TAG = "database-latest"
DEFAULT_ASSET_NAME = "offenders.db.zip"
DEFAULT_MANIFEST_NAME = "MANIFEST.json"
DEFAULT_DB_REL = Path("data/offenders.db")
USER_AGENT = "SOR-Public-Archiver-DB-Sync/1.0"
PHOTO_ASSET_PREFIX = "offenders.photos."


@dataclass
class SyncResult:
    ok: bool
    action: str  # skipped | downloaded | updated | error
    message: str
    record_count: Optional[int] = None
    sha256: Optional[str] = None
    bytes_written: int = 0
    photos_extracted: int = 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(log: Optional[Callable[[str], None]], msg: str) -> None:
    if log:
        try:
            log(msg)
        except Exception:
            pass


def _http_get(url: str, timeout: float = 120.0) -> bytes:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_download_file(
    url: str,
    dest: Path,
    *,
    timeout: float = 600.0,
    expected_sha256: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
    label: str = "asset",
) -> str:
    """Stream *url* to *dest*; return lowercase hex SHA-256 of the file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    if tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            pass
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"})
    h = hashlib.sha256()
    written = 0
    last_log = 0
    with urlopen(req, timeout=timeout) as resp:
        total = None
        try:
            total = int(resp.headers.get("Content-Length") or 0) or None
        except Exception:
            total = None
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                written += len(chunk)
                if written - last_log >= 50 * 1024 * 1024:
                    last_log = written
                    if total:
                        pct = 100.0 * written / total
                        _log(
                            log,
                            f"  {label}: {written / (1024 ** 2):.0f}/"
                            f"{total / (1024 ** 2):.0f} MB ({pct:.0f}%)",
                        )
                    else:
                        _log(log, f"  {label}: {written / (1024 ** 2):.0f} MB…")
    digest = h.hexdigest()
    if expected_sha256 and digest.lower() != str(expected_sha256).lower():
        try:
            tmp.unlink()
        except OSError:
            pass
        raise ValueError(
            f"SHA-256 mismatch for {label} "
            f"(got {digest[:16]}… expected {str(expected_sha256)[:16]}…)"
        )
    os.replace(str(tmp), str(dest))
    return digest


def _http_get_json(url: str, timeout: float = 60.0) -> Any:
    req = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def project_root_for_db(db_path: Path) -> Path:
    """Directory that should contain ``data/report_pages/...`` for photo_path."""
    db_path = Path(db_path).resolve()
    if db_path.parent.name.lower() == "data":
        return db_path.parent.parent
    return Path.cwd()


def resolve_release_urls(
    repo: str = DEFAULT_GITHUB_REPO,
    tag: str = DEFAULT_RELEASE_TAG,
    asset_name: str = DEFAULT_ASSET_NAME,
    manifest_name: str = DEFAULT_MANIFEST_NAME,
) -> Tuple[str, str, Dict[str, str]]:
    """
    Return (zip_url, manifest_url, extra_asset_urls_by_name).

    Prefers the GitHub Releases API; falls back to the stable download URL pattern.
    """
    repo = (repo or DEFAULT_GITHUB_REPO).strip().strip("/")
    tag = (tag or DEFAULT_RELEASE_TAG).strip()
    asset_name = (asset_name or DEFAULT_ASSET_NAME).strip()
    manifest_name = (manifest_name or DEFAULT_MANIFEST_NAME).strip()
    extra: Dict[str, str] = {}

    api = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    try:
        data = _http_get_json(api)
        assets = data.get("assets") or []
        by_name = {a.get("name"): a for a in assets if isinstance(a, dict)}
        zip_a = by_name.get(asset_name) or {}
        man_a = by_name.get(manifest_name) or {}
        zip_url = zip_a.get("browser_download_url") or ""
        man_url = man_a.get("browser_download_url") or ""
        for name, meta in by_name.items():
            if not isinstance(name, str):
                continue
            if name.startswith(PHOTO_ASSET_PREFIX) and name.endswith(".zip"):
                url = meta.get("browser_download_url") or ""
                if url:
                    extra[name] = url
        if zip_url:
            return zip_url, man_url, extra
    except Exception:
        pass

    base = f"https://github.com/{repo}/releases/download/{tag}"
    return f"{base}/{asset_name}", f"{base}/{manifest_name}", extra


def fetch_remote_manifest(
    repo: str = DEFAULT_GITHUB_REPO,
    tag: str = DEFAULT_RELEASE_TAG,
) -> Optional[Dict[str, Any]]:
    _, man_url, _ = resolve_release_urls(repo=repo, tag=tag)
    if not man_url:
        return None
    try:
        raw = _http_get(man_url, timeout=60.0)
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def local_db_fingerprint(db_path: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "exists": db_path.is_file(),
        "size_bytes": 0,
        "record_count": None,
        "sha256": None,
    }
    if not db_path.is_file():
        return out
    out["size_bytes"] = int(db_path.stat().st_size)
    try:
        out["sha256"] = sha256_file(db_path)
    except Exception:
        pass
    try:
        conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            out["record_count"] = int(
                conn.execute("SELECT COUNT(*) FROM offenders").fetchone()[0]
            )
        finally:
            conn.close()
    except Exception:
        pass
    return out


def _photo_fingerprint(remote: Optional[Dict[str, Any]]) -> str:
    """Stable fingerprint of photo parts listed in MANIFEST."""
    if not remote:
        return ""
    parts = remote.get("photos") or []
    if not isinstance(parts, list):
        return ""
    bits: List[str] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        bits.append(f"{p.get('name')}:{p.get('sha256')}")
    return "|".join(bits)


def _photos_present_locally(db_path: Path, *, sample: int = 40) -> bool:
    """True when a sample of photo_path values resolve on disk."""
    if not db_path.is_file():
        return False
    root = project_root_for_db(db_path)
    try:
        conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT photo_path FROM offenders "
                "WHERE photo_path IS NOT NULL AND TRIM(photo_path) != '' "
                "LIMIT ?",
                (max(sample * 3, sample),),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return False
    checked = 0
    found = 0
    for (raw,) in rows:
        rel = (raw or "").strip().replace("\\", "/")
        if not rel or "photos" not in rel.lower().split("/"):
            continue
        checked += 1
        if (root / rel).is_file() or Path(rel).is_file():
            found += 1
        if checked >= sample:
            break
    if checked == 0:
        return True  # DB has no photo paths to check
    return found >= max(1, checked // 2)


def needs_update(
    db_path: Path,
    remote: Optional[Dict[str, Any]],
) -> bool:
    """True if local DB is missing, outdated, or mugshots are not installed."""
    if not remote:
        return not db_path.is_file()
    if not db_path.is_file() or db_path.stat().st_size < 1000:
        return True
    stamp = db_path.with_suffix(db_path.suffix + ".sync.json")
    remote_photos = _photo_fingerprint(remote)
    if stamp.is_file():
        try:
            local = json.loads(stamp.read_text(encoding="utf-8"))
            if local.get("remote_sha256") and remote.get("sha256"):
                db_stale = local.get("remote_sha256") != remote.get("sha256")
                photos_stale = bool(remote_photos) and (
                    local.get("remote_photos_fingerprint") != remote_photos
                )
                if db_stale or photos_stale:
                    return True
                if remote.get("includes_photos") and not _photos_present_locally(db_path):
                    return True
                return False
        except Exception:
            pass
    try:
        local_fp = local_db_fingerprint(db_path)
        rc_local = local_fp.get("record_count")
        rc_remote = remote.get("record_count")
        if rc_local is not None and rc_remote is not None:
            if int(rc_remote) > int(rc_local):
                return True
    except Exception:
        pass
    if remote.get("includes_photos") and not _photos_present_locally(db_path):
        return True
    return False


def _safe_extract_member(zf: zipfile.ZipFile, member: str, dest_root: Path) -> Optional[Path]:
    """Extract one zip member under *dest_root*, blocking path traversal."""
    name = member.replace("\\", "/")
    if not name or name.endswith("/"):
        return None
    # Zip slip guard
    target = (dest_root / name).resolve()
    try:
        target.relative_to(dest_root.resolve())
    except ValueError:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(member) as src, open(target, "wb") as out:
        shutil.copyfileobj(src, out, length=1024 * 1024)
    return target


def _extract_photo_zip(
    zip_path: Path,
    dest_root: Path,
    *,
    log: Optional[Callable[[str], None]] = None,
) -> int:
    n = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = [m for m in zf.namelist() if m and not m.endswith("/")]
        _log(log, f"  Extracting {zip_path.name} ({len(names):,} files)…")
        for i, member in enumerate(names, 1):
            if _safe_extract_member(zf, member, dest_root) is not None:
                n += 1
            if i % 5000 == 0:
                _log(log, f"    … {i:,}/{len(names):,}")
    return n


def download_and_install_db(
    dest: Optional[Path] = None,
    *,
    repo: str = DEFAULT_GITHUB_REPO,
    tag: str = DEFAULT_RELEASE_TAG,
    force: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> SyncResult:
    """
    Download ``offenders.db.zip`` (+ mugshot parts) from GitHub Releases into *dest*.

    Replaces existing DB atomically (write temp → replace). Extracts photos under
    the project root beside ``data/``. Writes a ``.sync.json`` stamp beside the DB.
    """
    dest = Path(dest) if dest else DEFAULT_DB_REL
    dest = dest if dest.is_absolute() else (Path.cwd() / dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    project_root = project_root_for_db(dest)

    _log(log, f"Checking remote database ({repo} @ {tag})…")
    remote = fetch_remote_manifest(repo=repo, tag=tag)
    if remote:
        photo_n = int(remote.get("photo_file_count") or 0)
        photo_sz = int(remote.get("photo_size_bytes") or 0)
        _log(
            log,
            f"Remote: records={remote.get('record_count')} "
            f"sha={str(remote.get('sha256') or '')[:12]}… "
            f"db={remote.get('size_bytes')} "
            f"photos={photo_n} ({photo_sz / (1024 ** 3):.2f} GiB)",
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

    zip_url, _, extra_urls = resolve_release_urls(repo=repo, tag=tag)
    # Prefer MANIFEST photo list (ordered) for URLs / hashes
    photo_specs: List[Dict[str, Any]] = []
    if remote and isinstance(remote.get("photos"), list):
        for p in remote["photos"]:
            if isinstance(p, dict) and p.get("name"):
                photo_specs.append(p)
    if not photo_specs:
        for name in sorted(extra_urls):
            photo_specs.append({"name": name, "sha256": None})

    # Fill download URLs
    base = f"https://github.com/{repo}/releases/download/{tag}"
    for spec in photo_specs:
        name = str(spec["name"])
        if name not in extra_urls:
            extra_urls[name] = f"{base}/{name}"

    existed = dest.is_file() and dest.stat().st_size > 1000
    tmp_dir = Path(tempfile.mkdtemp(prefix="sor_db_sync_"))
    photos_extracted = 0
    bytes_written = 0
    try:
        zip_path = tmp_dir / "offenders.db.zip"
        _log(log, f"Downloading {zip_url} …")
        try:
            _http_download_file(
                zip_url,
                zip_path,
                timeout=600.0,
                expected_sha256=(remote or {}).get("sha256"),
                log=log,
                label="database zip",
            )
        except HTTPError as e:
            return SyncResult(False, "error", f"HTTP {e.code} downloading database: {e.reason}")
        except URLError as e:
            return SyncResult(False, "error", f"Network error: {e.reason}")
        except ValueError as e:
            return SyncResult(False, "error", str(e))
        except Exception as e:
            return SyncResult(False, "error", f"Download failed: {e}")

        bytes_written += zip_path.stat().st_size
        extract_dir = tmp_dir / "out"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            member = None
            for n in names:
                if n.replace("\\", "/").endswith("offenders.db") and not n.endswith("/"):
                    member = n
                    break
            if not member:
                return SyncResult(False, "error", "Zip does not contain offenders.db")
            zf.extract(member, extract_dir)
            extracted = extract_dir / member
            if not extracted.is_file():
                candidates = list(extract_dir.rglob("offenders.db"))
                if not candidates:
                    return SyncResult(False, "error", "Failed to extract offenders.db")
                extracted = candidates[0]

        try:
            conn = sqlite3.connect(str(extracted))
            n = int(conn.execute("SELECT COUNT(*) FROM offenders").fetchone()[0])
            conn.close()
        except Exception as e:
            return SyncResult(False, "error", f"Extracted DB failed integrity check: {e}")

        # Download + extract mugshot parts into project root
        for spec in photo_specs:
            name = str(spec["name"])
            url = extra_urls.get(name) or f"{base}/{name}"
            part_path = tmp_dir / name
            _log(log, f"Downloading mugshots {name} …")
            try:
                _http_download_file(
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
                return SyncResult(
                    False, "error", f"HTTP {e.code} downloading {name}: {e.reason}"
                )
            except Exception as e:
                return SyncResult(False, "error", f"Photo download failed ({name}): {e}")
            bytes_written += part_path.stat().st_size
            try:
                photos_extracted += _extract_photo_zip(part_path, project_root, log=log)
            except Exception as e:
                return SyncResult(False, "error", f"Photo extract failed ({name}): {e}")
            try:
                part_path.unlink()
            except OSError:
                pass

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

        stamp = {
            "remote_sha256": (remote or {}).get("sha256"),
            "remote_record_count": (remote or {}).get("record_count") or n,
            "remote_photos_fingerprint": _photo_fingerprint(remote),
            "photos_extracted": photos_extracted,
            "synced_at_utc": _utc_now(),
            "repo": repo,
            "tag": tag,
            "local_record_count": n,
            "project_root": str(project_root),
        }
        stamp_path = dest.with_suffix(dest.suffix + ".sync.json")
        stamp_path.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")

        action = "updated" if existed else "downloaded"
        photo_bit = (
            f", {photos_extracted:,} mugshots"
            if photos_extracted
            else (", photos unchanged" if photo_specs else "")
        )
        msg = (
            f"{'Updated' if existed else 'Downloaded'} database "
            f"({n:,} records{photo_bit})"
        )
        _log(log, msg)
        return SyncResult(
            ok=True,
            action=action,
            message=msg,
            record_count=n,
            sha256=(remote or {}).get("sha256"),
            bytes_written=bytes_written,
            photos_extracted=photos_extracted,
        )
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


def should_prompt_first_run(settings: Dict[str, Any], db_path: Path) -> bool:
    """True when user has never chosen, and no usable local DB is present."""
    if settings.get("db_sync_prompted"):
        return False
    if settings.get("db_sync_enabled"):
        return False
    if db_path.is_file() and db_path.stat().st_size > 10_000:
        return True
    return True
