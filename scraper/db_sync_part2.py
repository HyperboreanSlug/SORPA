from __future__ import annotations

import json
import sqlite3
import shutil

from scraper.db_sync_common import *  # noqa: F401,F403


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
        return True
    return found >= max(1, checked // 2)


def _load_stamp(db_path: Path) -> Dict[str, Any]:
    stamp = db_path.with_suffix(db_path.suffix + ".sync.json")
    if not stamp.is_file():
        return {}
    try:
        data = json.loads(stamp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _remote_delta_names(remote: Optional[Dict[str, Any]]) -> List[str]:
    if not remote or not isinstance(remote.get("deltas"), list):
        return []
    names: List[str] = []
    for d in remote["deltas"]:
        if isinstance(d, dict) and d.get("name"):
            names.append(str(d["name"]))
    return names


def pending_delta_specs(
    remote: Optional[Dict[str, Any]], local_stamp: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Delta entries from remote not yet applied locally (same base)."""
    if not remote or not isinstance(remote.get("deltas"), list):
        return []
    base_ok = True
    remote_sha = remote.get("sha256")
    if local_stamp.get("remote_sha256") and remote_sha:
        base_ok = local_stamp.get("remote_sha256") == remote_sha
    if local_stamp.get("base_id") and remote.get("base_id"):
        base_ok = base_ok and (local_stamp.get("base_id") == remote.get("base_id"))
    if not base_ok:
        return []
    applied = set(local_stamp.get("applied_deltas") or [])
    out: List[Dict[str, Any]] = []
    for d in remote["deltas"]:
        if not isinstance(d, dict) or not d.get("name"):
            continue
        if str(d["name"]) not in applied:
            out.append(d)
    return out


def photo_parts_needing_download(
    remote: Optional[Dict[str, Any]], local_stamp: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Return photo part specs whose sha differs from local stamp cache."""
    if not remote or not isinstance(remote.get("photos"), list):
        return []
    local_parts = local_stamp.get("local_photo_parts") or {}
    if not isinstance(local_parts, dict):
        local_parts = {}
    need: List[Dict[str, Any]] = []
    for p in remote["photos"]:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        name = str(p["name"])
        sha = str(p.get("sha256") or "")
        if sha and local_parts.get(name) == sha:
            continue
        need.append(p)
    return need


def needs_update(
    db_path: Path,
    remote: Optional[Dict[str, Any]],
) -> bool:
    """True if local DB/photos need download or delta apply."""
    if not remote:
        return not db_path.is_file()
    if not db_path.is_file() or db_path.stat().st_size < 1000:
        return True
    stamp = _load_stamp(db_path)
    remote_sha = remote.get("sha256")
    if stamp.get("remote_sha256") and remote_sha:
        if stamp.get("remote_sha256") != remote_sha:
            return True
        if pending_delta_specs(remote, stamp):
            return True
        if photo_parts_needing_download(remote, stamp):
            return True
        if remote.get("includes_photos") and not _photos_present_locally(db_path):
            return True
        return False
    # No stamp: compare counts; still prefer update if remote has deltas/base
    try:
        local_fp = local_db_fingerprint(db_path)
        rc_local = local_fp.get("record_count")
        rc_remote = remote.get("record_count")
        if rc_local is not None and rc_remote is not None:
            if int(rc_remote) != int(rc_local):
                return True
    except Exception:
        pass
    if remote.get("deltas"):
        return True
    if remote.get("includes_photos") and not _photos_present_locally(db_path):
        return True
    return False


def _safe_extract_member(zf: zipfile.ZipFile, member: str, dest_root: Path) -> Optional[Path]:
    """Extract one zip member under *dest_root*, blocking path traversal."""
    name = member.replace("\\", "/")
    if not name or name.endswith("/"):
        return None
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
