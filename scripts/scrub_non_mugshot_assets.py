#!/usr/bin/env python3
"""Delete non-mugshot site chrome (1×1, seals, banners, silhouettes) and fix DB.

- Clears offenders.photo_path when it points at a non-mugshot file
- Tries to re-point at a better sibling mugshot under photos/ or *_assets/
- Deletes non-essential image files under data/report_pages

Safe: only removes files classified by photo_quality.non_mugshot_reason.
"""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scraper.mugshot_ethnicity.photo_quality import (  # noqa: E402
    clear_placeholder_cache,
    is_non_mugshot,
    non_mugshot_reason,
    url_has_empty_image_id,
)

DB = ROOT / "data" / "offenders.db"
PAGES = ROOT / "data" / "report_pages"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def resolve(raw: str) -> Path | None:
    s = (raw or "").strip()
    if not s:
        return None
    for p in (Path(s), ROOT / s, ROOT / s.replace("\\", "/")):
        try:
            if p.is_file():
                return p.resolve()
        except OSError:
            continue
    return None


def best_sibling(bad: Path) -> Path | None:
    """Prefer a real mugshot next to a chrome file — never from a shared photos/ pool.

    Only consider:
    - same stem, different extension (rare)
    - other files in the same *_assets/ folder for one HTML page
    Never pick another file from …/photos/ (those are per-offender downloads
    and reusing them attaches the wrong face).
    """
    parent = bad.parent
    parts_l = [x.lower() for x in bad.parts]
    in_shared_photos = "photos" in parts_l and not any(
        x.endswith("_assets") or x == "assets" for x in parts_l
    )
    if in_shared_photos:
        # Same stem, other extension only
        for cand in parent.glob(bad.stem + ".*"):
            if cand.resolve() == bad.resolve():
                continue
            if cand.suffix.lower() not in IMAGE_EXTS:
                continue
            if is_non_mugshot(cand):
                continue
            if cand.suffix.lower() == ".gif":
                continue
            try:
                if cand.stat().st_size >= 500:
                    return cand
            except OSError:
                continue
        return None

    candidates: list[Path] = []
    if parent.is_dir():
        candidates.extend(parent.iterdir())
    # Also check dedicated photos/ next to assets (same HTML page folder)
    if parent.name.endswith("_assets"):
        photos = parent.parent / "photos"
        # Do NOT scan whole photos/ — only same-stem if present
        if photos.is_dir():
            for cand in photos.glob(bad.stem + ".*"):
                candidates.append(cand)
    best: tuple[int, Path] | None = None
    for cand in candidates:
        if not cand.is_file() or cand.suffix.lower() not in IMAGE_EXTS:
            continue
        if cand.resolve() == bad.resolve():
            continue
        if is_non_mugshot(cand):
            continue
        if cand.suffix.lower() == ".gif":
            continue
        try:
            sz = cand.stat().st_size
        except OSError:
            continue
        if sz < 500:
            continue
        score = sz
        if cand.suffix.lower() in (".jpg", ".jpeg"):
            score += 50_000
        if best is None or score > best[0]:
            best = (score, cand)
    return best[1] if best else None


def rel_path(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ROOT)).replace("/", "\\")
    except ValueError:
        try:
            return str(p.resolve().relative_to(Path.cwd())).replace("/", "\\")
        except ValueError:
            return str(p)


def main() -> int:
    clear_placeholder_cache()
    dry = "--dry-run" in sys.argv
    print(f"ROOT={ROOT}  dry_run={dry}")

    # --- DB photo_path cleanup ---
    cleared = 0
    fixed = 0
    kept = 0
    cleared_urls = 0
    if DB.is_file():
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, photo_path, photo_url FROM offenders "
            "WHERE (photo_path IS NOT NULL AND TRIM(photo_path) != '') "
            "   OR (photo_url IS NOT NULL AND TRIM(photo_url) != '')"
        ).fetchall()
        updates: list[tuple[str | None, str | None, int]] = []
        cleared_urls = 0
        for r in rows:
            oid = int(r["id"])
            raw = (r["photo_path"] or "").strip()
            url = (r["photo_url"] or "").strip()
            path = resolve(raw) if raw else None
            new_path = raw or None
            new_url = url or None
            changed = False

            if path is not None and is_non_mugshot(path):
                reason = non_mugshot_reason(path) or "non-mugshot"
                # Never reassign to another file — wrong-face risk is too high
                # (shared photos/ pools previously attached one mugshot to many people).
                # Only keep a same-stem alternate extension in the same folder.
                alt = best_sibling(path)
                same_stem = (
                    alt is not None
                    and alt.stem.lower() == path.stem.lower()
                    and alt.parent.resolve() == path.parent.resolve()
                )
                if same_stem:
                    new_path = rel_path(alt)
                    fixed += 1
                    print(f"  fix id={oid}: {reason} -> {new_path}")
                else:
                    new_path = None
                    cleared += 1
                    print(f"  clear id={oid}: {reason}  was {raw}")
                changed = True
            elif raw and path is None:
                kept += 1
            elif raw:
                kept += 1

            # Drop empty CallImage?imgID= / ImageId=0 so we never re-fetch stubs
            if url and url_has_empty_image_id(url):
                new_url = None
                cleared_urls += 1
                changed = True
                if not (path is not None and is_non_mugshot(path)):
                    print(f"  clear url id={oid}: empty image id")

            if changed:
                updates.append((new_path, new_url, oid))
        if not dry and updates:
            conn.executemany(
                "UPDATE offenders SET photo_path = ?, photo_url = ? WHERE id = ?",
                updates,
            )
            conn.commit()
        conn.close()
    print(f"DB: kept={kept} fixed={fixed} cleared_paths={cleared} cleared_urls={cleared_urls}")

    # --- Delete non-essential image files under report_pages ---
    deleted = 0
    reasons = Counter()
    bytes_freed = 0
    if PAGES.is_dir():
        for p in PAGES.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in IMAGE_EXTS:
                continue
            reason = non_mugshot_reason(p)
            if not reason:
                continue
            reasons[reason] += 1
            try:
                sz = p.stat().st_size
            except OSError:
                sz = 0
            if dry:
                deleted += 1
                bytes_freed += sz
                continue
            try:
                p.unlink()
                deleted += 1
                bytes_freed += sz
            except OSError as e:
                print(f"  FAIL delete {p}: {e}")
    print(f"Files deleted: {deleted}  (~{bytes_freed/1024:.1f} KB)")
    for reason, n in reasons.most_common():
        print(f"  {n:5}  {reason}")

    # Also clear deepface hits that pointed at placeholders (is_hit still 1)
    if DB.is_file() and not dry:
        try:
            conn = sqlite3.connect(DB)
            # Mark scans with missing/cleared photos — leave table; scanner skips non-files
            n = conn.execute(
                "UPDATE deepface_scans SET is_hit = 0, error = "
                "COALESCE(error, 'photo was non-mugshot chrome') "
                "WHERE is_hit = 1 AND ("
                "photo_path IS NULL OR TRIM(photo_path) = '' OR "
                "photo_path LIKE '%563cbdfdb75290%' OR "
                "photo_path LIKE '%.gif'"
                ")"
            ).rowcount
            conn.commit()
            conn.close()
            print(f"deepface_scans: demoted {n} gif/empty hits")
        except Exception as e:
            print(f"deepface_scans update skipped: {e}")

    print("Done." + (" (dry-run — no changes)" if dry else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
