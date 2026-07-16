"""Repair MA SORB junk crime fields and broken lowercase source URLs.

Re-parses archived report HTML when available; always restores camelCase action paths.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scraper.public_links import normalize_ma_sorb_url  # noqa: E402
from scraper.reports.fetcher import ReportFetcher  # noqa: E402
from scraper.reports.fetcher_crime import is_demographic_crime_junk  # noqa: E402

DB = ROOT / "data" / "offenders.db"


def main() -> int:
    dry = "--dry-run" in sys.argv
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, full_name, crime, offense_type, offense_description,
               source_url, external_id, report_html_path, address
        FROM offenders
        WHERE source_url LIKE '%sorb.chs.state.ma.us%'
           OR source_state LIKE '%MA%'
           OR (crime LIKE '%Photo Date%' OR crime LIKE '%Year Of Birth%')
           OR (offense_description LIKE '%Photo Date%')
        """
    ).fetchall()

    fetcher = ReportFetcher(delay=0)
    updated = 0
    url_fixed = 0
    crime_fixed = 0
    samples: list[str] = []

    for r in rows:
        oid = int(r["id"])
        patch: dict = {}

        # Fix source_url / external_id casing
        for col in ("source_url", "external_id"):
            raw = (r[col] or "").strip()
            if not raw or "sorb.chs.state.ma.us" not in raw.lower():
                continue
            fixed = normalize_ma_sorb_url(raw)
            if fixed and fixed != raw:
                patch[col] = fixed
                url_fixed += 1

        crime = (r["crime"] or "").strip()
        junk = bool(crime and is_demographic_crime_junk(crime))
        odesc = (r["offense_description"] or "").strip()
        if odesc and is_demographic_crime_junk(odesc):
            junk = True

        html_rel = (r["report_html_path"] or "").strip()
        html_path = None
        if html_rel:
            for cand in (Path(html_rel), ROOT / html_rel, ROOT / html_rel.replace("\\", "/")):
                if cand.is_file():
                    html_path = cand
                    break

        if junk or (html_path and (not crime or junk)):
            if html_path is not None:
                try:
                    html = html_path.read_text(encoding="utf-8", errors="replace")
                    base = patch.get("source_url") or (r["source_url"] or "")
                    found = fetcher._from_html(html, base)
                except Exception as e:
                    samples.append(f"  parse fail id={oid}: {e}")
                    found = {}
                new_crime = (found.get("crime") or "").strip()
                if new_crime and not is_demographic_crime_junk(new_crime):
                    patch["crime"] = new_crime
                    patch["offense_description"] = new_crime
                    if len(new_crime) < 120:
                        patch["offense_type"] = new_crime
                    else:
                        patch["offense_type"] = new_crime[:120]
                    crime_fixed += 1
                elif junk:
                    # Clear junk rather than leave wrong card text
                    patch["crime"] = None
                    patch["offense_description"] = None
                    patch["offense_type"] = None
                    crime_fixed += 1
                # Fix address if it was set to table header "Type"
                if (r["address"] or "").strip().lower() in ("type", "row", "live"):
                    addr = (found.get("address") or "").strip()
                    if addr and addr.lower() not in ("type", "row", "live"):
                        patch["address"] = addr
            elif junk:
                patch["crime"] = None
                patch["offense_description"] = None
                patch["offense_type"] = None
                crime_fixed += 1

        if not patch:
            continue
        updated += 1
        name = r["full_name"] or ""
        if "AMAYA" in name.upper() or len(samples) < 12:
            samples.append(
                f"  id={oid} {name!r}: "
                + ", ".join(f"{k}={v!r}"[:80] for k, v in patch.items())
            )
        if not dry:
            cols = ", ".join(f"{k} = ?" for k in patch)
            conn.execute(
                f"UPDATE offenders SET {cols} WHERE id = ?",
                (*patch.values(), oid),
            )

    if not dry:
        conn.commit()
    fetcher.close()
    conn.close()
    print(f"{'DRY ' if dry else ''}rows touched={updated} url_fixes={url_fixed} crime_fixes={crime_fixed}")
    for s in samples:
        print(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
