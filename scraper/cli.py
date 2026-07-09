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

    total_records = 0
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
            else:
                print("  - No records found (may need direct download or API access)")

        except Exception as e:
            print(f"  ✗ Error: {e}")

    print(f"\n{'='*60}")
    print(f"  Total records scraped: {total_records}")
    print(f"  Output directory: {output_dir}")
    print(f"{'='*60}\n")


def cmd_search(args: argparse.Namespace) -> None:
    """Search offender database."""
    from .searcher import SexOffenderSearcher

    db_path = args.database or "data/offenders.db"
    searcher = SexOffenderSearcher(db_path=db_path)

    try:
        if args.name:
            results = searcher.search_by_name(
                name=args.name,
                state=args.state,
                race=args.race,
                limit=args.limit
            )
            print(f"\n{'='*60}")
            print(f"  Search Results for '{args.name}'")
            print(f"{'='*60}")
            print(f"  Total matching: {results.total_count}")
            print(f"  Query time: {results.query_time_ms:.1f}ms")

            if results.records:
                print(f"\n  {'Name':<35} {'Race':<12} {'State':<6} {'Age':>4} {'County':<20}")
                print(f"  {'-'*87}")
                for r in results.records[:args.limit]:
                    name = f"{r.get('first_name', '') or ''} {r.get('last_name', '') or ''}".strip()
                    race = (r.get("race") or "N/A")[:12]
                    state = (r.get("state") or "N/A")[:6]
                    age = r.get("age", "")
                    county = (r.get("county") or "N/A")[:20]
                    print(f"  {name:<35} {race:<12} {state:<6} {str(age):>4} {county:<20}")

            if args.export:
                searcher.db.export_to_csv(
                    args.export,
                    filters={"name": args.name, "state": args.state, "race": args.race},
                )
                print(f"\n  Exported to: {args.export}")

        elif args.race:
            results = searcher.search_by_race(
                race=args.race,
                state=args.state,
                limit=args.limit
            )
            scope = f" in {args.state}" if args.state else ""
            print(f"\n{'='*60}")
            print(f"  Records with race '{args.race}'{scope}: {len(results.records)}")
            print(f"{'='*60}")

            if results.records:
                print(f"\n  {'Name':<35} {'Race':<12} {'State':<6} {'Age':>4}")
                print(f"  {'-'*60}")
                for r in results.records[:min(args.limit, 50)]:
                    name = f"{r.get('first_name', '') or ''} {r.get('last_name', '') or ''}".strip()
                    race = (r.get("race") or "N/A")[:12]
                    state = (r.get("state") or "N/A")[:6]
                    age = r.get("age", "")
                    print(f"  {name:<35} {race:<12} {state:<6} {str(age):>4}")

            if args.export:
                searcher.db.export_to_csv(
                    args.export,
                    filters={"race": args.race, "state": args.state},
                )
                print(f"\n  Exported to: {args.export}")

        elif args.state:
            results = searcher.search_by_state(
                state=args.state,
                limit=args.limit
            )
            print(f"\n{'='*60}")
            print(f"  Offenders in {args.state}: {len(results.records)}")
            print(f"{'='*60}")

            if results.records:
                for r in results.records[:20]:
                    name = f"{r.get('first_name', '') or ''} {r.get('last_name', '') or ''}".strip()
                    race = (r.get("race") or "N/A")
                    age = r.get("age", "")
                    print(f"  {name:<35} Race: {race:<12} Age: {str(age):>4}")

            if args.export:
                searcher.db.export_to_csv(args.export, filters={"state": args.state})
                print(f"\n  Exported to: {args.export}")

        else:
            # Show summary stats
            total = searcher.get_total_count()
            race_dist = searcher.get_race_distribution()
            state_dist = searcher.get_state_distribution()

            print(f"\n{'='*60}")
            print("  Sex Offender Database Summary")
            print(f"{'='*60}")
            print(f"\n  Total records: {total:,}")

            print("\n  Race Distribution:")
            for dist in race_dist:
                race = dist.get("race") or "N/A"
                count = dist.get("count", 0)
                pct = (count / total * 100) if total else 0
                bar = "#" * int(pct / 2)
                print(f"    {race:<15} {count:>8,}  {pct:6.1f}%  {bar}")

            print("\n  Top States:")
            for dist in state_dist[:10]:
                state = dist.get("state") or "N/A"
                count = dist.get("count", 0)
                pct = (count / total * 100) if total else 0
                bar = "#" * int(pct / 2)
                print(f"    {state:<6} {count:>8,}  {pct:6.1f}%  {bar}")

            print(f"\n{'='*60}\n")
    finally:
        searcher.close()


