"""Package mugshots into smaller, stable photo-part zips for GitHub Releases.

Why many medium parts beat few ~1.8 GiB ones:
  - Clients already skip parts whose SHA matches the local stamp.
  - Path-hash shards keep a file in the same part as the archive grows, so
    adding photos only dirties a subset of parts.
  - Unchanged shards are left on disk (not rewritten), so their SHA stays stable.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Soft target size per part (clients re-download whole parts when SHA changes).
TARGET_PHOTO_PART_BYTES = 400 * 1024 * 1024  # 400 MiB
# Hard ceiling under GitHub's ~2 GiB release-asset limit.
MAX_PHOTO_PART_BYTES = 1_800_000_000
# Minimum number of path-hash shards (keeps cold-start request count reasonable).
MIN_SHARDS = 8
# Cap shards so cold-start does not open hundreds of HTTP downloads.
MAX_SHARDS = 48

PHOTO_PREFIX = "offenders.photos."
SHARD_STATE_REL = Path("releases") / "publish_state" / "photo_shards.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel_arc(root: Path, fp: Path) -> str:
    return fp.relative_to(root).as_posix()


def _file_sig(fp: Path) -> str:
    """Cheap stability token: size + mtime_ns (good enough for pack skip)."""
    try:
        st = fp.stat()
        return f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        return "0:0"


def choose_shard_count(total_bytes: int) -> int:
    if total_bytes <= 0:
        return MIN_SHARDS
    n = (int(total_bytes) + TARGET_PHOTO_PART_BYTES - 1) // TARGET_PHOTO_PART_BYTES
    return max(MIN_SHARDS, min(MAX_SHARDS, int(n)))


def shard_for_arc(arc: str, n_shards: int) -> int:
    digest = hashlib.sha1(arc.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % max(1, n_shards)


def _load_state(root: Path) -> Dict[str, Any]:
    p = root / SHARD_STATE_REL
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(root: Path, state: Dict[str, Any]) -> None:
    p = root / SHARD_STATE_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _shard_fingerprint(members: List[Tuple[str, str]]) -> str:
    """Hash of sorted (arc, file_sig) pairs for one shard."""
    h = hashlib.sha256()
    for arc, sig in sorted(members):
        h.update(arc.encode("utf-8"))
        h.update(b"\0")
        h.update(sig.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def write_photo_parts(
    root: Path,
    files: List[Path],
    *,
    out_dir: Optional[Path] = None,
    force_rebuild: bool = False,
) -> List[Dict[str, Any]]:
    """
    Zip mugshots into ``offenders.photos.NNN.zip`` path-hash shards.

    Returns manifest photo entries. Skips rewriting shards whose member
    fingerprint matches the last publish state (unless *force_rebuild*).
    """
    root = Path(root)
    out_dir = Path(out_dir) if out_dir else root / "releases"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build (arc, path, sig, size)
    items: List[Tuple[str, Path, str, int]] = []
    total_bytes = 0
    for fp in files:
        try:
            sz = int(fp.stat().st_size)
        except OSError:
            continue
        arc = _rel_arc(root, fp)
        items.append((arc, fp, _file_sig(fp), sz))
        total_bytes += sz

    if not items:
        # Clear old parts
        for old in out_dir.glob(f"{PHOTO_PREFIX}*.zip"):
            try:
                old.unlink()
            except OSError:
                pass
        _save_state(root, {"n_shards": MIN_SHARDS, "shards": {}})
        return []

    n_shards = choose_shard_count(total_bytes)
    prev = _load_state(root)
    prev_shards = prev.get("shards") if isinstance(prev.get("shards"), dict) else {}

    # Bucket members
    buckets: Dict[int, List[Tuple[str, Path, str, int]]] = {i: [] for i in range(n_shards)}
    for arc, fp, sig, sz in items:
        buckets[shard_for_arc(arc, n_shards)].append((arc, fp, sig, sz))

    parts: List[Dict[str, Any]] = []
    new_state_shards: Dict[str, Any] = {}
    active_names: set[str] = set()

    for idx in range(n_shards):
        members = buckets[idx]
        if not members:
            continue
        name = f"{PHOTO_PREFIX}{idx:03d}.zip"
        active_names.add(name)
        part_path = out_dir / name
        fp_list = [(arc, sig) for arc, _fp, sig, _sz in members]
        fingerprint = _shard_fingerprint(fp_list)
        raw_bytes = sum(sz for _a, _p, _s, sz in members)

        prev_meta = prev_shards.get(f"{idx:03d}") if isinstance(prev_shards, dict) else None
        can_reuse = (
            not force_rebuild
            and isinstance(prev_meta, dict)
            and prev_meta.get("fingerprint") == fingerprint
            and part_path.is_file()
            and prev_meta.get("sha256")
        )
        if can_reuse:
            # Guard: refuse reuse if file grew past hard limit (corrupt/partial)
            if part_path.stat().st_size <= MAX_PHOTO_PART_BYTES:
                entry = {
                    "name": name,
                    "sha256": str(prev_meta.get("sha256")),
                    "size_bytes": int(part_path.stat().st_size),
                    "file_count": len(members),
                    "uncompressed_bytes": raw_bytes,
                }
                parts.append(entry)
                new_state_shards[f"{idx:03d}"] = {
                    "fingerprint": fingerprint,
                    "sha256": entry["sha256"],
                    "file_count": len(members),
                    "name": name,
                }
                print(
                    f"  reuse {name}: files={len(members)} "
                    f"zip={entry['size_bytes'] / (1024 * 1024):.1f} MB"
                )
                continue

        # Rebuild this shard only
        if part_path.exists():
            try:
                part_path.unlink()
            except OSError:
                pass
        with zipfile.ZipFile(
            part_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as zf:
            # Spill if a single shard exceeds hard limit (rare with path hash)
            written = 0
            for arc, fp, _sig, sz in sorted(members, key=lambda t: t[0].lower()):
                if written > 0 and written + sz > MAX_PHOTO_PART_BYTES:
                    # Overflow: still write (GitHub hard limit) but warn
                    print(
                        f"  warn: {name} exceeds soft cap while packing {arc}"
                    )
                zf.write(fp, arcname=arc)
                written += sz
        size = part_path.stat().st_size
        if size > MAX_PHOTO_PART_BYTES:
            print(
                f"  warn: {name} is {size / (1024 ** 3):.2f} GiB "
                f"(over GitHub 2 GiB limit) — raise MAX_SHARDS / lower target"
            )
        sha = _sha256_file(part_path)
        entry = {
            "name": name,
            "sha256": sha,
            "size_bytes": size,
            "file_count": len(members),
            "uncompressed_bytes": raw_bytes,
        }
        parts.append(entry)
        new_state_shards[f"{idx:03d}"] = {
            "fingerprint": fingerprint,
            "sha256": sha,
            "file_count": len(members),
            "name": name,
        }
        print(
            f"  wrote {name}: files={len(members)} "
            f"zip={size / (1024 * 1024):.1f} MB"
        )

    # Remove obsolete part files not in this layout
    for old in out_dir.glob(f"{PHOTO_PREFIX}*.zip"):
        if old.name not in active_names:
            try:
                old.unlink()
                print(f"  removed obsolete {old.name}")
            except OSError as e:
                print(f"  warn: could not remove {old.name}: {e}")

    _save_state(
        root,
        {
            "n_shards": n_shards,
            "target_part_bytes": TARGET_PHOTO_PART_BYTES,
            "shards": new_state_shards,
        },
    )
    print(
        f"photos: parts={len(parts)} shards={n_shards} files={len(items)} "
        f"raw={total_bytes / (1024 ** 3):.2f} GiB "
        f"target≈{TARGET_PHOTO_PART_BYTES / (1024 ** 2):.0f} MiB/part"
    )
    # Stable order by name for MANIFEST
    parts.sort(key=lambda p: str(p.get("name") or ""))
    return parts
