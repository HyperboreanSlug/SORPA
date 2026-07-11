"""Identity matching: middle names + multi-identifier merge rules."""
from __future__ import annotations

import unittest

from scraper.database import Database
from scraper.database.identity import (
    dobs_compatible,
    middles_compatible,
    should_merge_records,
    score_identity_match,
)


class IdentityUnitTests(unittest.TestCase):
    def test_middle_v_vs_rashmibabu_conflict(self):
        self.assertIs(middles_compatible("V", "RASHMIBABU"), False)
        self.assertIs(middles_compatible("R", "RASHMIBABU"), True)
        self.assertIs(middles_compatible("Rashmi", "RASHMIBABU"), True)
        self.assertIs(middles_compatible("", "V"), None)

    def test_dob_conflict(self):
        self.assertIs(dobs_compatible("01/09/1978", "1973-10-29"), False)
        self.assertIs(dobs_compatible("1978-01-09", "01/09/1978"), True)

    def test_fl_vs_co_patel_not_merged(self):
        fl = {
            "first_name": "NIRAJ",
            "middle_name": "V",
            "last_name": "PATEL",
            "date_of_birth": "01/09/1978",
            "height": "600",
            "weight": "202",
            "external_id": "120472",
            "source_state": "FL",
        }
        co = {
            "first_name": "NIRAJ",
            "middle_name": "RASHMIBABU",
            "last_name": "PATEL",
            "date_of_birth": "1973-10-29",
            "height": "600",
            "weight": "202",
            "external_id": "xx40592092",
            "state": "CO",
        }
        ok, score, reasons = should_merge_records(fl, co)
        self.assertFalse(ok)
        self.assertIn("hard_reject", reasons)
        # Even if CO has no middle, DOB conflict still blocks
        co2 = dict(co)
        co2["middle_name"] = ""
        ok2, _, reasons2 = should_merge_records(fl, co2)
        self.assertFalse(ok2)
        self.assertTrue(any("dob" in r or "hard" in r for r in reasons2))

    def test_same_person_fl_reimport(self):
        a = {
            "first_name": "NIRAJ",
            "middle_name": "V",
            "last_name": "PATEL",
            "date_of_birth": "01/09/1978",
            "height": "600",
            "weight": "202",
            "external_id": "120472",
        }
        b = {
            "first_name": "NIRAJ",
            "middle_name": "V",
            "last_name": "PATEL",
            "date_of_birth": "1978-01-09",
            "height": "600",
            "weight": "202",
            "external_id": "120472",
        }
        ok, score, reasons = should_merge_records(a, b)
        self.assertTrue(ok)
        self.assertGreaterEqual(score, 6)

    def test_import_does_not_merge_fl_onto_co(self):
        db = Database(":memory:")
        try:
            rid = db.insert_offender(
                {
                    "first_name": "NIRAJ",
                    "middle_name": "RASHMIBABU",
                    "last_name": "PATEL",
                    "date_of_birth": "1973-10-29",
                    "height": "600",
                    "weight": "202",
                    "race": "Asian or Pacific Islander",
                    "state": "CO",
                    "source_state": "CO",
                    "external_id": "xx40592092",
                    "source_url": (
                        "https://apps.colorado.gov/apps/dps/sor/"
                        "search/search-detail.jsf?id=xx40592092"
                    ),
                }
            )
            import tempfile, csv
            from pathlib import Path

            td = Path(tempfile.mkdtemp())
            p = td / "fl_sor.csv"
            with open(p, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(
                    f,
                    fieldnames=[
                        "FIRST_NAME", "MIDDLE_NAME", "LAST_NAME", "RACE",
                        "HEIGHT", "WEIGHT", "EYE_COLOR", "PERSON_NBR",
                        "SEX", "BIRTH_DATE", "PERM_CITY", "PERM_STATE",
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
                        "PERM_CITY": "Cobden",
                        "PERM_STATE": "IL",
                    }
                )
            result = db.import_csv(str(p), state="FL", merge_sources=True)
            # Must insert new FL row, not merge into CO
            self.assertEqual(result["merged"], 0)
            self.assertEqual(result["imported"], 1)
            n = db._conn.execute("SELECT COUNT(*) FROM offenders").fetchone()[0]
            self.assertEqual(n, 2)
            co = db.get_offender_by_id(rid)
            self.assertEqual(co["external_id"], "xx40592092")
            # CO race must not become letter W from FL
            self.assertNotEqual((co.get("race") or "").strip().upper(), "W")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