def cmd_misclassify(args: argparse.Namespace) -> None:
    """Find potential race/ethnicity misclassifications."""
    from .searcher import SexOffenderSearcher

    db_path = args.database or "data/offenders.db"
    searcher = SexOffenderSearcher(db_path=db_path)

    min_confidence = args.confidence
    limit = args.limit
    ethnicity = args.ethnicity

    print(f"\n{'='*60}")
    print("  Analyzing for Misclassifications")
    print(f"{'='*60}")
    print(f"  Ethnicity filter: {ethnicity}")
    print(f"  Min confidence: {min_confidence}")
    print(f"  Max records to analyze: {limit}")
    print()

    try:
        if ethnicity == "hispanic":
            results = searcher.find_hispanic_misclassifications(
                min_confidence=min_confidence, limit=limit
            )
            title = "Hispanic Names with Non-Hispanic Race Classification"
        elif ethnicity == "asian":
            results = searcher.find_asian_misclassifications(
                min_confidence=min_confidence, limit=limit
            )
            title = "Asian Names with Non-Asian Race Classification"
        elif ethnicity == "african_american":
            results = searcher.find_african_american_misclassifications(
                min_confidence=min_confidence, limit=limit
            )
            title = "African-American Names with Non-Black Race Classification"
        else:
            results = searcher.analyze_ethnicities(
                min_confidence=min_confidence, limit=limit
            )
            title = "All Potential Misclassifications"

        print(f"  {title}")
        print(f"\n  Found {len(results)} potential misclassifications:\n")
        print(f"  {'Name':<35} {'Recorded Race':<15} {'Likely Ethnicity':<20} {'Confidence':>10}")
        print(f"  {'-'*82}")

        for mc in results[:args.max_display]:
            name = f"{mc.record.get('first_name', '') or ''} {mc.record.get('last_name', '') or ''}".strip()
            race = (mc.expected_race or "N/A")[:15]
            likely = mc.likely_ethnicity[:20]
            conf = f"{mc.confidence:.3f}"
            print(f"  {name:<35} {race:<15} {likely:<20} {conf:>10}")

        if args.export:
            eth_filter = None if ethnicity == "all" else ethnicity
            count = searcher.export_misclassifications(
                args.export,
                min_confidence=min_confidence,
                limit=limit,
                ethnicity_filter=eth_filter,
            )
            print(f"\n  Exported {count} records to: {args.export}")

        print(f"\n{'='*60}\n")
    finally:
        searcher.close()


def cmd_export(args: argparse.Namespace) -> None:
    """Export filtered data from the database."""
    from .searcher import SexOffenderSearcher

    db_path = args.database or "data/offenders.db"
    searcher = SexOffenderSearcher(db_path=db_path)

    try:
        filters = {}
        if args.state:
            filters["state"] = args.state
        if args.race:
            filters["race"] = args.race
        if args.name:
            filters["name"] = args.name

        if args.name and not args.state and not args.race:
            # Name-only path uses search results for consistent ranking
            results = searcher.search_by_name(args.name, limit=args.limit)
            with open(args.output, "w", newline="", encoding="utf-8") as f:
                if results.records:
                    writer = csv.DictWriter(f, fieldnames=list(results.records[0].keys()))
                    writer.writeheader()
                    for record in results.records[:args.limit]:
                        writer.writerow(record)
                else:
                    writer = csv.DictWriter(f, fieldnames=["id"])
                    writer.writeheader()
            print(f"Exported {len(results.records)} records to {args.output}")
        else:
            count = searcher.export_filtered(args.output, filters=filters)
            print(f"Exported {count} records to {args.output}")
    finally:
        searcher.close()


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


