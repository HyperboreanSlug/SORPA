"""Multi-source provenance: FL race W + CO Asian both retained."""
from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from scraper.database import Database
from scraper.database.sources import (
    attach_source_to_record,
    extract_tracked_fields,
    make_source,
    multi_source_display,
    parse_sources,
)
from scraper.nsopw.builder import NSOPWEthnicDatabaseBuilder


class MultiSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")

    def tearDown(self) -> None:
        self.db.close()

    def test_fl_and_co_race_both_kept(self) -> None:
        fl = {
            "first_name": "NIRAJ",
            "last_name": "PATEL",
            "race": "W",
            "height": "600",
            "weight": "202",
            "eye_color": "Brown",
            "gender": "M",
            "external_id": "120472",
            "source_state": "FL",
        }
        src_fl = make_source(
            source_type="csv_bulk",
            jurisdiction="FL",
            origin="fl_sor",
            external_id="120472",
            fields=extract_tracked_fields(fl),
            html_status="no_url",
        )
        attach_source_to_record(fl, src_fl)
        rid = self.db.insert_offender(fl)
        row = self.db.get_offender_by_id(rid)
        self.assertIsNotNone(row)
        assert row is not None
        row["state"] = "CO"
        row["source_url"] = (
            "https://apps.colorado.gov/apps/dps/sor/search/search-detail.jsf?id=xx40592092"
        )
        NSOPWEthnicDatabaseBuilder._merge_demographics(
            object(),
            row,
            {
                "report_fetch_ok": True,
                "race": "Asian or Pacific Islander",
                "gender": "M",
                "report_html_path": "data/report_pages/CO/x.html",
                "report_url": row["source_url"],
                "report_final_url": row["source_url"],
                "report_fetch_status": 200,
            },
        )
        srcs = parse_sources(row["sources_json"])
        self.assertEqual(len(srcs), 2)
        races = {(s.get("jurisdiction"), (s.get("fields") or {}).get("race")) for s in srcs}
        self.assertIn(("FL", "W"), races)
        self.assertIn(("CO", "Asian or Pacific Islander"), races)
        disp = multi_source_display(srcs, "race")
        self.assertIn("W", disp)
        self.assertIn("Asian", disp)
        self.assertIn("FL", disp)
        self.assertIn("CO", disp)

    def test_import_csv_merges_sources(self) -> None:
        # Same person: matching DOB + middle initial required (not name+height alone)
        rid = self.db.insert_offender(
            {
                "first_name": "NIRAJ",
                "middle_name": "V",
                "last_name": "PATEL",
                "race": "Asian or Pacific Islander",
                "date_of_birth": "1978-01-09",
                "state": "FL",
                "height": "600",
                "weight": "202",
                "external_id": "120472",
                "source_url": "https://offender.fdle.state.fl.us/offender/sops/flyer.jsf?personId=120472",
            }
        )
        td = Path(tempfile.mkdtemp())
        p = td / "fl_sor.csv"
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "FIRST_NAME", "MIDDLE_NAME", "LAST_NAME", "RACE", "HEIGHT", "WEIGHT",
                    "EYE_COLOR", "PERSON_NBR", "SEX", "BIRTH_DATE",
                ],
            )
            w.writeheader()
            w.writerow(
                {
                    "FIRST_NAME": "NIRAJ",
                    "MIDDLE_NAME": "V",
                    "LAST_NAME": "PATEL",
                    "RACE": "W",
                    "HEIGHT": "600",
                    "WEIGHT": "202",
                    "EYE_COLOR": "Brown",
                    "PERSON_NBR": "120472",
                    "SEX": "M",
                    "BIRTH_DATE": "01/09/1978",
                }
            )
        result = self.db.import_csv(str(p), state="FL", merge_sources=True)
        self.assertEqual(result["merged"], 1)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(
            self.db._conn.execute("SELECT COUNT(*) FROM offenders").fetchone()[0],
            1,
        )
        row = self.db.get_offender_by_id(rid)
        assert row is not None
        srcs = parse_sources(row.get("sources_json"))
        self.assertGreaterEqual(len(srcs), 1)
        self.assertTrue(
            any((s.get("fields") or {}).get("race") == "W" for s in srcs)
            or "W" in str(row.get("race") or "")
        )

    def test_backfill_sources_tags_letter_race(self) -> None:
        rid = self.db.insert_offender(
            {
                "first_name": "TEST",
                "last_name": "PERSON",
                "race": "W",
                "height": "511",
                "weight": "180",
                "eye_color": "Brown",
            }
        )
        # Clear sources if insert tagged somehow
        self.db.update_offender(rid, {"sources_json": None})
        out = self.db.backfill_sources(only_missing=True)
        self.assertGreaterEqual(out["updated"], 1)
        row = self.db.get_offender_by_id(rid)
        assert row is not None
        srcs = parse_sources(row.get("sources_json"))
        self.assertGreaterEqual(len(srcs), 1)
        self.assertEqual(srcs[0].get("type"), "csv_bulk")


if __name__ == "__main__":
    unittest.main()
