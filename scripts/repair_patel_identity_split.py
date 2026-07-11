#!/usr/bin/env python3
"""
Split incorrectly merged NIRAJ PATEL (FL bulk + CO registry) into two rows.

FL:  NIRAJ V PATEL, DOB 1978-01-09, PERSON_NBR 120472
CO:  NIRAJ (RASHMIBABU) PATEL, DOB 1973-10-29, xx40592092

Run from repo root:
  python scripts/repair_patel_identity_split.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scraper.database import Database
from scraper.database.sources import (
    apply_sources_to_record,
    dumps_sources,
    make_source,
    parse_sources,
)


def main() -> int:
    db = Database(str(ROOT / "data" / "offenders.db"))
    try:
        row = db._conn.execute(
            "SELECT * FROM offenders WHERE id = 83836"
        ).fetchone()
        if not row:
            # fallback search
            row = db._conn.execute(
                """
                SELECT * FROM offenders
                WHERE UPPER(last_name)='PATEL'
                  AND UPPER(first_name) LIKE 'NIRAJ%'
                  AND source_url LIKE '%xx40592092%'
                LIMIT 1
                """
            ).fetchone()
        if not row:
            print("CO NIRAJ PATEL row not found")
            return 1
        co = dict(row)
        rid = int(co["id"])
        print(f"Found CO chimera id={rid} race={co.get('race')!r}")

        # Strip FL bulk source entries from sources_json
        sources = parse_sources(co.get("sources_json"))
        kept = []
        for s in sources:
            origin = str(s.get("origin") or "").lower()
            lab = str(s.get("label") or "").lower()
            jur = str(s.get("jurisdiction") or "").upper()
            if jur == "FL" or "fl_sor" in origin or "fl sor" in lab:
                print(f"  drop source: {s.get('id')} {s.get('label')}")
                continue
            kept.append(s)
        co["sources_json"] = dumps_sources(kept)
        # Prefer CO race empty until HTML verify — remove lone W from FL
        if (co.get("race") or "").strip().upper() in ("W", "WHITE"):
            # only clear if we dropped an FL source that owned it
            co["race"] = None
        apply_sources_to_record(co)
        # Ensure CO identity fields
        patch = {
            "sources_json": co.get("sources_json"),
            "race": co.get("race"),
            "flags": co.get("flags"),
            "middle_name": co.get("middle_name") or "RASHMIBABU",
            "source_state": "CO",
            "state": "CO",
        }
        # Do not keep FL-only DOB if sources said conflict — CO DOB already 1973
        db.update_offender(rid, patch)
        print(f"  updated CO row id={rid} middle={patch['middle_name']}")

        # Insert FL person if missing
        exists = db._conn.execute(
            "SELECT id FROM offenders WHERE external_id = ? OR "
            "external_id = ? OR source_url LIKE ?",
            ("120472", "fl:120472", "%personId=120472%"),
        ).fetchone()
        if exists:
            print(f"  FL row already exists id={exists[0]}")
        else:
            fl = {
                "first_name": "NIRAJ",
                "middle_name": "V",
                "last_name": "PATEL",
                "full_name": "NIRAJ V PATEL",
                "race": "W",
                "gender": "M",
                "date_of_birth": "1978-01-09",
                "height": "600",
                "weight": "202",
                "eye_color": "Brown",
                "hair_color": "Black",
                "city": "Cobden",
                "state": "IL",
                "source_state": "FL",
                "zip_code": "62920",
                "external_id": "120472",
                "photo_url": (
                    "https://offender.fdle.state.fl.us/offender/CallImage?imgID=4163580"
                ),
                "source_url": (
                    "https://offender.fdle.state.fl.us/offender/sops/flyer.jsf?personId=120472"
                ),
            }
            src = make_source(
                source_type="csv_bulk",
                jurisdiction="FL",
                origin="fl_sor",
                label="FL SOR CSV",
                external_id="120472",
                source_url=fl["source_url"],
                fields={
                    k: fl[k]
                    for k in (
                        "race", "gender", "date_of_birth", "height", "weight",
                        "eye_color", "hair_color", "city", "state", "zip_code",
                    )
                },
                html_status="pending",
            )
            from scraper.database.sources import attach_source_to_record

            attach_source_to_record(fl, src)
            new_id = db.insert_offender(fl)
            print(f"  inserted FL NIRAJ V PATEL id={new_id}")

        # Verify no merge path would re-collapse
        from scraper.database.identity import should_merge_records

        fl_row = dict(
            db._conn.execute(
                "SELECT * FROM offenders WHERE external_id='120472' LIMIT 1"
            ).fetchone()
        )
        co_row = db.get_offender_by_id(rid)
        ok, sc, reasons = should_merge_records(fl_row, co_row)
        print(f"  would_merge={ok} score={sc} reasons={reasons}")
        assert not ok, "identity match still merges FL into CO!"
        print("OK split complete")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
