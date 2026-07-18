"""Statute number → charge label expansion (bare statute dumps)."""
from __future__ import annotations

import unittest

from scraper.crime_summary import summarize_crime
from scraper.statute_ref import expand_statutes, labels_for_statute_text, lookup_statute


class StatuteRefTests(unittest.TestCase):
    def test_ne_28_320_01_gallegos(self):
        """DAVID GALLEGOS NE: Statute Number(s): 28-320.01 → real charge."""
        raw = "Statute Number(s): 28-320.01"
        self.assertEqual(lookup_statute("28-320.01"), "Sexual assault of a child")
        self.assertEqual(
            expand_statutes(raw),
            "Sexual assault of a child",
        )
        out = summarize_crime(raw)
        self.assertEqual(out, "Sexual assault of a child")
        self.assertNotIn("28-320", out)
        self.assertNotIn("Statute", out)

    def test_ne_attempt_plus_charge(self):
        raw = "Statute Number(s): 28-201 28-319(1)(c)"
        labs = labels_for_statute_text(raw)
        self.assertEqual(len(labs), 1)
        self.assertTrue(labs[0].casefold().startswith("attempted"))
        self.assertIn("sexual assault", labs[0].casefold())
        out = summarize_crime(raw)
        self.assertIn("attempted", out.casefold())
        self.assertNotIn("28-201", out)
        self.assertNotIn("Statute number", out.casefold())

    def test_subsection_fallback(self):
        # (3) subsection should still resolve via 28-320.01 base
        self.assertEqual(
            lookup_statute("28-320.01(3)"),
            "Sexual assault of a child",
        )

    def test_never_leave_statute_number_garbage(self):
        out = summarize_crime("Statute Number(s): 28-320(3)")
        self.assertNotEqual(out, "")
        self.assertNotIn("28", out)  # no bare statute crumbs
        self.assertIn("sexual assault", out.casefold())


if __name__ == "__main__":
    unittest.main()
