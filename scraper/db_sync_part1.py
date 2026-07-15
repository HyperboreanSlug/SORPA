from __future__ import annotations

import json
import os
import sqlite3
import hashlib

from scraper.db_sync_common import *  # noqa: F401,F403

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
            if name.endswith(".zip") and (
                name.startswith(PHOTO_ASSET_PREFIX)
                or name.startswith(DELTA_ASSET_PREFIX)
            ):
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


