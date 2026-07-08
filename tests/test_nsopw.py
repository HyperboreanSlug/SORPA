"""Unit tests for NSOPW client parsing and ethnic builder surname selection."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.nsopw_client import NSOPWClient, NSOPWOffender
from scraper.nsopw_builder import NSOPWEthnicDatabaseBuilder
from scraper.report_fetcher import ReportFetcher


class NSOPWParseTests(unittest.TestCase):
    def test_parse_offender(self):
        client = NSOPWClient(delay=0)
        raw = {
            "name": {"givenName": "JUAN", "middleName": "A", "surName": "GARCIA"},
            "aliases": [{"givenName": "JOHN", "surName": "GARCIA"}],
            "gender": "M",
            "dob": "1980-05-01T00:00:00",
            "age": 45,
            "locations": [
                {
                    "type": "R",
                    "streetAddress": "123 MAIN ST",
                    "city": "MIAMI",
                    "state": "FL",
                    "zipCode": "33101",
                    "latitude": 25.7,
                    "longitude": -80.2,
                }
            ],
            "offenderUri": "https://example.gov/report/1",
            "imageUri": "https://example.gov/photo/1",
            "absconder": False,
            "jurisdictionId": "FL",
        }
        off = client._parse_offender(raw)
        self.assertEqual(off.first_name, "JUAN")
        self.assertEqual(off.last_name, "GARCIA")
        self.assertEqual(off.state, "FL")
        self.assertEqual(off.offender_uri, "https://example.gov/report/1")
        rec = off.to_record()
        self.assertEqual(rec["source_url"], "https://example.gov/report/1")
        self.assertEqual(rec["gender"], "M")
        client.close()

    def test_search_requires_both_names(self):
        client = NSOPWClient(delay=0)
        with self.assertRaises(ValueError):
            client.search_by_name("", "Garcia")
        client.close()


class ReportFetcherTests(unittest.TestCase):
    def test_html_label_extraction(self):
        html = """
        <html><body>
        <table>
          <tr><th>Race</th><td>White</td></tr>
          <tr><th>Height</th><td>5'10\"</td></tr>
          <tr><th>Hair Color</th><td>Brown</td></tr>
        </table>
        <p>Ethnicity: Hispanic</p>
        </body></html>
        """
        fetcher = ReportFetcher(delay=0)
        data = fetcher._from_html(html)
        self.assertEqual(data.get("race"), "White")
        self.assertEqual(data.get("height"), "5'10\"")
        self.assertIn(data.get("ethnicity"), ("Hispanic", "Hispanic"))
        fetcher.close()


class BuilderSurnameTests(unittest.TestCase):
    def test_hispanic_surnames_selected(self):
        b = NSOPWEthnicDatabaseBuilder(db_path=":memory:", delay=1.5, report_delay=1.5)
        try:
            pairs = b.surnames_for_ethnicity("hispanic", limit_per_group=5)
            self.assertTrue(len(pairs) >= 1)
            self.assertTrue(all(label == "Hispanic" for _, label in pairs))
            # Floor enforced on delays
            self.assertGreaterEqual(b.search_limiter.min_interval, 1.5)
        finally:
            b.close()

    def test_report_html_column_exists(self):
        from scraper.database import Database
        db = Database(":memory:")
        try:
            cols = {row[1] for row in db._conn.execute("PRAGMA table_info(offenders)")}
            self.assertIn("report_html_path", cols)
            rid = db.insert_offender({
                "first_name": "Test",
                "last_name": "User",
                "source_url": "https://example.gov/r/1",
                "report_html_path": "data/report_pages/TX/abc.html",
            })
            self.assertEqual(rid, 1)
            row = db._conn.execute(
                "SELECT report_html_path FROM offenders WHERE id=1"
            ).fetchone()
            self.assertEqual(row["report_html_path"], "data/report_pages/TX/abc.html")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
