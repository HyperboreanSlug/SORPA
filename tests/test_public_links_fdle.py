"""FDLE openable URL: never send users to error404 for dead personId flyers."""
from __future__ import annotations

import json
import unittest

from scraper.public_links import (
    FL_FDLE_SEARCH_HOME,
    openable_url_for_record,
    resolve_public_source_url,
)


class FdleOpenableUrlTests(unittest.TestCase):
    def test_error404_url_falls_back_to_search(self):
        url = resolve_public_source_url(
            "https://offender.fdle.state.fl.us/offender/error/error404.jsf",
            state="FL",
        )
        self.assertEqual(url, FL_FDLE_SEARCH_HOME)

    def test_blocked_http_404_skips_dead_flyer(self):
        """Carlos Gabriel Ramirez-style: PERSON_NBR used as personId → 404."""
        rec = {
            "first_name": "Carlos",
            "middle_name": "Gabriel",
            "last_name": "Ramirez",
            "state": "FL",
            "source_state": "YY | FL",
            "source_url": (
                "https://offender.fdle.state.fl.us/offender/sops/flyer.jsf"
                "?personId=19184"
            ),
            "external_id": "19184",
            "flags": json.dumps(["multi_source", "blocked:http_404"]),
            "sources_json": json.dumps(
                [
                    {
                        "html_status": "blocked:http_404",
                        "source_url": (
                            "https://offender.fdle.state.fl.us/offender/"
                            "error/error404.jsf"
                        ),
                    }
                ]
            ),
        }
        out = openable_url_for_record(rec)
        self.assertEqual(out, FL_FDLE_SEARCH_HOME)
        self.assertNotIn("personId=19184", out)
        self.assertNotIn("error404", out)

    def test_live_flyer_unchanged_without_404_flag(self):
        rec = {
            "state": "FL",
            "source_url": (
                "https://offender.fdle.state.fl.us/offender/sops/flyer.jsf"
                "?personId=139323"
            ),
            "flags": json.dumps(["photo_archived"]),
        }
        out = openable_url_for_record(rec)
        self.assertIn("personId=139323", out)
        self.assertIn("flyer.jsf", out)

    def test_identity_mismatch_never_opens_wrong_flyer(self):
        """Jorge Quintana-style: PERSON_NBR flyer is Eugene Williams — refuse."""
        rec = {
            "full_name": "Jorge Quintana",
            "first_name": "Jorge",
            "last_name": "Quintana",
            "state": "FL",
            "source_state": "FL",
            "source_url": (
                "https://offender.fdle.state.fl.us/offender/sops/flyer.jsf"
                "?personId=37181"
            ),
            "external_id": "37181",
            "flags": json.dumps(
                [
                    "identity_html_mismatch",
                    "identity:name_mismatch:EUGENE WILLIAMS",
                    "photo_archived",
                ]
            ),
            "sources_json": json.dumps(
                [
                    {
                        "html_status": "identity:name_mismatch:EUGENE WILLIAMS",
                        "source_url": (
                            "https://offender.fdle.state.fl.us/offender/sops/"
                            "flyer.jsf?personId=37181"
                        ),
                    }
                ]
            ),
        }
        out = openable_url_for_record(rec)
        self.assertEqual(out, FL_FDLE_SEARCH_HOME)
        self.assertNotIn("personId=37181", out)
        self.assertNotIn("37181", out)


if __name__ == "__main__":
    unittest.main()
