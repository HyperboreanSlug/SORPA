#!/usr/bin/env python3
"""Scrub local PII from offenders.db and package DB + referenced mugshots for release.

Outputs under ``releases/``:
  - offenders.db.zip              (SQLite only)
  - offenders.photos.NNN.zip      (mugshots under data/report_pages/*/photos/)
  - MANIFEST.json                 (sha256, sizes, photo part list)

Photo zips use path-hash shards (~400 MiB target) under GitHub's ~2 GiB limit.
Unchanged shards keep their SHA so clients re-download only dirty parts.
Only files referenced by ``offenders.photo_path`` are included.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "data" / "offenders.db"
OUT_DIR = ROOT / "releases"
SCRUBBED = OUT_DIR / "offenders_scrubbed.db"
ZIP_PATH = OUT_DIR / "offenders.db.zip"
PHOTO_PREFIX = "offenders.photos."
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

OUT_DIR.mkdir(exist_ok=True)


def scrub_path(val: object) -> object:
    if not val:
        return val
    s = str(val)
    low = s.replace("/", "\\").lower()
    for marker in ("data\\report_pages\\", "data\\"):
        idx = low.find(marker)
        if idx >= 0:
            return s[idx:].replace("/", "\\")
    s2 = USER_PAT.sub("", s)
    s2 = re.sub(r"^[A-Za-z]:\\", "", s2)
    return s2


USER_PAT = re.compile(
    r"([A-Za-z]:[\\/]Users[\\/][^\\/\"']+[\\/])"
    r"|(/home/[^/\"']+/)"
    r"|(/Users/[^/\"']+/)",
    re.I,
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm_rel(p: str) -> str:
    return (p or "").strip().replace("\\", "/").lstrip("./")


def collect_referenced_photos(db_path: Path) -> list[Path]:
    """Return absolute Paths for existing photo files referenced by the DB."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT DISTINCT photo_path FROM offenders "
            "WHERE photo_path IS NOT NULL AND TRIM(photo_path) != ''"
        ).fetchall()
    finally:
        conn.close()

    found: list[Path] = []
    missing = 0
    seen: set[str] = set()
    for (raw,) in rows:
        rel = _norm_rel(str(raw or ""))
        if not rel or rel in seen:
            continue
        seen.add(rel)
        # Only ship dedicated mugshot downloads, not HTML chrome assets
        parts = rel.lower().split("/")
        if "photos" not in parts:
            continue
        if any(p.endswith("_assets") or p == "assets" for p in parts):
            continue
        fp = (ROOT / rel).resolve()
        try:
            fp.relative_to(ROOT)
        except ValueError:
            missing += 1
            continue
        if not fp.is_file() or fp.suffix.lower() not in IMAGE_EXTS:
            missing += 1
            continue
        found.append(fp)
    print(f"photos: referenced_ok={len(found)} skipped_missing_or_non_photos={missing}")
    return sorted(found, key=lambda p: str(p).lower())


def write_photo_parts(files: list[Path], *, force_rebuild: bool = False) -> list[dict]:
    """Zip mugshots into stable path-hash photo parts (~400 MiB target)."""
    from scraper.db_publish_photos import write_photo_parts as _write

    return _write(ROOT, files, out_dir=OUT_DIR, force_rebuild=force_rebuild)


