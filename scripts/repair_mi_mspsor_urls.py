"""
Rewrite Michigan mspsor.com source_url / external_id to path-style details URLs.

Legacy form:
  https://mspsor.com/Home/OffenderDetails?id={uuid}

Canonical (live site JS):
  https://mspsor.com/Home/OffenderDetails/{uuid}

Also normalizes lowercased paths from older normalize_identity_url runs.

Usage:
  python scripts/repair_mi_mspsor_urls.py
  python scripts/repair_mi_mspsor_urls.py --dry-run
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.public_links import (  # noqa: E402
    normalize_mspsor_url,
    split_source_urls,
)


def _rewrite_blob(raw: str) -> str:
    text = (raw or "").strip()
    if not text or "mspsor" not in text.lower():
        return text
    parts = split_source_urls(text)
    if not parts:
        # single non-split URL
        if "mspsor" in text.lower():
            return normalize_mspsor_url(text)
        return text
    out: list[str] = []
    changed = False
    for p in parts:
        if "mspsor" in p.lower():
            n = normalize_mspsor_url(p)
            if n != p:
                changed = True
            out.append(n)
        else:
            out.append(p)
    if not changed and len(parts) == 1:
        return out[0] if out else text
    return " | ".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--database",
        default=str(ROOT / "data" / "offenders.db"),
        help="Path to offenders.db",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db_path = Path(args.database)
    if not db_path.is_file():
        print(f"Database not found: {db_path}")
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, source_url, external_id, source_state, state
        FROM offenders
        WHERE IFNULL(source_url,'') LIKE '%mspsor%'
           OR IFNULL(external_id,'') LIKE '%mspsor%'
           OR UPPER(IFNULL(source_state,'')) = 'MI'
           OR UPPER(IFNULL(state,'')) = 'MI'
        """
    ).fetchall()

    updated = 0
    for r in rows:
        rid = int(r["id"])
        su = r["source_url"] or ""
        ext = r["external_id"] or ""
        new_su = _rewrite_blob(su) if "mspsor" in su.lower() else su
        new_ext = _rewrite_blob(ext) if "mspsor" in ext.lower() else ext
        # Prefer UUID-only external_id when we can extract it from the URL
        if new_su and "mspsor.com/Home/OffenderDetails/" in new_su:
            from scraper.public_links import extract_mspsor_offender_id

            oid = extract_mspsor_offender_id(new_su)
            if oid and (not new_ext or "mspsor" in new_ext.lower() or new_ext == su):
                new_ext = oid
        if new_su == su and new_ext == ext:
            continue
        updated += 1
        if args.dry_run:
            if updated <= 5:
                print(f"  would fix id={rid}")
                print(f"    url: {su[:90]} → {new_su[:90]}")
                print(f"    ext: {ext[:90]} → {new_ext[:90]}")
            continue
        conn.execute(
            "UPDATE offenders SET source_url = ?, external_id = ? WHERE id = ?",
            (new_su or None, new_ext or None, rid),
        )

    if not args.dry_run:
        conn.commit()
    conn.close()
    mode = "would update" if args.dry_run else "updated"
    print(f"{mode} {updated} rows in {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
