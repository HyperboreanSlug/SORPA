"""HTML identity gate — never attach wrong-person flyer data."""
from __future__ import annotations

import json
import unittest

from scraper.reports.identity_gate import (
    demo_identity_ok,
    record_name_matches_html,
    split_html_display_name,
)


class IdentityGateTests(unittest.TestCase):
    def test_ossiel_rejects_jose_triana(self):
        from scraper.reports.identity_gate import extract_person_name_from_html

        rec = {
            "first_name": "Ossiel",
            "last_name": "Zuniga",
            "date_of_birth": "01/06/1975",
        }
        self.assertFalse(record_name_matches_html(rec, "Jose Triana"))
        self.assertTrue(record_name_matches_html(rec, "Ossiel Zuniga"))
        # Map chrome must never be treated as a person name
        junk = (
            '<span style="font-weight: bold">This map plots out the location '
            "of the selected offender or predator.</span>"
            '<span style="font-weight: bold">Ossiel Zuniga</span>'
        )
        self.assertEqual(extract_person_name_from_html(junk), "Ossiel Zuniga")

    def test_dob_mismatch_is_nuclear_reject(self):
        rec = {
            "first_name": "Ossiel",
            "last_name": "Zuniga",
            "date_of_birth": "01/06/1975",
        }
        ok, reason = demo_identity_ok(
            rec,
            {
                "report_fetch_ok": True,
                "full_name": "Ossiel Zuniga",
                "date_of_birth": "03/03/1961",
                "race": "White",
            },
        )
        self.assertFalse(ok)
        self.assertIn("dob_mismatch", reason)

    def test_antonio_accepts_full_name(self):
        rec = {
            "first_name": "ANTONIO",
            "middle_name": "DARRELL",
            "last_name": "JACKSON",
        }
        self.assertTrue(
            record_name_matches_html(rec, "ANTONIO DARRELL JACKSON")
        )
        self.assertFalse(record_name_matches_html(rec, "Jose Triana"))

    def test_jr_suffix_matches(self):
        rec = {"first_name": "ROBERT", "middle_name": "CHARLES", "last_name": "BEDFORD"}
        self.assertTrue(
            record_name_matches_html(rec, "ROBERT CHARLES BEDFORD JR")
        )
        rec2 = {"first_name": "BRUCE", "middle_name": "EDWARD", "last_name": "CHEESMAN"}
        self.assertTrue(
            record_name_matches_html(rec2, "BRUCE EDWARD CHEESMAN JR")
        )

    def test_split_last_first(self):
        f, m, l = split_html_display_name("JACKSON, ANTONIO DARRELL")
        self.assertEqual(f.upper(), "ANTONIO")
        self.assertEqual(l.upper(), "JACKSON")

    def test_demo_identity_ok_mismatch_reason(self):
        rec = {"first_name": "Ossiel", "last_name": "Zuniga"}
        ok, reason = demo_identity_ok(
            rec,
            {
                "report_fetch_ok": True,
                "full_name": "Jose Triana",
                "race": "White",
            },
        )
        self.assertFalse(ok)
        self.assertIn("name_mismatch", reason)

    def test_merge_demographics_rejects_wrong_person(self):
        from scraper.nsopw.builder import NSOPWEthnicDatabaseBuilder
        from scraper.database.sources import parse_sources

        rec = {
            "first_name": "Ossiel",
            "last_name": "Zuniga",
            "race": "W",
            "external_id": "5478",
            "source_url": (
                "https://offender.fdle.state.fl.us/offender/sops/"
                "flyer.jsf?personId=5478"
            ),
            "state": "FL",
        }
        NSOPWEthnicDatabaseBuilder._merge_demographics(
            object(),
            rec,
            {
                "report_fetch_ok": True,
                "full_name": "Jose Triana",
                "race": "White",
                "gender": "Male",
                "report_html_path": "data/report_pages/FL/fake.html",
                "report_url": rec["source_url"],
                "report_final_url": rec["source_url"],
                "report_fetch_status": 200,
            },
        )
        # Must not mark html-verified White from Jose onto Ossiel
        self.assertNotIn("✓", str(rec.get("race") or ""))
        srcs = parse_sources(rec.get("sources_json"))
        for s in srcs:
            if s.get("type") == "report_html":
                self.assertFalse(s.get("html_verified"))
        self.assertIn("identity_html_mismatch", str(rec.get("flags") or ""))

    def test_strip_wrong_person_clears_fdle_url_and_photo(self):
        """Poisoned PERSON_NBR flyer + CallImage must not survive strip."""
        from scraper.reports.identity_gate import strip_wrong_person_html

        rec = {
            "full_name": "Jorge Quintana",
            "first_name": "Jorge",
            "last_name": "Quintana",
            "state": "FL",
            "source_url": (
                "https://offender.fdle.state.fl.us/offender/sops/"
                "flyer.jsf?personId=37181"
            ),
            "external_id": "37181",
            "photo_path": r"data\report_pages\FL\photos\f3fa99f75da10663.jpg",
            "photo_url": (
                "https://offender.fdle.state.fl.us/offender/CallImage?imgID=108891"
            ),
            "report_html_path": None,
            "flags": json.dumps(
                [
                    "identity_html_mismatch",
                    "identity:name_mismatch:EUGENE WILLIAMS",
                ]
            ),
            "sources_json": json.dumps(
                [
                    {
                        "type": "nsopw_report",
                        "html_status": "identity:name_mismatch:EUGENE WILLIAMS",
                        "source_url": (
                            "https://offender.fdle.state.fl.us/offender/sops/"
                            "flyer.jsf?personId=37181"
                        ),
                    }
                ]
            ),
        }
        self.assertTrue(strip_wrong_person_html(rec, reason="test"))
        self.assertFalse(rec.get("source_url"))
        self.assertFalse(rec.get("photo_path"))
        self.assertFalse(rec.get("photo_url"))
        self.assertFalse(rec.get("external_id"))


if __name__ == "__main__":
    unittest.main()