def scrub_database() -> int:
    if SCRUBBED.exists():
        SCRUBBED.unlink()
    shutil.copy2(SRC, SCRUBBED)

    conn = sqlite3.connect(str(SCRUBBED))
    conn.execute("PRAGMA journal_mode=DELETE")

    for t in ("nsopw_query_log",):
        try:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        except Exception:
            pass

    cur = conn.execute(
        "SELECT id, photo_path, report_html_path, sources_json, raw_data_json FROM offenders"
    )
    updates = []
    n_scrub = 0
    while True:
        batch = cur.fetchmany(5000)
        if not batch:
            break
        for rid, photo, html, sources, raw in batch:
            new_photo = scrub_path(photo) if photo else photo
            new_html = scrub_path(html) if html else html
            new_sources, new_raw = sources, raw
            changed = (new_photo != photo) or (new_html != html)
            if sources and re.search(r"Users|/[Uu]sers/|C:\\\\|C:/|/home/", sources):
                try:
                    data = json.loads(sources)
                except Exception:
                    data = None
                if isinstance(data, list):
                    for src in data:
                        if not isinstance(src, dict):
                            continue
                        for k in ("html_path", "photo_path", "origin"):
                            if isinstance(src.get(k), str) and not str(src[k]).startswith(
                                "http"
                            ):
                                sf = scrub_path(src[k])
                                if sf != src[k]:
                                    src[k] = sf
                                    changed = True
                        fields = src.get("fields")
                        if isinstance(fields, dict):
                            for fk in ("photo_path", "report_html_path"):
                                if isinstance(fields.get(fk), str):
                                    sf = scrub_path(fields[fk])
                                    if sf != fields[fk]:
                                        fields[fk] = sf
                                        changed = True
                    new_sources = json.dumps(data, ensure_ascii=False)
                else:
                    ns = USER_PAT.sub("", sources)
                    if ns != sources:
                        new_sources = ns
                        changed = True
            if raw and re.search(r"Users|C:\\\\|/home/", raw or ""):
                try:
                    rdata = json.loads(raw)

                    def walk(o):
                        if isinstance(o, dict):
                            return {k: walk(v) for k, v in o.items()}
                        if isinstance(o, list):
                            return [walk(v) for v in o]
                        if (
                            isinstance(o, str)
                            and re.search(r"Users|C:\\\\|/home/", o)
                            and not o.startswith("http")
                        ):
                            return scrub_path(o)
                        return o

                    nr = json.dumps(walk(rdata), ensure_ascii=False)[:50000]
                    if nr != raw:
                        new_raw = nr
                        changed = True
                except Exception:
                    nr = USER_PAT.sub("", raw)
                    if nr != raw:
                        new_raw = nr
                        changed = True
            if changed:
                n_scrub += 1
                updates.append((new_photo, new_html, new_sources, new_raw, rid))

    print("rows_scrubbed", n_scrub)
    conn.executemany(
        "UPDATE offenders SET photo_path=?, report_html_path=?, sources_json=?, "
        "raw_data_json=? WHERE id=?",
        updates,
    )
    conn.commit()
    print("vacuum...")
    conn.execute("VACUUM")
    conn.close()
    return n_scrub


def main() -> int:
    import argparse
    import os

    ap = argparse.ArgumentParser(description="Scrub DB + package base/delta for release")
    ap.add_argument(
        "--full-base",
        action="store_true",
        help="Force a full offenders.db.zip base (clears delta chain)",
    )
    ap.add_argument(
        "--skip-photos",
        action="store_true",
        help="Reuse previous photo parts in MANIFEST (fast delta publishes)",
    )
    ap.add_argument(
        "--force-photo-rebuild",
        action="store_true",
        help="Rebuild every photo shard even if fingerprints match",
    )
    args = ap.parse_args()

    if not SRC.is_file():
        print(f"Missing {SRC}")
        return 1

    os.chdir(ROOT)

    print("scrubbing database…")
    scrub_database()

    c2 = sqlite3.connect(str(SCRUBBED))
    leaks = c2.execute(
        "SELECT COUNT(*) FROM offenders WHERE "
        "photo_path LIKE '%\\\\Users\\\\%' OR report_html_path LIKE '%\\\\Users\\\\%' OR "
        "photo_path LIKE '%/Users/%' OR report_html_path LIKE '%/Users/%' OR "
        "sources_json LIKE '%\\\\Users\\\\%' OR sources_json LIKE '%/Users/%' OR "
        "raw_data_json LIKE '%\\\\Users\\\\%' OR raw_data_json LIKE '%/Users/%'"
    ).fetchone()[0]
    c2.close()
    if leaks:
        print(f"WARN: path leak rows after scrub: {leaks}")

    photo_parts: list = []
    if not args.skip_photos:
        print("packing referenced mugshots…")
        photo_files = collect_referenced_photos(SCRUBBED)
        photo_parts = write_photo_parts(
            photo_files, force_rebuild=bool(args.force_photo_rebuild)
        )
    else:
        print("skipping photo pack (reuse MANIFEST photo list)")

    from scraper.db_publish_package import package_db_release

    print("packaging base or delta…")
    result = package_db_release(
        ROOT,
        SCRUBBED,
        photo_parts=photo_parts or None,
        full_base=bool(args.full_base),
    )
    mode = result.get("mode")
    print(
        f"package mode={mode} ops={result.get('ops')} "
        f"records={result.get('record_count')} "
        f"msg={result.get('message') or ''}"
    )
    if mode == "noop":
        print("Nothing to package (DB unchanged since last publish index).")
        return 0
    man = result.get("manifest") or {}
    print(
        f"base_sha={str(man.get('sha256') or '')[:16]}… "
        f"deltas={len(man.get('deltas') or [])} "
        f"photo_parts={man.get('photo_part_count')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