def cmd_nsopw(args: argparse.Namespace) -> None:
    """Search NSOPW for common ethnic surnames and build a local database."""
    from .nsopw_builder import NSOPWEthnicDatabaseBuilder

    first_names = None
    first_mode = getattr(args, "first_mode", "initials") or "initials"
    if args.first_names:
        first_names = [x.strip() for x in args.first_names.split(",") if x.strip()]
        first_mode = "custom"
    elif getattr(args, "initials_only", False):
        first_names = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        first_mode = "initials"

    jurisdictions = None
    if args.jurisdictions:
        jurisdictions = [x.strip().upper() for x in args.jurisdictions.split(",") if x.strip()]

    print("\n" + "=" * 60)
    print("  NSOPW Ethnic Name Search → Local Database")
    print("=" * 60)
    print("  Source: https://www.nsopw.gov/")
    print("  Short partials (e.g. M + AH) meet the 3-letter min and collapse surnames.")
    print("  Rate limits enforced; Conditions of Use apply.")
    print("=" * 60 + "\n")

    builder = NSOPWEthnicDatabaseBuilder(
        db_path=args.database or "data/offenders.db",
        delay=args.delay,
        report_delay=args.report_delay,
        html_dir=getattr(args, "html_dir", None) or "data/report_pages",
    )
    try:
        stats = builder.build(
            ethnicity=args.ethnicity,
            surnames_limit=args.surnames,
            all_surnames=bool(getattr(args, "all_surnames", False)),
            subcategory=getattr(args, "subcategory", None) or "all",
            first_names=first_names,
            first_mode=first_mode,
            jurisdictions=jurisdictions,
            max_searches=args.max_searches,
            max_names=(
                args.max_names
                if getattr(args, "max_names", None) is not None
                else args.max_reports
            ),
            skip_existing_urls=not args.force_reinsert,
            skip_completed_searches=not bool(getattr(args, "no_resume", False)),
            new_files_only=not bool(getattr(args, "redownload_html", False)),
            enrich_reports=not args.skip_reports,
            save_html=not getattr(args, "no_save_html", False),
        )
        print(f"\nDatabase: {args.database or 'data/offenders.db'}")
        print(f"Inserted {stats.inserted} new records "
              f"({stats.reports_with_demographics} with race/ethnicity from reports, "
              f"{stats.html_saved} HTML pages saved).")
    finally:
        builder.close()


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

    total_imported = 0
    total_skipped = 0
    try:
        for csv_file in csv_files:
            print(f"\nImporting {csv_file.name}...")
            result = db.import_csv(str(csv_file), state=args.state)
            if isinstance(result, dict):
                print(
                    f"  Imported {result.get('imported', 0)} "
                    f"(skipped {result.get('skipped', 0)} existing URLs, "
                    f"{result.get('total_rows', 0)} rows)."
                )
                total_imported += int(result.get("imported") or 0)
                total_skipped += int(result.get("skipped") or 0)
            else:
                print(f"  Imported {result} records.")
                total_imported += int(result or 0)
    finally:
        db.close()

    print(
        f"\nTotal imported: {total_imported} records to {db_path}"
        + (f" ({total_skipped} skipped)" if total_skipped else "")
    )


