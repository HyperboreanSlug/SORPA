"""Online listing availability (dead URL / 404) for Reports."""
from __future__ import annotations

import json
import unittest

from scraper.online_listing import (
    UNAVAILABLE_ONLINE_LABEL,
    listing_unavailable_online,
    online_status_label,
)


class OnlineListingTests(unittest.TestCase):
    def test_jacinto_style_404_flag(self):
        rec = {
            "full_name": "Jacinto Calderon",
            "source_url": (
                "https://offender.fdle.state.fl.us/offender/sops/flyer.jsf"
                "?personId=17757"
            ),
            "flags": json.dumps(
                [
                    "multi_source",
                    "blocked:http_404",
                    "identity_html_mismatch",
                ]
            ),
        }
        self.assertTrue(listing_unavailable_online(rec))
        self.assertEqual(online_status_label(rec), UNAVAILABLE_ONLINE_LABEL)

    def test_live_listing_ok(self):
        rec = {
            "source_url": (
                "https://offender.fdle.state.fl.us/offender/sops/flyer.jsf"
                "?personId=139323"
            ),
            "flags": json.dumps(["photo_archived", "html_archived"]),
        }
        self.assertFalse(listing_unavailable_online(rec))
        self.assertEqual(online_status_label(rec), "")

    def test_error404_url(self):
        rec = {
            "source_url": (
                "https://offender.fdle.state.fl.us/offender/error/error404.jsf"
            ),
        }
        self.assertTrue(listing_unavailable_online(rec))

    def test_sources_json_html_status(self):
        rec = {
            "source_url": "https://example.com/offender/1",
            "sources_json": json.dumps(
                [{"html_status": "blocked:http_404", "source_url": "https://x/1"}]
            ),
        }
        self.assertTrue(listing_unavailable_online(rec))

    def test_empty_record(self):
        self.assertFalse(listing_unavailable_online(None))
        self.assertFalse(listing_unavailable_online({}))


if __name__ == "__main__":
    unittest.main()
