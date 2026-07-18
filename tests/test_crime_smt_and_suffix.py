"""SMT-as-crime rejection + NSOPW Jr suffix + MI statute card parse."""
from __future__ import annotations

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from scraper.nsopw.client_parse import NSOPWClientParseMixin
from scraper.reports.fetcher import ReportFetcher
from scraper.reports.fetcher_crime import (
    extract_statute_card_offenses,
    is_demographic_crime_junk,
    is_smt_description_junk,
)
from scraper.reports.identity_gate import extract_person_name_from_html

_ROOT = Path(__file__).resolve().parents[1]
_MI_HTML = _ROOT / "data" / "report_pages" / "MI" / "868276d7aa9c25ee.html"

_SMT_CRIME = (
    "D.J., CENTER-ATX: STAR WITH FACE INSIDE | RIGHT: UPPER SIDE-DOT | "
    "LEFT CENTER: DIRTY SOUTH | RIGHT CENTER: LONESTAR HUSTLER"
)
# Guadalupe Alvarado-style MI Description column (tattoo, not charge)
_GUADALUPE_SMT = "OUTLINED CROSS WITH POINTED TIPS IN BLACK INK; ARM, LEFT, LOWER"

_MINI_MI = """
<html><body>
  <h2 class="text-primary text-nowrap">DAVID  AGUILAR JR</h2>
  <div class="card-header">
    <span>750.520C1A - CRIMINAL SEXUAL CONDUCT 2ND DEGREE (PERSON UNDER 13)</span>
  </div>
  <table>
    <tr><th>Type</th><th>Location</th><th>Description</th></tr>
    <tr><td>TATTOOS</td><td>NECK</td><td>LEFT CENTER: DIRTY SOUTH</td></tr>
    <tr><td>TATTOOS</td><td>NECK</td><td>RIGHT CENTER: LONESTAR HUSTLER</td></tr>
  </table>
</body></html>
"""


class _P(NSOPWClientParseMixin):
    pass


class SmtCrimeAndSuffixTests(unittest.TestCase):
    def test_smt_junk_detected(self):
        self.assertTrue(is_smt_description_junk(_SMT_CRIME))
        self.assertTrue(is_demographic_crime_junk(_SMT_CRIME))
        self.assertTrue(is_smt_description_junk(_GUADALUPE_SMT))
        self.assertTrue(is_demographic_crime_junk(_GUADALUPE_SMT))
        self.assertTrue(is_smt_description_junk("ARM, LEFT"))
        self.assertTrue(is_smt_description_junk("ARM, LEFT; ARM, RIGHT"))
        self.assertTrue(
            is_smt_description_junk("ABDOMEN; EAR, LEFT; ARM, LEFT, LOWER")
        )
        self.assertTrue(is_smt_description_junk("4-5 INCH SCAR"))
        self.assertFalse(
            is_demographic_crime_junk(
                "750.520C1A - CRIMINAL SEXUAL CONDUCT 2ND DEGREE (PERSON UNDER 13)"
            )
        )
        # Section label glued onto a real charge must NOT be treated as junk
        self.assertFalse(
            is_demographic_crime_junk(
                "Scars, Marks and Tattoos — Forcible Rape * , More Information"
            )
        )
        self.assertFalse(
            is_smt_description_junk(
                "Scars, Marks and Tattoos — Possession Of Child Pornography"
            )
        )

    def test_guadalupe_alvarado_smt_not_summarized_as_crime(self):
        from scraper.crime_summary import summarize_crime

        self.assertEqual(summarize_crime(_GUADALUPE_SMT), "")

    def test_nsopw_keeps_jr_suffix(self):
        off = _P()._parse_offender(
            {
                "name": {
                    "givenName": "DAVID",
                    "surName": "AGUILAR",
                    "suffix": "JR",
                },
                "jurisdictionId": "MI",
                "locations": [{"state": "MI", "type": "R"}],
            }
        )
        self.assertEqual(off.full_name, "DAVID AGUILAR JR")
        self.assertEqual(off.last_name, "AGUILAR JR")
        self.assertEqual(off.first_name, "DAVID")

    def test_mini_mi_parse(self):
        f = ReportFetcher.__new__(ReportFetcher)
        found = f._from_html(_MINI_MI, "https://mspsor.com/")
        crime = (found.get("crime") or "").upper()
        self.assertIn("CRIMINAL SEXUAL CONDUCT", crime)
        self.assertNotIn("DIRTY SOUTH", crime)
        self.assertNotIn("LONESTAR", crime)
        self.assertEqual(found.get("full_name"), "DAVID AGUILAR JR")

    def test_statute_card_helper(self):
        soup = BeautifulSoup(_MINI_MI, "html.parser")
        crime = extract_statute_card_offenses(soup)
        self.assertIn("520C1A", crime.upper())
        self.assertNotIn("DIRTY", crime.upper())

    @unittest.skipUnless(_MI_HTML.is_file(), "archived MI HTML not present")
    def test_live_archive_david_aguilar(self):
        html = _MI_HTML.read_text(encoding="utf-8", errors="replace")
        self.assertEqual(extract_person_name_from_html(html), "DAVID AGUILAR JR")
        f = ReportFetcher.__new__(ReportFetcher)
        found = f._from_html(html, "https://mspsor.com/")
        crime = (found.get("crime") or "").upper()
        self.assertIn("CRIMINAL SEXUAL CONDUCT", crime)
        self.assertNotIn("DIRTY SOUTH", crime)
        self.assertEqual(found.get("full_name"), "DAVID AGUILAR JR")


if __name__ == "__main__":
    unittest.main()
