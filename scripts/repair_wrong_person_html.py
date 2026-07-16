"""
Strip HTML scrapes / flyer links that fail name identity checks.

FL bulk PERSON_NBR is not flyer personId — many records got wrong flyers
(e.g. Ossiel Zuniga → Jose Triana). This re-validates archived HTML and
clears poisoned report_html / photo / verified race.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.database import Database
from scraper.public_links import extract_fdle_person_id
from scraper.reports.identity_gate import (
    extract_person_name_from_html_path,
    record_name_matches_html,
    strip_wrong_person_html,
)


def _clear_poisoned_fdle_url(rec: dict) -> bool:
    """Drop FDLE flyer URLs when archived HTML name mismatches the record."""
    url = str(rec.get("source_url") or "").strip()
    if "fdle" not in url.lower() or "personid=" not in url.lower():
        return False
    html = str(rec.get("report_html_path") or "").strip()
    if not html:
        return False
    hn = extract_person_name_from_html_path(html)
    if not hn or record_name_matches_html(rec, hn):
        return False
    # Remove only the poisoned FDLE segment(s)
    parts = [p.strip() for p in url.split(" | ") if p.strip()]
    kept = []
    for p in parts:
        if "fdle" in p.lower() and extract_fdle_person_id(p):
            continue
        kept.append(p)
    rec["source_url"] = " | ".join(kept) if kept else None
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = Database(args.db)
    try:
        sql = (
            "SELECT * FROM offenders WHERE "
            "(report_html_path IS NOT NULL AND TRIM(report_html_path) != '') "
            "OR (sources_json LIKE '%report_html%') "
            "OR (source_url LIKE '%fdle%personId%' OR source_url LIKE '%fdle%personid%')"
        )
        if args.limit and args.limit > 0:
            sql += f" LIMIT {int(args.limit)}"
        rows = [dict(r) for r in db._conn.execute(sql).fetchall()]
        fixed = 0
        scanned = 0
        for rec in rows:
            scanned += 1
            before_race = rec.get("race")
            before_html = rec.get("report_html_path")
            before_url = rec.get("source_url")
            ch1 = strip_wrong_person_html(rec)
            ch2 = _clear_poisoned_fdle_url(rec)
            if not (ch1 or ch2):
                continue
            fixed += 1
            print(
                f"  id={rec.get('id')} {rec.get('full_name')}: "
                f"race {before_race!r}→{rec.get('race')!r} "
                f"html {before_html!r}→{rec.get('report_html_path')!r} "
                f"url_cleared={ch2}"
            )
            if args.dry_run:
                continue
            patch = {
                k: rec.get(k)
                for k in (
                    "race",
                    "flags",
                    "sources_json",
                    "report_html_path",
                    "photo_path",
                    "source_url",
                )
            }
            db.update_offender(int(rec["id"]), patch)
        print(f"scanned={scanned} fixed={fixed} dry_run={args.dry_run}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
