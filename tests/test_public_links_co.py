"""Colorado CBI SOR URL normalization (host + case-sensitive id)."""
from __future__ import annotations

import unittest

from scraper.public_links import openable_url_for_record, resolve_public_source_url
from scraper.public_links_co import (
    extract_co_offender_id,
    normalize_co_sor_url,
)


class CoPublicLinksTests(unittest.TestCase):
    def test_id_uppercased_and_host_rewritten(self):
        raw = (
            "https://www.colorado.gov/apps/cdps/sor/search/"
            "search-detail.jsf?id=xx55195899"
        )
        out = normalize_co_sor_url(raw)
        self.assertIn("apps.colorado.gov/apps/dps/sor", out)
        self.assertIn("id=XX55195899", out)
        self.assertIn("ext=t", out)
        self.assertNotIn("xx55195899", out)
        self.assertNotIn("cdps", out)

    def test_extract_from_external_id_blob(self):
        blob = (
            "https://www.colorado.gov/apps/cdps/sor/search/"
            "search-detail.jsf?id=XX36274113 | xx36274113"
        )
        self.assertEqual(extract_co_offender_id(blob), "XX36274113")

    def test_resolve_and_openable(self):
        raw = (
            "https://www.colorado.gov/apps/cdps/sor/search/"
            "search-detail.jsf?id=xx55195899"
        )
        got = resolve_public_source_url(raw, state="CO")
        self.assertIn("id=XX55195899", got)
        rec = {
            "state": "CO",
            "source_state": "CO",
            "source_url": "",
            "external_id": raw,
        }
        opened = openable_url_for_record(rec)
        self.assertIn("id=XX55195899", opened)
        self.assertIn("apps.colorado.gov", opened)


if __name__ == "__main__":
    unittest.main()
