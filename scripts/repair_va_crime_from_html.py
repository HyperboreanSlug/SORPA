"""Backfill VA crime from archived vspsor.com HTML (card-header offenses).

All VA rows previously had race/photo but empty crime because titles live in
``.card-header.gold`` cards that the generic table parser missed — so enrich
never marked them complete.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scraper.reports.fetcher import ReportFetcher  # noqa: E402
from scraper.reports.fetcher_crime import is_demographic_crime_junk  # noqa: E402

DB = ROOT / "data" / "offenders.db"


def _resolve_html(rel: str) -> Optional[Path]:
    if not rel:
        return None
    for cand in (Path(rel), ROOT / rel, ROOT / rel.replace("\\", "/")):
        if cand.is_file():
            return cand
    return None


def _patch_sources(raw: Optional[str], crime: str) -> Optional[str]:
    if not raw:
        return None
    try:
        sources = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(sources, list):
        return None
    changed = False
    for src in sources:
        if not isinstance(src, dict):
            continue
        fields = src.get("fields")
        if not isinstance(fields, dict):
            continue
        if src.get("type") not in ("report_html", "nsopw") and "report" not in str(
            src.get("origin") or ""
        ):
            continue
        for k in ("crime", "offense_description"):
            if fields.get(k) != crime:
                fields[k] = crime
                changed = True
    return json.dumps(sources, ensure_ascii=False) if changed else None


def main() -> int:
    dry = "--dry-run" in sys.argv
    limit = 0
    for a in sys.argv[1:]:
        if a.startswith("--limit="):
            limit = int(a.split("=", 1)[1])

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, full_name, crime, offense_description, offense_type,
               report_html_path, source_url, sources_json
        FROM offenders
        WHERE (source_state = 'VA' OR source_url LIKE '%vspsor%')
          AND (crime IS NULL OR TRIM(crime) = '')
          AND report_html_path IS NOT NULL AND TRIM(report_html_path) != ''
        ORDER BY id
        """
    ).fetchall()
    if limit > 0:
        rows = rows[:limit]

    fetcher = ReportFetcher(delay=0)
    updated = 0
    samples: list[str] = []
    try:
        for r in rows:
            hp = _resolve_html(r["report_html_path"] or "")
            if hp is None:
                continue
            try:
                html = hp.read_text(encoding="utf-8", errors="replace")
                found = fetcher._from_html(html, r["source_url"] or "")
            except Exception as e:
                if len(samples) < 5:
                    samples.append(f"  parse fail id={r['id']}: {e}")
                continue
            crime = (found.get("crime") or "").strip()
            if not crime or is_demographic_crime_junk(crime):
                continue
            patch: Dict[str, Any] = {
                "crime": crime,
                "offense_description": crime,
                "offense_type": crime if len(crime) < 120 else crime[:120],
            }
            src = _patch_sources(r["sources_json"], crime)
            if src:
                patch["sources_json"] = src
            updated += 1
            if len(samples) < 8:
                samples.append(
                    f"  id={r['id']} {r['full_name']!r}: {crime[:90]!r}"
                )
            if not dry:
                cols = ", ".join(f"{k} = ?" for k in patch)
                conn.execute(
                    f"UPDATE offenders SET {cols} WHERE id = ?",
                    (*patch.values(), int(r["id"])),
                )
    finally:
        fetcher.close()

    if not dry:
        conn.commit()
    print(
        f"{'DRY ' if dry else ''}updated={updated} candidates={len(rows)}"
    )
    for s in samples:
        print(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
