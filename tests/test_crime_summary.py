"""Tests for SOR crime string summarization (Reports mode)."""
from __future__ import annotations

import unittest

from scraper.crime_summary import summarize_crime


class CrimeSummaryTests(unittest.TestCase):
    def test_fl_boilerplate_preferred_example(self):
        raw = (
            "Commission of OR Attempt, Solicit, or Conspire to Commit; "
            "Chapter 794; Sexual Battery *Excluding subsections 794.011(10); "
            "s. 800.04(4)(b); Lewd/lascivious offenses committed upon or in the "
            "presence of persons less than 16 years of age, where the victim is "
            "under 12 or the court finds the use of force or coercion.; "
            "s. 800.04(5)(c)1; Lewd/lascivious offenses committed upon or in the "
            "presence of persons less than 16 years of age, where the court finds "
            "molestation involving unclothed genitals.; s. 800.04(5)(d)"
        )
        out = summarize_crime(raw)
        self.assertEqual(
            out,
            "Sexual battery · Lewd/lascivious (under 12/force) · "
            "Lewd/lascivious (unclothed genitals)",
        )

    def test_fl_short_codes(self):
        raw = (
            "11/19/2025; SEX BAT/ WPN. OR FORCE; F.S. 794.011(3); 1909371; "
            "Miami-Dade, FL; Guilty/convict; Commission of OR Attempt, Solicit, "
            "or Conspire to Commit; Chapter 794; Sexual Battery "
            "*Excluding subsections 794.011(10)"
        )
        out = summarize_crime(raw)
        self.assertIn("Sexual battery", out)
        self.assertNotIn("Commission of", out)
        self.assertNotIn("794.011", out)
        self.assertNotIn("11/19", out)

    def test_empty(self):
        self.assertEqual(summarize_crime(""), "")
        self.assertEqual(summarize_crime(None), "")


if __name__ == "__main__":
    unittest.main()
