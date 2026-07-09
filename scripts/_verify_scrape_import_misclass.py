#!/usr/bin/env python3
"""
Verify scrape-like + CSV import rows land in SQLite and appear in Misclassify.

  python scripts/_verify_scrape_import_misclass.py
"""
from __future__ import annotations

import csv
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.database import Database
from scraper.searcher import SexOffenderSearcher


def main() -> None:
    errors: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        db_path = tmp_p / "verify.db"
        db = Database(str(db_path))

        # --- Simulate "old" bulk of rows (would fill a 10k ASC scan cap) ---
        # Keep small but prove newest-first: insert many fillers then new rows.
        fillers = [
            {
                "first_name": f"Old{i}",
                "last_name": "Smith",
                "race": "White",
                "state": "TX",
                "source_url": f"https://example.gov/old/{i}",
            }
            for i in range(20)
        ]
        r0 = db.import_records(fillers, state="TX", skip_existing_urls=True)
        if r0["imported"] != 20:
            errors.append(f"fillers import {r0}")

        # --- Simulate scrape results (in-memory → import_records) ---
        scrape_hits = [
            {
                "first_name": "Ana",
                "last_name": "Garcia",
                "race": "White",
                "crime": "Offense",
                "source_url": "https://example.gov/ga/garcia",
                "city": "Atlanta",
            },
            {
                "first_name": "Raj",
                "last_name": "Patel",
                "race": "White",
                "crime": "Offense",
                "source_url": "https://example.gov/ga/patel",
            },
            {
                "first_name": "Wei",
                "last_name": "Chen",
                "race": "White",
                "source_url": "https://example.gov/ga/chen",
            },
        ]
        r_scrape = db.import_records(scrape_hits, state="GA", skip_existing_urls=True)
        if r_scrape["imported"] != 3:
            errors.append(f"scrape import expected 3 got {r_scrape}")
        else:
            print(f"OK: scrape-like import +{r_scrape['imported']}")

        # --- Simulate CSV import (file path) ---
        csv_path = tmp_p / "fl_offenders.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["First Name", "Last Name", "Race", "Source URL"],
            )
            w.writeheader()
            w.writerow({
                "First Name": "Carlos",
                "Last Name": "Rodriguez",
                "Race": "White",
                "Source URL": "https://example.gov/fl/rod",
            })
            w.writerow({
                "First Name": "Bob",
                "Last Name": "Jones",
                "Race": "White",
                "Source URL": "https://example.gov/fl/jones",
            })
        r_csv = db.import_csv(str(csv_path), skip_existing_urls=True)
        if r_csv["imported"] != 2:
            errors.append(f"csv import expected 2 got {r_csv}")
        else:
            print(f"OK: CSV import +{r_csv['imported']}")

        total = db.get_total_count()
        if total != 25:
            errors.append(f"total expected 25 got {total}")
        else:
            print(f"OK: DB total={total}")

        # Search finds scraped/imported
        g = db.search_by_name("Garcia")
        if not g or g[0].get("state") != "GA":
            errors.append(f"Garcia missing/wrong state: {g[:1]}")
        else:
            print(f"OK: Garcia in DB state={g[0].get('state')} full={g[0].get('full_name')}")

        # Integrity counts them
        rep = db.get_integrity_report()
        by = {s["state"]: s["total"] for s in rep["by_state"]}
        if by.get("GA") != 3 or by.get("FL") != 2:
            errors.append(f"integrity by_state wrong: {by}")
        else:
            print(f"OK: integrity GA={by.get('GA')} FL={by.get('FL')}")

        db.close()

        # --- Misclassify must find newest imported ethnic surnames ---
        searcher = SexOffenderSearcher(db_path=str(db_path))
        try:
            # Unlimited scan
            all_mc, base = searcher.analyze_ethnicities(
                min_confidence=0.5,
                limit=0,
                ethnicity_filter="hispanic",
                return_base_count=True,
            )
            names = {(m.record.get("last_name") or "").lower() for m in all_mc}
            if "garcia" not in names or "rodriguez" not in names:
                errors.append(f"full scan hispanic misclass missing names: {names}")
            else:
                print(f"OK: full-scan hispanic misclass {sorted(names)} (base={base})")

            indian_mc, ib = searcher.analyze_ethnicities(
                min_confidence=0.5, limit=0, ethnicity_filter="indian",
                return_base_count=True,
            )
            inames = {(m.record.get("last_name") or "").lower() for m in indian_mc}
            if "patel" not in inames:
                errors.append(f"indian misclass missing Patel: {inames}")
            else:
                print(f"OK: indian misclass {sorted(inames)} (base={ib})")

            asian_mc, ab = searcher.analyze_ethnicities(
                min_confidence=0.5, limit=0, ethnicity_filter="asian",
                return_base_count=True,
            )
            anames = {(m.record.get("last_name") or "").lower() for m in asian_mc}
            if "chen" not in anames:
                errors.append(f"asian misclass missing Chen: {anames}")
            else:
                print(f"OK: asian misclass {sorted(anames)} (base={ab})")

            # Limited scan must prefer newest (Garcia/Patel/Chen/Rodriguez at end)
            # Cap of 5 should still see the new imports if newest_first works.
            limited_mc, _ = searcher.analyze_ethnicities(
                min_confidence=0.5,
                limit=5,
                ethnicity_filter="hispanic",
                return_base_count=True,
            )
            lim_names = {
                (m.record.get("last_name") or "").lower() for m in limited_mc
            }
            # Among last 5 rows: Rodriguez, Jones, Chen, Patel, Garcia (or similar)
            # At least one hispanic misclass from new batch should appear
            if not lim_names.intersection({"garcia", "rodriguez"}):
                errors.append(
                    f"newest-first limited scan missed new hispanic rows: {lim_names}"
                )
            else:
                print(f"OK: limited(5) newest-first hispanic hits {sorted(lim_names)}")

            # Prove ASC-only would fail: only old Smith fillers at start
            old_only = list(searcher.db.iter_offenders(limit=5, newest_first=False))
            old_names = {(r.get("last_name") or "").lower() for r in old_only}
            if "garcia" in old_names:
                errors.append("unexpected: oldest-5 includes Garcia")
            if "smith" not in old_names:
                errors.append(f"oldest-5 should be Smith fillers: {old_names}")
            else:
                print(f"OK: oldest-5 are fillers {old_names} (would miss new imports)")
        finally:
            searcher.close()

    if errors:
        print("\n=== FAILED ===")
        for e in errors:
            print(f"  - {e}")
        raise SystemExit(1)
    print("\n=== SCRAPE/IMPORT → DB → MISCLASSIFICATION VERIFIED ===")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
