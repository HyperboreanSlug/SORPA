"""Deported detection and LISTED banner formatting."""
from __future__ import annotations

import unittest

from gui_app.shared.deported import (
    format_export_race_label,
    format_listed_banner,
    is_deported,
)


class DeportedTests(unittest.TestCase):
    def test_detects_address_and_city(self):
        self.assertTrue(
            is_deported({"address": "DEPORTED TO MEXICO", "city": "UNKNOWN"})
        )
        self.assertTrue(is_deported({"city": "DEPORTED"}))
        self.assertTrue(is_deported({"address": "000 DEPORTED ST"}))
        self.assertTrue(is_deported({"address": "UNKNOWN - DEPORTED"}))
        self.assertFalse(is_deported({"address": "123 MAIN ST", "city": "MIAMI"}))
        self.assertFalse(is_deported({}))

    def test_banner_block_letters(self):
        rec = {"address": "DEPORTED TO MEXICO"}
        self.assertEqual(
            format_listed_banner("White", rec),
            "LISTED WHITE - DEPORTED",
        )
        self.assertEqual(
            format_listed_banner("White", {"address": "1 MAIN ST"}),
            "LISTED WHITE",
        )
        self.assertEqual(
            format_listed_banner("White ✓", rec),
            "LISTED WHITE - DEPORTED",
        )
        self.assertEqual(
            format_listed_banner("", rec),
            "LISTED - DEPORTED",
        )

    def test_export_race_label(self):
        rec = {"address": "DEPORTED TO MEXICO"}
        # Export cards never show "deported" — cleaned race only.
        self.assertEqual(format_export_race_label("White", rec), "WHITE")
        self.assertEqual(format_export_race_label("Black", rec), "BLACK")
        self.assertEqual(
            format_export_race_label("White", {"address": "1 MAIN ST"}),
            "WHITE",
        )
        self.assertEqual(format_export_race_label("", rec), "")


if __name__ == "__main__":
    unittest.main()
