#!/usr/bin/env python3
"""
CLI tool for mass-downloading and searching US sex offender databases.

Usage:
    # Scrape all states
    python -m scraper.cli scrape --all

    # Scrape specific states
    python -m scraper.cli scrape --states FL,TX,CA,NY

    # Search by name
    python -m scraper.cli search --name "Garcia"

    # Filter by race
    python -m scraper.cli search --race "White"

    # Find misclassifications (Hispanic names marked as White)
    python -m scraper.cli misclassify --ethnicity hispanic

    # Mugshot verify (name + face) / gross scan (face only)
    python -m scraper.cli mugshot-verify --ethnicity indian --limit 200
    python -m scraper.cli mugshot-scan --min-conf 0.85 --limit 500

    # Export results to CSV
    python -m scraper.cli export --output results.csv
"""

import argparse
import csv
from pathlib import Path

def cmd_scrape(args: argparse.Namespace) -> None:
    """Scrape offender data from state registries."""
    from .config import get_registry_by_abbr, REGISTRIES, get_bulk_capable_sources
    from .scrapers.base import ScraperFactory

    states = [s.strip() for s in args.states.split(",")] if args.states else []
    delay = args.delay

    # Determine which states to scrape
    if args.all:
        registries = [r for r in REGISTRIES if r.abbr != "US"]
    elif args.direct_only:
        # Prefer known bulk-capable paths (direct + arcgis + hybrid with files)
        registries = get_bulk_capable_sources()
        if not registries:
            registries = [r for r in REGISTRIES if r.direct_downloads]
    elif states:
        registries = []
        for s in states:
            reg = get_registry_by_abbr(s) or get_registry_by_abbr(s.title())
            if reg:
                registries.append(reg)
            else:
                print(f"  Warning: Unknown state '{s}', skipping.")
    else:
        print("No targets specified. Use --all, --direct-only, or --states.")
        print("Tip: --direct-only scrapes verified bulk sources (GA, DC, …).")
        return

    if not registries:
        print("No matching registries found.")
        return

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print("  Sex Offender Registry Scraper")
    print(f"{'='*60}")
    print(f"  States to scrape: {len(registries)}")
    print(f"  Output directory: {output_dir}")
    print(f"  Delay between requests: {delay}s")
    print(f"{'='*60}\n")

    do_import = not bool(getattr(args, "no_import_db", False))
    db_path = getattr(args, "database", None) or "data/offenders.db"
    skip_urls = not bool(getattr(args, "force_reinsert", False))

    total_records = 0
    total_imported = 0
    total_skipped = 0
    for reg in registries:
        abbr = reg.abbr
        print(f"\n[{abbr}] Scraping {reg.name}...")
        try:
            scraper = ScraperFactory.create(abbr, delay=delay)
            try:
                records = scraper.scrape()
            finally:
                scraper.close()

            if records:
                csv_path = output_dir / f"{abbr.lower()}_offenders.csv"
                # Union of keys so sparse records still export cleanly
                fieldnames: list = []
                seen = set()
                for record in records:
                    for key in record.keys():
                        if key not in seen:
                            seen.add(key)
                            fieldnames.append(key)
                with open(csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                    writer.writeheader()
                    for record in records:
                        writer.writerow(record)

                print(f"  ✓ Saved {len(records)} records to {csv_path}")
                total_records += len(records)

                if do_import:
                    from .database import Database

                    db = Database(db_path)
                    try:
                        imp = db.import_records(
                            records,
                            state=abbr,
                            skip_existing_urls=skip_urls,
                        )
                    finally:
                        db.close()
                    total_imported += int(imp.get("imported") or 0)
                    total_skipped += int(imp.get("skipped") or 0)
                    print(
                        f"  ✓ DB import: +{imp.get('imported', 0)} "
                        f"(skipped {imp.get('skipped', 0)}) → {db_path}"
                    )
            else:
                print("  - No records found (may need direct download or API access)")

        except Exception as e:
            print(f"  ✗ Error: {e}")

    print(f"\n{'='*60}")
    print(f"  Total records scraped: {total_records}")
    if do_import:
        print(f"  DB imported: {total_imported} (skipped {total_skipped})")
        print(f"  Database: {db_path}")
        print("  Next: python -m scraper.cli misclassify --ethnicity all")
    else:
        print("  DB import skipped (--no-import-db); use: python -m scraper.cli import -i …")
    print(f"  Output directory: {output_dir}")
    print(f"{'='*60}\n")


def cmd_import(args: argparse.Namespace) -> None:
    """Import CSV files into the database."""
    from .database import Database

    db_path = args.database or "data/offenders.db"
    db = Database(db_path)

    input_path = Path(args.input)
    if input_path.is_file():
        csv_files = [input_path]
    else:
        csv_files = sorted(input_path.glob("*.csv"))

    if not csv_files:
        print(f"No CSV files found at {input_path}")
        db.close()
        return

    merge_sources = not getattr(args, "no_merge_sources", False)
    total_imported = 0
    total_skipped = 0
    total_merged = 0
    try:
        for csv_file in csv_files:
            print(f"\nImporting {csv_file.name}...")
            result = db.import_csv(
                str(csv_file),
                state=args.state,
                merge_sources=merge_sources,
            )
            if isinstance(result, dict):
                print(
                    f"  Imported {result.get('imported', 0)} "
                    f"(merged sources into {result.get('merged', 0)} existing, "
                    f"skipped {result.get('skipped', 0)}, "
                    f"{result.get('total_rows', 0)} rows)."
                )
                total_imported += int(result.get("imported") or 0)
                total_skipped += int(result.get("skipped") or 0)
                total_merged += int(result.get("merged") or 0)
            else:
                print(f"  Imported {result} records.")
                total_imported += int(result or 0)
    finally:
        db.close()

    print(
        f"\nTotal imported: {total_imported} records to {db_path}"
        + (f" ({total_merged} source-merged)" if total_merged else "")
        + (f" ({total_skipped} skipped)" if total_skipped else "")
    )


def cmd_tag_sources(args: argparse.Namespace) -> None:
    """Backfill sources_json provenance on existing rows; optional HTML verify."""
    from .database import Database

    db_path = args.database or "data/offenders.db"
    db = Database(db_path)
    try:
        print(f"Backfilling sources on {db_path}…")
        result = db.backfill_sources(
            limit=getattr(args, "limit", None) or None,
            only_missing=not getattr(args, "retags", False),
            log=print,
        )
        print(
            f"Backfill: scanned={result.get('scanned')} "
            f"updated={result.get('updated')}"
        )
        if getattr(args, "verify_html", False):
            from .nsopw_builder import NSOPWEthnicDatabaseBuilder

            builder = NSOPWEthnicDatabaseBuilder(db=db)
            v = builder.verify_all_sources(
                limit=int(getattr(args, "verify_limit", 0) or 0) or 500,
                state=getattr(args, "state", None),
                only_unverified=True,
                log=print,
            )
            print(f"HTML verify: {v}")
    finally:
        db.close()


def cmd_repair_fl_sor(args: argparse.Namespace) -> None:
    """Re-apply fl_sor.csv so every FDLE person has id, FL source, URL."""
    from pathlib import Path

    from .database import Database
    from .database.backup import backup_database_file

    db_path = args.database or "data/offenders.db"
    csv_path = (
        getattr(args, "input", None)
        or "data/downloads/fl_sor.csv"
    )
    if not getattr(args, "no_backup", False):
        try:
            bak_dir = Path(db_path).resolve().parent / "backups"
            bak, note = backup_database_file(db_path, bak_dir)
            print(f"Backup: {bak}" + (f" ({note})" if note else ""))
        except Exception as e:
            print(f"Backup skipped/failed: {e}")

    db = Database(db_path)
    try:
        print(f"Repairing FL SOR from {csv_path} → {db_path}")
        result = db.repair_fl_sor_from_csv(csv_path, log=print)
        print(f"Result: {result}")
    finally:
        db.close()


def cmd_status(args: argparse.Namespace) -> None:
    """Show scrape support matrix for all registries."""
    from .config import REGISTRIES

    print(f"\n{'Abbr':<5} {'Method':<14} {'Bulk?':<7} Jurisdiction")
    print("-" * 72)
    bulk_n = 0
    for r in REGISTRIES:
        if r.abbr == "US":
            continue
        has_bulk = r.scrape_method in ("direct", "arcgis", "api") or bool(r.direct_downloads)
        if has_bulk:
            bulk_n += 1
        flag = "YES" if has_bulk else "no"
        print(f"{r.abbr:<5} {r.scrape_method:<14} {flag:<7} {r.name}")
        if r.notes and args.verbose:
            print(f"      {r.notes[:90]}")
    print("-" * 72)
    print(f"Bulk-capable (configured): {bulk_n}")
    print("Most states are interactive search only and cannot be bulk-scraped.\n")


