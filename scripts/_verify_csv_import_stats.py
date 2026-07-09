#!/usr/bin/env python3
"""
Verify CSV import lands in SQLite and feeds Integrity + Misclassify statistics.

Run from repo root:
  python scripts/_verify_csv_import_stats.py
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


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def _ok(msg: str) -> None:
    print(f"OK:   {msg}")


def main() -> None:
    errors: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        db_path = tmp_p / "verify.db"
        db = Database(str(db_path))

        # --- 1) Title-case scrape-style CSV (filename → GA state) ---
        ga_csv = tmp_p / "ga_offenders.csv"
        with open(ga_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "First Name",
                    "Last Name",
                    "Race",
                    "Crime",
                    "Source URL",
                    "City",
                ],
            )
            w.writeheader()
            w.writerow({
                "First Name": "Ana",
                "Last Name": "Garcia",
                "Race": "White",
                "Crime": "Offense A",
                "Source URL": "https://example.gov/ga/1",
                "City": "Atlanta",
            })
            w.writerow({
                "First Name": "James",
                "Last Name": "Smith",
                "Race": "White",
                "Crime": "",
                "Source URL": "https://example.gov/ga/2",
                "City": "Macon",
            })
            w.writerow({
                "First Name": "Raj",
                "Last Name": "Patel",
                "Race": "White",  # Indian surname misclassified as White
                "Crime": "Offense B",
                "Source URL": "https://example.gov/ga/3",
                "City": "Savannah",
            })

        r1 = db.import_csv(str(ga_csv), skip_existing_urls=True)
        if r1["imported"] != 3:
            errors.append(f"GA import expected 3, got {r1}")
        else:
            _ok(f"GA CSV imported {r1['imported']} rows")

        # --- 2) Snake-case export-style CSV (explicit State) ---
        fl_csv = tmp_p / "custom_export.csv"
        with open(fl_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "first_name",
                    "last_name",
                    "race",
                    "state",
                    "crime",
                    "source_url",
                    "photo_path",
                ],
            )
            w.writeheader()
            w.writerow({
                "first_name": "Carlos",
                "last_name": "Rodriguez",
                "race": "White",
                "state": "FL",
                "crime": "Battery",
                "source_url": "https://example.gov/fl/1",
                "photo_path": "data/photos/x.jpg",
            })
            w.writerow({
                "first_name": "Wei",
                "last_name": "Chen",
                "race": "White",  # Asian surname misclassified
                "state": "FL",
                "crime": "Theft",
                "source_url": "https://example.gov/fl/2",
                "photo_path": "",
            })

        r2 = db.import_csv(str(fl_csv), skip_existing_urls=True)
        if r2["imported"] != 2:
            errors.append(f"FL import expected 2, got {r2}")
        else:
            _ok(f"snake-case CSV imported {r2['imported']} rows")

        # --- 3) Directory import with de-dupe ---
        dld = tmp_p / "downloads"
        dld.mkdir()
        # Copy CSVs + one duplicate URL file
        dup = dld / "az_offenders.csv"
        with open(dup, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f, fieldnames=["First Name", "Last Name", "Race", "Source URL"]
            )
            w.writeheader()
            w.writerow({
                "First Name": "X",
                "Last Name": "Y",
                "Race": "Black",
                "Source URL": "https://example.gov/ga/1",  # already in DB
            })
            w.writerow({
                "First Name": "New",
                "Last Name": "Person",
                "Race": "Black",
                "Source URL": "https://example.gov/az/9",
            })

        # Also put a fresh state file
        az2 = dld / "nm_offenders.csv"
        with open(az2, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f, fieldnames=["First Name", "Last Name", "Race", "Source URL"]
            )
            w.writeheader()
            w.writerow({
                "First Name": "Maria",
                "Last Name": "Lopez",
                "Race": "Hispanic",
                "Source URL": "https://example.gov/nm/1",
            })

        summary = db.import_csv_directory(str(dld), skip_existing_urls=True)
        # 1 skipped (dup URL) + 1 new from az + 1 from nm = imported 2, skipped 1
        if summary["imported"] != 2:
            errors.append(f"dir import expected imported=2, got {summary}")
        elif summary["skipped"] != 1:
            errors.append(f"dir import expected skipped=1, got {summary}")
        else:
            _ok(
                f"directory import files={summary['files']} "
                f"imported={summary['imported']} skipped={summary['skipped']}"
            )

        total = db.get_total_count()
        # 3 GA + 2 FL + 2 dir = 7
        if total != 7:
            errors.append(f"total count expected 7, got {total}")
        else:
            _ok(f"DB total count = {total}")

        # --- 4) Field normalization + state inference ---
        garcia = db.search_by_name("Garcia")
        if not garcia:
            errors.append("Garcia not found after import")
        else:
            g = garcia[0]
            if g.get("first_name") != "Ana":
                errors.append(f"Garcia first_name={g.get('first_name')!r}")
            if (g.get("state") or "").upper() != "GA":
                errors.append(f"Garcia state expected GA, got {g.get('state')!r}")
            if g.get("race") != "White":
                errors.append(f"Garcia race={g.get('race')!r}")
            if g.get("crime") != "Offense A":
                errors.append(f"Garcia crime={g.get('crime')!r}")
            if g.get("source_url") != "https://example.gov/ga/1":
                errors.append(f"Garcia url={g.get('source_url')!r}")
            _ok(
                f"Garcia row: state={g.get('state')} race={g.get('race')} "
                f"crime={g.get('crime')}"
            )

        # --- 5) Integrity statistics ---
        rep = db.get_integrity_report()
        ov = rep["overall"]
        if ov["total"] != 7:
            errors.append(f"integrity total {ov['total']} != 7")
        # All 7 have race
        if ov["with_race"] != 7:
            errors.append(f"with_race expected 7, got {ov['with_race']}")
        # Crimes: Garcia, Patel, Rodriguez, Chen = 4
        # Smith / Person / Lopez have empty crime
        if ov["with_crime"] != 4:
            errors.append(f"with_crime expected 4, got {ov['with_crime']}")
        # photo only Rodriguez
        if ov["with_photo"] != 1:
            errors.append(f"with_photo expected 1, got {ov['with_photo']}")
        # all have source_url
        if ov["with_url"] != 7:
            errors.append(f"with_url expected 7, got {ov['with_url']}")

        by_st = {s["state"]: s for s in rep["by_state"]}
        if "GA" not in by_st or by_st["GA"]["total"] != 3:
            errors.append(f"GA integrity bucket wrong: {by_st.get('GA')}")
        if "FL" not in by_st or by_st["FL"]["total"] != 2:
            errors.append(f"FL integrity bucket wrong: {by_st.get('FL')}")
        if "AZ" not in by_st or by_st["AZ"]["total"] != 1:
            errors.append(f"AZ integrity bucket wrong: {by_st.get('AZ')}")
        if "NM" not in by_st or by_st["NM"]["total"] != 1:
            errors.append(f"NM integrity bucket wrong: {by_st.get('NM')}")
        _ok(
            f"integrity overall total={ov['total']} race={ov['with_race']} "
            f"crime={ov['with_crime']} photo={ov['with_photo']} url={ov['with_url']}"
        )
        _ok(f"integrity by_state: { {k: by_st[k]['total'] for k in sorted(by_st)} }")

        # Race distribution
        races = { (r["race"] or ""): r["count"] for r in db.get_race_distribution() }
        if races.get("White", 0) < 4:
            errors.append(f"race dist White too low: {races}")
        else:
            _ok(f"race distribution includes White={races.get('White')}")

        # --- 6) Misclassify statistics on imported rows ---
        searcher = SexOffenderSearcher(db_path=str(db_path))
        try:
            results, base = searcher.analyze_ethnicities(
                min_confidence=0.5,
                limit=0,  # all
                ethnicity_filter="hispanic",
                return_base_count=True,
            )
            # Garcia, Rodriguez, Lopez = hispanic surnames (base ≥ 3)
            if base < 3:
                errors.append(f"hispanic base_count expected ≥3, got {base}")
            # Garcia+Rodriguez recorded White → misclass; Lopez is Hispanic-compatible
            hispanic_names = {
                (m.record.get("last_name") or "").lower() for m in results
            }
            if "garcia" not in hispanic_names:
                errors.append(f"Garcia not in hispanic misclass: {hispanic_names}")
            if "rodriguez" not in hispanic_names:
                errors.append(f"Rodriguez not in hispanic misclass: {hispanic_names}")
            if "lopez" in hispanic_names:
                # Lopez race=Hispanic should be compatible
                errors.append("Lopez should NOT be misclassified as race=Hispanic")
            _ok(
                f"hispanic misclass: {len(results)} of base {base} "
                f"names={sorted(hispanic_names)}"
            )

            indian_mc, indian_base = searcher.analyze_ethnicities(
                min_confidence=0.5,
                limit=0,
                ethnicity_filter="indian",
                return_base_count=True,
            )
            indian_names = {
                (m.record.get("last_name") or "").lower() for m in indian_mc
            }
            if "patel" not in indian_names:
                errors.append(f"Patel not in indian misclass: {indian_names}")
            if indian_base < 1:
                errors.append(f"indian base_count expected ≥1, got {indian_base}")
            _ok(
                f"indian misclass: {len(indian_mc)} of base {indian_base} "
                f"names={sorted(indian_names)}"
            )

            asian_mc, asian_base = searcher.analyze_ethnicities(
                min_confidence=0.5,
                limit=0,
                ethnicity_filter="asian",
                return_base_count=True,
            )
            asian_names = {
                (m.record.get("last_name") or "").lower() for m in asian_mc
            }
            if "chen" not in asian_names:
                errors.append(f"Chen not in asian misclass: {asian_names}")
            _ok(
                f"asian misclass: {len(asian_mc)} of base {asian_base} "
                f"names={sorted(asian_names)}"
            )
        finally:
            searcher.close()

        # --- 7) Re-import same files → all skipped, stats unchanged ---
        before = db.get_total_count()
        r_again = db.import_csv(str(ga_csv), skip_existing_urls=True)
        if r_again["imported"] != 0 or r_again["skipped"] != 3:
            errors.append(f"re-import should skip all: {r_again}")
        if db.get_total_count() != before:
            errors.append("total changed after re-import with skip")
        rep2 = db.get_integrity_report()
        if rep2["overall"]["total"] != before:
            errors.append("integrity total drifted after re-import")
        _ok("re-import with skip_existing leaves DB + integrity stable")

        db.close()

    if errors:
        print("\n=== VERIFICATION FAILED ===")
        for e in errors:
            print(f"  - {e}")
        raise SystemExit(1)

    print("\n=== ALL CSV IMPORT → DB → STATISTICS CHECKS PASSED ===")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
