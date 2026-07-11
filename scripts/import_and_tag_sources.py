#!/usr/bin/env python3
"""
One-shot: backfill sources_json + import bulk SOR CSVs with multi-source merge.

Run from repo root (or any cwd — paths resolve relative to this file's parent):

    python scripts/import_and_tag_sources.py
    python scripts/import_and_tag_sources.py --skip-backfill
    python scripts/import_and_tag_sources.py --only-backfill
    python scripts/import_and_tag_sources.py --csv data/downloads/fl_sor.csv --state FL

Progress prints to stdout every 5k merge rows (see database.csv_io).
Does not need an interactive agent session.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--database",
        default=str(ROOT / "data" / "offenders.db"),
        help="SQLite path (default: data/offenders.db)",
    )
    ap.add_argument(
        "--downloads",
        default=str(ROOT / "data" / "downloads"),
        help="Directory of SOR CSVs (default: data/downloads)",
    )
    ap.add_argument(
        "--csv",
        action="append",
        default=None,
        help="Import only this CSV (repeatable). Default: fl_sor.csv + sor.csv",
    )
    ap.add_argument("--state", type=str, default=None, help="Force state for --csv")
    ap.add_argument("--skip-backfill", action="store_true")
    ap.add_argument("--only-backfill", action="store_true")
    ap.add_argument(
        "--no-merge",
        action="store_true",
        help="Insert only; do not merge into existing same-person rows",
    )
    args = ap.parse_args()

    from scraper.database import Database

    db_path = Path(args.database)
    print(f"DB: {db_path}", flush=True)
    db = Database(str(db_path))
    try:
        total = db._conn.execute("SELECT COUNT(*) FROM offenders").fetchone()[0]
        print(f"Rows before: {total}", flush=True)

        if not args.skip_backfill:
            print("=== backfill_sources ===", flush=True)
            t0 = time.time()
            res = db.backfill_sources(only_missing=True, log=print)
            print(f"Backfill done in {time.time() - t0:.1f}s: {res}", flush=True)

        if args.only_backfill:
            return 0

        downloads = Path(args.downloads)
        if args.csv:
            jobs = [(Path(p), args.state) for p in args.csv]
        else:
            jobs = [
                (downloads / "fl_sor.csv", "FL"),
                (downloads / "sor.csv", "GA"),
            ]

        merge = not args.no_merge
        for path, st in jobs:
            if not path.is_file():
                print(f"SKIP missing: {path}", flush=True)
                continue
            print(f"=== import {path.name} state={st or 'auto'} merge={merge} ===", flush=True)
            t0 = time.time()
            r = db.import_csv(
                str(path),
                state=st,
                merge_sources=merge,
            )
            print(
                f"Done {path.name} in {time.time() - t0:.1f}s → {json.dumps(r)}",
                flush=True,
            )

        total2 = db._conn.execute("SELECT COUNT(*) FROM offenders").fetchone()[0]
        tagged = db._conn.execute(
            "SELECT COUNT(*) FROM offenders "
            "WHERE sources_json IS NOT NULL AND length(sources_json) > 5"
        ).fetchone()[0]
        print(f"Rows after: {total2}  tagged: {tagged}/{total2}", flush=True)

        # Spot-check Niraj Patel if present
        row = db._conn.execute(
            "SELECT id, race, external_id, substr(sources_json,1,200) "
            "FROM offenders WHERE UPPER(first_name)='NIRAJ' AND UPPER(last_name)='PATEL' "
            "LIMIT 1"
        ).fetchone()
        if row:
            print(f"Spot-check NIRAJ PATEL id={row[0]} race={row[1]!r} ext={row[2]!r}", flush=True)
            print(f"  sources_json[:200]={row[3]!r}", flush=True)
    finally:
        db.close()
    print("ALL DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
