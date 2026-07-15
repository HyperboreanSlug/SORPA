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


from scraper.cli_cmds_scrape import cmd_scrape, cmd_import, cmd_tag_sources, cmd_status
from scraper.cli_cmds_search import cmd_search, cmd_misclassify, cmd_export
from scraper.cli_cmds_mugshot import (
    cmd_mugshot_verify,
    cmd_mugshot_scan,
    cmd_mugshot_setup,
)
from scraper.cli_cmds_nsopw import cmd_nsopw
from scraper.cli_cmds_dedupe import cmd_dedupe

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
    p_scrape.add_argument(
        "--no-import-db",
        action="store_true",
        help="Only write CSVs; do not insert scrape results into SQLite (Misclassify needs DB rows)",
    )
    p_scrape.add_argument(
        "--force-reinsert",
        action="store_true",
        help="When importing scrape results, do not skip existing source_url rows",
    )
    _add_database_arg(p_scrape)

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
        choices=[
            "all", "hispanic", "asian",
            "indian", "mena", "indian/mena", "indian/mena (merged)", "merged",
            "african_american", "african", "arabic", "jewish", "portuguese",
            "native_american", "european",
        ],
        default="all",
        help=(
            "Misclass filter: indian=Indic only; mena=Arabic only; "
            "indian/mena (merged)=both"
        ),
    )
    p_misclassify.add_argument("--confidence", type=float, default=0.5, help="Minimum confidence threshold (0-1)")
    p_misclassify.add_argument(
        "--limit", type=int, default=0,
        help="Max records to scan (0 = entire DB; when set, newest ids first so new imports are included)",
    )
    p_misclassify.add_argument("--max-display", type=int, default=20, help="Max results to display")
    p_misclassify.add_argument("--export", type=str, help="Export misclassifications to CSV file")
    _add_database_arg(p_misclassify)

    # Mugshot ethnicity verify / scan
    p_mv = subparsers.add_parser(
        "mugshot-verify",
        help="Verify name-based misclass hits using mugshot ethnicity scores",
    )
    p_mv.add_argument(
        "--ethnicity",
        default="indian",
        help="Name ethnicity filter (default indian; use all for every family)",
    )
    p_mv.add_argument("--confidence", type=float, default=0.5, help="Min name confidence")
    p_mv.add_argument("--face-conf", type=float, default=0.75, help="Min face confidence")
    p_mv.add_argument(
        "--combined-conf", type=float, default=0.8,
        help="Min combined confidence to mark confirms_misclass",
    )
    p_mv.add_argument("--limit", type=int, default=500, help="Max name-misclass rows to consider")
    p_mv.add_argument("--max-display", type=int, default=30)
    p_mv.add_argument(
        "--backend", default="auto",
        choices=["auto", "deepface", "clip", "mock"],
        help="Vision backend (auto prefers deepface, then clip)",
    )
    p_mv.add_argument("--include-no-photo", action="store_true")
    p_mv.add_argument("--export", type=str, help="Export CSV or JSON path")
    _add_database_arg(p_mv)

    p_ms = subparsers.add_parser(
        "mugshot-scan",
        help="Scan mugshots for gross face-vs-race mismatches (e.g. Black face / White race)",
    )
    p_ms.add_argument(
        "--recorded-race", default="WHITE",
        help="Comma-separated registry races to scan (default WHITE)",
    )
    p_ms.add_argument(
        "--face-labels", default="black,indian,asian",
        help="Comma-separated face labels that flag a hit",
    )
    p_ms.add_argument("--min-conf", type=float, default=0.85, help="Min face confidence (high bar)")
    p_ms.add_argument("--limit", type=int, default=500, help="Max candidates with photos")
    p_ms.add_argument("--state", type=str, help="Limit to state")
    p_ms.add_argument("--max-display", type=int, default=40)
    p_ms.add_argument(
        "--backend", default="auto",
        choices=["auto", "deepface", "clip", "mock"],
    )
    p_ms.add_argument("--export", type=str, help="Export CSV or JSON path")
    _add_database_arg(p_ms)

    p_mds = subparsers.add_parser(
        "mugshot-setup",
        help="Install DeepFace + download race model weights (local, offline after)",
    )
    p_mds.add_argument(
        "--no-warm", action="store_true",
        help="Only pip-install packages; skip model weight download",
    )

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
    p_import.add_argument(
        "--no-merge-sources",
        action="store_true",
        help="Do not merge CSV rows into existing same-person records (always insert)",
    )
    _add_database_arg(p_import)

    # Multi-source provenance tagging
    p_tag = subparsers.add_parser(
        "tag-sources",
        help="Tag existing rows with sources_json provenance (prevent silent race overwrites)",
    )
    p_tag.add_argument(
        "--limit", type=int, default=0,
        help="Max rows to tag (0 = all missing sources_json)",
    )
    p_tag.add_argument(
        "--retag", action="store_true", dest="retags",
        help="Retag rows even if sources_json already present",
    )
    p_tag.add_argument(
        "--verify-html", action="store_true",
        help="After tagging, fetch report HTML for each source URL",
    )
    p_tag.add_argument(
        "--verify-limit", type=int, default=500,
        help="Max rows for HTML verification (default 500)",
    )
    p_tag.add_argument("--state", type=str, help="Limit HTML verify to one state")
    _add_database_arg(p_tag)

    # Dedupe command
    p_dedupe = subparsers.add_parser(
        "dedupe",
        help="Find and remove duplicate offender rows (by URL, external id, or name+DOB)",
    )
    p_dedupe.add_argument(
        "--strategy",
        choices=[
            "all", "source_url", "external_id", "name_state_dob", "name_dob",
            "name_state_soft", "name_state",
        ],
        default="all",
        help=(
            "Match key: source_url (strongest), external_id, name_state_dob, "
            "name_dob (multi-state), name_state_soft (name+state+photo/address), "
            "name_state (weaker), or all safe strategies (default)"
        ),
    )
    p_dedupe.add_argument(
        "--check", action="store_true", default=True,
        help="Report duplicate groups (default action)",
    )
    p_dedupe.add_argument(
        "--remove", action="store_true",
        help="Delete extras (keeps richest row per group; merges missing fields onto keeper)",
    )
    p_dedupe.add_argument(
        "--dry-run", action="store_true",
        help="With --remove, show what would be deleted without writing",
    )
    p_dedupe.add_argument(
        "--no-merge", action="store_true",
        help="Do not copy filled fields from deleted rows onto the kept row",
    )
    p_dedupe.add_argument(
        "--include-name-state", action="store_true",
        help="With --strategy all, also run weaker name+state matching",
    )
    p_dedupe.add_argument(
        "--force-unsafe",
        action="store_true",
        help=(
            "Also collapse shared CAPTCHA/portal URL groups (dangerous — "
            "can merge many different offenders). Default skips those."
        ),
    )
    p_dedupe.add_argument(
        "--max-show", type=int, default=15,
        help="Max sample groups to print (default 15)",
    )
    _add_database_arg(p_dedupe)

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
            "all", "hispanic", "asian",
            "indian", "mena", "indian/mena", "indian/mena (merged)", "merged",
            "african_american", "african",
            "arabic", "jewish", "portuguese", "native_american", "european",
        ],
        default="hispanic",
        help=(
            "Surname list: indian=Indic; mena=Arabic; "
            "indian/mena (merged)=both; default: hispanic"
        ),
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
        help=(
            "Repeat old searches: re-run (first, last) queries already in the "
            "completed-search log. Default is to skip finished queries."
        ),
    )
    p_nsopw.add_argument(
        "--repeat-searches", action="store_true", dest="no_resume",
        help="Alias for --no-resume (explicitly re-run completed searches)",
    )
    p_nsopw.add_argument(
        "--redownload-html", action="store_true",
        help="Re-fetch report pages even when local HTML already exists",
    )
    p_nsopw.add_argument(
        "--first-mode",
        choices=[
            "initials", "indian", "indian_wide",
            "common", "common_wide",  # aliases → indian / indian_wide
            "full", "custom",
        ],
        default="initials",
        help=(
            "Name strategy (default: initials = full A–Z firsts + all list surname digraphs). "
            "indian = abbreviated: Indian first letters ASRPMKVNBD AND top ~30 Indian "
            "surname digraphs (RA/CH/KA/…); indian_wide widens both; "
            "common/common_wide alias indian modes; full = full first names"
        ),
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
        "--report-threads", type=int, default=1,
        help=(
            "Parallel report-fetch worker threads (default: 1 = sequential). "
            "Each state website is only ever hit by one thread at a time; the "
            "report delay applies per state. The NSOPW search API stays serial."
        ),
    )
    p_nsopw.add_argument(
        "--skip-reports", action="store_true",
        help="Only save NSOPW hits + links; do not fetch report pages",
    )
    p_nsopw.add_argument(
        "--enrich-scope",
        choices=["all", "ethnicity_match"],
        default="all",
        help=(
            "When fetching reports: all hits (default) or ethnicity_match "
            "(only surnames on the selected ethnicity list)"
        ),
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
        "mugshot-verify": cmd_mugshot_verify,
        "mugshot-scan": cmd_mugshot_scan,
        "mugshot-setup": cmd_mugshot_setup,
        "export": cmd_export,
        "import": cmd_import,
        "tag-sources": cmd_tag_sources,
        "dedupe": cmd_dedupe,
        "status": cmd_status,
        "nsopw": cmd_nsopw,
    }
    commands[args.command](args)


