"""Michigan mspsor.com openable URL canonicalization."""
from __future__ import annotations

import unittest

from scraper.public_links import (
    MI_MSPSOR_DETAILS_BASE,
    MI_MSPSOR_SEARCH_HOME,
    extract_mspsor_offender_id,
    normalize_mspsor_url,
    openable_url_for_record,
    resolve_public_source_url,
)
from scraper.database import Database


_OID = "4a603723-4995-4708-bc05-d6e69f0a2f82"


class MspsorUrlTests(unittest.TestCase):
    def test_query_form_to_path(self):
        legacy = f"https://mspsor.com/Home/OffenderDetails?id={_OID}"
        out = normalize_mspsor_url(legacy)
        self.assertEqual(out, f"{MI_MSPSOR_DETAILS_BASE}/{_OID}")

    def test_lowercase_path_restored(self):
        low = f"https://mspsor.com/home/offenderdetails/{_OID}"
        out = normalize_mspsor_url(low)
        self.assertEqual(out, f"{MI_MSPSOR_DETAILS_BASE}/{_OID}")
        self.assertIn("/Home/OffenderDetails/", out)

    def test_www_stripped_to_apex(self):
        u = f"https://www.mspsor.com/Home/OffenderDetails?id={_OID}"
        out = normalize_mspsor_url(u)
        self.assertTrue(out.startswith("https://mspsor.com/Home/OffenderDetails/"))
        self.assertIn(_OID, out)

    def test_extract_id(self):
        self.assertEqual(
            extract_mspsor_offender_id(
                f"https://mspsor.com/Home/OffenderDetails?id={_OID}"
            ),
            _OID,
        )
        self.assertEqual(
            extract_mspsor_offender_id(
                f"https://mspsor.com/Home/OffenderDetails/{_OID}"
            ),
            _OID,
        )

    def test_resolve_prefers_mspsor_when_state_mi(self):
        multi = (
            "https://appsdoc.wi.gov/public/captcha | "
            f"https://mspsor.com/Home/OffenderDetails?id={_OID}"
        )
        out = resolve_public_source_url(multi, state="MI")
        self.assertEqual(out, f"{MI_MSPSOR_DETAILS_BASE}/{_OID}")

    def test_mi_without_mspsor_falls_back_to_search(self):
        out = resolve_public_source_url(
            "https://appsdoc.wi.gov/public/captcha",
            state="MI",
        )
        self.assertEqual(out, MI_MSPSOR_SEARCH_HOME)

    def test_openable_for_record(self):
        rec = {
            "source_state": "MI",
            "state": "MI",
            "source_url": f"https://mspsor.com/Home/OffenderDetails?id={_OID}",
        }
        self.assertEqual(
            openable_url_for_record(rec),
            f"{MI_MSPSOR_DETAILS_BASE}/{_OID}",
        )

    def test_identity_normalize_canonical(self):
        legacy = f"https://mspsor.com/Home/OffenderDetails?id={_OID}"
        norm = Database.normalize_identity_url(legacy)
        self.assertEqual(norm, f"{MI_MSPSOR_DETAILS_BASE}/{_OID}")
        self.assertNotIn("?id=", norm)


if __name__ == "__main__":
    unittest.main()
