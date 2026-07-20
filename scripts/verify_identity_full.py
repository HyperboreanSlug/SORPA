"""
FULL identity verification for SORPA offenders.db.

Checks:
  - HTML scrape name matches record
  - HTML DOB matches record (when both present)
  - Photos exist and align with report HTML tree
  - FDLE flyer links are not another person's page

Nuclear findings can be auto-stripped with --repair.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows consoles default to cp1252 and crash on audit strings that contain
# characters like ≠ / non-breaking hyphens. Force UTF-8 (with replacement) so
# the summary + nuclear sample always print.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from scraper.database import Database
from scraper.reports.identity_audit import run_full_audit


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--repair", action="store_true", help="Strip nuclear mismatches")
    ap.add_argument(
        "--all-rows",
        action="store_true",
        help="Scan every offender (default: only rows with html/photo/FL links)",
    )
    ap.add_argument(
        "--report",
        default="data/reports/identity_audit.csv",
        help="CSV report path for findings",
    )
    ap.add_argument("--max-print", type=int, default=80)
    args = ap.parse_args()

    db = Database(args.db)
    try:
        summary = run_full_audit(
            db,
            repair=bool(args.repair),
            limit=int(args.limit or 0),
            only_html=not bool(args.all_rows),
        )

        # Write CSV of findings
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "severity",
                    "code",
                    "offender_id",
                    "full_name",
                    "detail",
                    "html_name",
                    "html_dob",
                    "record_dob",
                    "html_path",
                    "source_url",
                ],
            )
            w.writeheader()
            for finding in summary.findings:
                w.writerow(
                    {
                        "severity": finding.severity,
                        "code": finding.code,
                        "offender_id": finding.offender_id,
                        "full_name": finding.full_name,
                        "detail": finding.detail,
                        "html_name": finding.html_name,
                        "html_dob": finding.html_dob,
                        "record_dob": finding.record_dob,
                        "html_path": finding.html_path,
                        "source_url": finding.source_url,
                    }
                )

        by_sev = Counter(f.severity for f in summary.findings)
        by_code = Counter(f.code for f in summary.findings)
        print()
        print("=== SUMMARY ===")
        print(f"scanned:          {summary.scanned:,}")
        print(f"with_html:        {summary.with_html:,}")
        print(f"with_photo:       {summary.with_photo:,}")
        print(f"name_ok:          {summary.name_ok:,}")
        print(f"name_mismatch:    {summary.name_mismatch:,}  (NUCLEAR)")
        print(f"name_unparsed:    {summary.name_unparsed:,}")
        print(f"dob_ok:           {summary.dob_ok:,}")
        print(f"dob_mismatch:     {summary.dob_mismatch:,}  (NUCLEAR)")
        print(f"photo_issues:     {summary.photo_orphan:,}")
        print(f"fl_link_wrong:    {summary.fl_link_suspect:,}  (NUCLEAR)")
        print(f"repaired:         {summary.repaired:,}")
        print(f"findings by severity: {dict(by_sev)}")
        print(f"findings by code: {dict(by_code)}")
        print(f"report: {report_path}")

        nuclear = [f for f in summary.findings if f.severity == "nuclear"]
        print()
        print(f"=== NUCLEAR sample (up to {args.max_print}) ===")
        for f in nuclear[: int(args.max_print)]:
            print(
                f"  [{f.code}] id={f.offender_id} {f.full_name}: {f.detail}"
            )
        if len(nuclear) > args.max_print:
            print(f"  … +{len(nuclear) - args.max_print} more nuclear findings in CSV")

        # Exit non-zero if unrepaired nuclear remain and not repair mode leftover
        remaining_nuclear = summary.name_mismatch + summary.dob_mismatch + summary.fl_link_suspect
        if remaining_nuclear and not args.repair:
            print(
                f"\nFAIL: {remaining_nuclear} nuclear issues remain. "
                "Re-run with --repair to strip wrong-person data."
            )
            return 2
        if args.repair:
            print("\nRepair pass complete. Re-run without --repair to verify clean.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
