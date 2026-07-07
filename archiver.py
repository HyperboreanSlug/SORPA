#!/usr/bin/env python3
"""
US Public Sex Offender Registry (SOR) Data Archiver - CLI

Downloads direct bulk data files where available from public sex offender registries.
See README.md for details.
"""

import argparse
from pathlib import Path

from core import load_sources, perform_downloads, DEFAULT_DELAY


def cmd_list(args: argparse.Namespace) -> None:
    sources = load_sources()
    print(f"{'Jurisdiction':<25} {'Abbr':<6} {'Direct?':<8} Registry URL")
    print("-" * 100)
    direct_count = 0
    for s in sources:
        has_direct = "YES" if s.get("direct_downloads") else "no"
        if s.get("direct_downloads"):
            direct_count += 1
        print(f"{s['jurisdiction']:<25} {s['abbr']:<6} {has_direct:<8} {s['registry_url']}")
    print("-" * 100)
    print(f"Total jurisdictions: {len(sources)}")
    print(f"With known direct downloads: {direct_count}")
    print("\nNote: 'Direct?' = published bulk file available for automated download.")
    print("Most jurisdictions only provide interactive search pages.")


def cmd_download(args: argparse.Namespace) -> None:
    sources = load_sources()
    out_base = Path(args.output_dir)
    delay = args.delay

    targets = []

    if args.all_direct:
        targets = [s for s in sources if s.get("direct_downloads")]
        print(f"Downloading direct sources for {len(targets)} jurisdictions...")
    elif args.states:
        wanted = {x.strip().lower() for x in args.states.split(",")}
        targets = [
            s for s in sources
            if s["abbr"].lower() in wanted or s["jurisdiction"].lower() in wanted
        ]
        print(f"Downloading for {len(targets)} matching jurisdiction(s)...")
    else:
        print("No targets specified. Use --all-direct or --states.")
        return

    if not targets:
        print("No matching targets with direct downloads found.")
        return

    def log(msg: str):
        print(msg)

    perform_downloads(targets, out_base, delay=delay, log_callback=log)


def cmd_snapshot(args: argparse.Namespace) -> None:
    """Convenience wrapper."""
    args.all_direct = True
    args.states = None
    cmd_download(args)


def main():
    parser = argparse.ArgumentParser(
        description="Archive publicly available U.S. sex offender registry data. "
                    "See README for legal and usage requirements."
    )
    parser.add_argument("--output-dir", default="archives", help="Base directory for archives")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"Seconds to sleep between requests (default {DEFAULT_DELAY})")

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List all known jurisdictions and direct download status")
    p_list.set_defaults(func=cmd_list)

    p_dl = sub.add_parser("download", help="Download direct data files")
    p_dl.add_argument("--all-direct", action="store_true", help="Download from all known direct sources")
    p_dl.add_argument("--states", help="Comma-separated list of abbreviations or names (e.g. AZ,DC,Georgia)")
    p_dl.set_defaults(func=cmd_download)

    p_snap = sub.add_parser("snapshot", help="Create a dated snapshot of all direct-download sources")
    p_snap.set_defaults(func=cmd_snapshot)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