def _add_database_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database", "-d",
        default="data/offenders.db",
        help="Path to SQLite database (default: data/offenders.db)",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Sex Offender Database Scraper - Mass download and search tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scrape all states
  python -m scraper.cli scrape --all

  # Scrape specific states
  python -m scraper.cli scrape --states FL,TX,CA,NY

  # Only states with bulk downloads
  python -m scraper.cli scrape --direct-only

  # Search by name
  python -m scraper.cli search --name "Garcia"

  # Find Hispanic names marked as White
  python -m scraper.cli misclassify --ethnicity hispanic

  # Export to CSV
  python -m scraper.cli export --output results.csv

  # NSOPW ethnic surname search → database (polite rate limits)
  python -m scraper.cli nsopw --ethnicity hispanic --surnames 5 --max-searches 20
        """
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Scrape command
    p_scrape = subparsers.add_parser("scrape", help="Scrape offender data from state registries")
    p_scrape.add_argument("--all", action="store_true", help="Scrape all states")
    p_scrape.add_argument("--states", type=str, help="Comma-separated state abbreviations (e.g., FL,TX,CA)")
    p_scrape.add_argument(
        "--direct-only",
        action="store_true",
        help="Only scrape jurisdictions with bulk paths (direct/arcgis/hybrid)",
    )
    p_scrape.add_argument("--output", default="data/downloads", help="Output directory for scraped data")
    p_scrape.add_argument("--delay", type=float, default=1.0, help="Delay between requests (seconds)")

    # Search command
    p_search = subparsers.add_parser("search", help="Search offender database")
    p_search.add_argument("--name", type=str, help="Search by name")
    p_search.add_argument("--state", type=str, help="Filter by state")
    p_search.add_argument("--race", type=str, help="Filter by race")
    p_search.add_argument("--limit", type=int, default=1000, help="Maximum results to return")
    p_search.add_argument("--export", type=str, help="Export results to CSV file")
    _add_database_arg(p_search)

    # Misclassification command
    p_misclassify = subparsers.add_parser("misclassify", help="Find potential race/ethnicity misclassifications")
    p_misclassify.add_argument(
        "--ethnicity",
        choices=["all", "hispanic", "asian", "african_american"],
        default="all",
        help="Type of ethnicity to check for misclassification",
    )
    p_misclassify.add_argument("--confidence", type=float, default=0.5, help="Minimum confidence threshold (0-1)")
    p_misclassify.add_argument("--limit", type=int, default=10000, help="Max records to analyze")
    p_misclassify.add_argument("--max-display", type=int, default=20, help="Max results to display")
    p_misclassify.add_argument("--export", type=str, help="Export misclassifications to CSV file")
    _add_database_arg(p_misclassify)

    # Export command
    p_export = subparsers.add_parser("export", help="Export filtered data from database")
    p_export.add_argument("--output", "-o", default="data/export.csv", help="Output file path")
    p_export.add_argument("--state", type=str, help="Filter by state")
    p_export.add_argument("--race", type=str, help="Filter by race")
    p_export.add_argument("--name", type=str, help="Filter by name")
    p_export.add_argument("--limit", type=int, default=10000, help="Max records to export")
    _add_database_arg(p_export)

    # Import command
    p_import = subparsers.add_parser("import", help="Import CSV files into database")
    p_import.add_argument("--input", "-i", default="data/downloads", help="Input directory or CSV file")
    p_import.add_argument("--state", type=str, help="Default state for imported records")
    _add_database_arg(p_import)

    # Status command
    p_status = subparsers.add_parser("status", help="Show per-state scrape support matrix")
    p_status.add_argument("-v", "--verbose", action="store_true", help="Show notes")

    # NSOPW ethnic search command
    p_nsopw = subparsers.add_parser(
        "nsopw",
        help="Search NSOPW for common ethnic surnames; save report links + demographics",
    )
    p_nsopw.add_argument(
        "--ethnicity",
        choices=[
            "all", "hispanic", "asian", "indian", "african_american", "african",
            "arabic", "jewish", "portuguese", "native_american", "european",
        ],
        default="hispanic",
        help="Ethnic surname list (asian=East/SE Asian; indian=South Asian; default: hispanic)",
    )
    p_nsopw.add_argument(
        "--subcategory", type=str, default="all",
        help="Nested group within ethnicity (e.g. chinese, korean, india, german) or 'all'",
    )
    p_nsopw.add_argument(
        "--surnames", type=int, default=10,
        help="Max surnames per ethnic group (default: 10; ignored with --all-surnames)",
    )
    p_nsopw.add_argument(
        "--all-surnames", action="store_true",
        help="Search every surname in the selected ethnic list(s)",
    )
    p_nsopw.add_argument(
        "--no-resume", action="store_true",
        help="Do not skip previously completed (first, surname) searches",
    )
    p_nsopw.add_argument(
        "--redownload-html", action="store_true",
        help="Re-fetch report pages even when local HTML already exists",
    )
    p_nsopw.add_argument(
        "--first-mode",
        choices=["initials", "full", "custom"],
        default="initials",
        help="First-name strategy: initials A–Z (partial match, default), full names, or custom list",
    )
    p_nsopw.add_argument(
        "--first-names", type=str, default=None,
        help="Comma-separated first names/prefixes (implies custom mode)",
    )
    p_nsopw.add_argument(
        "--initials-only", action="store_true",
        help="Deprecated alias for --first-mode initials",
    )
    p_nsopw.add_argument(
        "--html-dir", default="data/report_pages",
        help="Directory to store archived report HTML pages",
    )
    p_nsopw.add_argument(
        "--no-save-html", action="store_true",
        help="Do not save report HTML snapshots",
    )
    p_nsopw.add_argument(
        "--jurisdictions", type=str, default=None,
        help="Comma-separated jurisdiction codes (default: all states/territories)",
    )
    p_nsopw.add_argument(
        "--max-searches", type=int, default=40,
        help="Maximum NSOPW name queries to run (default: 40; 0 = unlimited)",
    )
    p_nsopw.add_argument(
        "--max-reports", type=int, default=80,
        help="Maximum unique offender names to process (default: 80; 0 = unlimited)",
    )
    p_nsopw.add_argument(
        "--max-names", type=int, default=None,
        help="Alias for --max-reports (max unique names; 0 = unlimited)",
    )
    p_nsopw.add_argument(
        "--delay", type=float, default=3.0,
        help="Seconds between NSOPW API searches (default: 3.0; floor 2.0 for Cloudflare)",
    )
    p_nsopw.add_argument(
        "--report-delay", type=float, default=0.75,
        help="Seconds between state report/HTML fetches (default: 0.75; floor 0.25)",
    )
    p_nsopw.add_argument(
        "--skip-reports", action="store_true",
        help="Only save NSOPW hits + links; do not fetch report pages",
    )
    p_nsopw.add_argument(
        "--force-reinsert", action="store_true",
        help="Insert even if source_url already exists",
    )
    _add_database_arg(p_nsopw)

    args = parser.parse_args()

    commands = {
        "scrape": cmd_scrape,
        "search": cmd_search,
        "misclassify": cmd_misclassify,
        "export": cmd_export,
        "import": cmd_import,
        "status": cmd_status,
        "nsopw": cmd_nsopw,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
