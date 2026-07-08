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

    def test_fl_border_panel_cells(self):
        """Florida FDLE flyer: alternating borderPanelCell label/value."""
        html = """
        <html><body>
        <div class="ui-g-12 ui-md-6 ui-lg-6 borderPanelCell">
          <span class="Fs16 Fright">Race: </span></div>
        <div class="ui-g-12 ui-md-6 ui-lg-6 borderPanelCell">
          <span class="Fleft black">White</span></div>
        <div class="ui-g-12 ui-md-6 ui-lg-6 borderPanelCell">
          <span class="Fs16 Fright">Sex: </span></div>
        <div class="ui-g-12 ui-md-6 ui-lg-6 borderPanelCell">
          <span class="Fleft black">Male</span></div>
        <div class="ui-g-12 ui-md-6 ui-lg-6 borderPanelCell">
          <span class="Fs16 Fright">Hair: </span></div>
        <div class="ui-g-12 ui-md-6 ui-lg-6 borderPanelCell">
          <span class="Fleft black">Black</span></div>
        </body></html>
        """
        fetcher = ReportFetcher(delay=0)
        data = fetcher._from_html(html)
        self.assertEqual(data.get("race"), "White")
        self.assertEqual(data.get("gender"), "Male")
        self.assertEqual(data.get("hair_color"), "Black")
        fetcher.close()

    def test_icrimewatch_bullet_labels(self):
        """OffenderWatch tables use bullet-prefixed labels: '• Race:'."""
        html = """
        <html><body><table>
        <tr>
          <td><strong>&bull; Race:</strong></td><td>White</td>
          <td><strong>&bull; Eyes:</strong></td><td>Brown</td>
        </tr>
        <tr>
          <td><strong>&bull; Hair:</strong></td><td>Black</td>
          <td><strong>&bull; Height:</strong></td><td>5'10\"</td>
        </tr>
        <tr>
          <td><strong>&bull; Sex:</strong></td><td>Male</td>
          <td><strong>&bull; Weight:</strong></td><td>180</td>
        </tr>
        </table></body></html>
        """
        fetcher = ReportFetcher(delay=0)
        data = fetcher._from_html(html)
        self.assertEqual(data.get("race"), "White")
        self.assertEqual(data.get("eye_color"), "Brown")
        self.assertEqual(data.get("hair_color"), "Black")
        self.assertEqual(data.get("gender"), "Male")
        fetcher.close()
    def test_resolve_icrimewatch_fwd(self):
        import base64
        target = "http://www.icrimewatch.net/offenderdetails.php?OfndrID=1&AgencyID=2"
        fwd = base64.b64encode(target.encode()).decode()
        url = f"https://sheriffalerts.com/cap_office_disclaimer.php?office=1&fwd={fwd}"
        resolved = ReportFetcher._resolve_gateway_url(url)
        self.assertEqual(resolved, target)

    def test_disclaimer_form_detection_and_post_data(self):
        html = """
        <html><body>
        <h1>Disclaimer</h1>
        <p>You must agree to the terms & conditions!</p>
        <form method="post" action="">
          <input type="hidden" name="fwd" value="abc123" />
          <input id="agree" type="checkbox" name="agree" value="1" />
          <label for="agree">I agree to the above terms &amp; conditions.</label>
          <input id="continue" type="submit" name="continue" value="Continue" />
        </form>
        </body></html>
        """
        self.assertTrue(
            ReportFetcher._looks_like_disclaimer(
                html, "https://sheriffalerts.com/cap_office_disclaimer.php?office=1"
            )
        )
        soup = __import__("bs4", fromlist=["BeautifulSoup"]).BeautifulSoup(html, "html.parser")
        form = ReportFetcher._find_disclaimer_form(soup)
        self.assertIsNotNone(form)
        data = ReportFetcher._build_disclaimer_post_data(
            form, "https://sheriffalerts.com/cap_office_disclaimer.php?office=1&fwd=abc123"
        )
        self.assertEqual(data.get("agree"), "1")
        self.assertEqual(data.get("continue"), "Continue")
        self.assertEqual(data.get("fwd"), "abc123")


class BuilderSurnameTests(unittest.TestCase):
    def test_hispanic_surnames_selected(self):
        b = NSOPWEthnicDatabaseBuilder(db_path=":memory:", delay=1.5, report_delay=0.1)
        try:
            pairs = b.surnames_for_ethnicity("hispanic", limit_per_group=5)
            self.assertTrue(len(pairs) >= 1)
            self.assertTrue(all(label == "Hispanic" for _, label in pairs))
            # Floors: search ≥2.0s, report ≥0.25s (no double-sleep on clients)
            self.assertGreaterEqual(b.search_delay, 2.0)
            self.assertGreaterEqual(b.report_delay, 0.25)
            self.assertEqual(b.client.delay, 0.0)
            self.assertEqual(b.reports.delay, 0.0)
        finally:
            b.close()

    def test_all_surnames_exceeds_cap(self):
        b = NSOPWEthnicDatabaseBuilder(db_path=":memory:", delay=2.0, report_delay=0.25)
        try:
            capped = b.surnames_for_ethnicity("hispanic", limit_per_group=3, all_surnames=False)
            all_s = b.surnames_for_ethnicity("hispanic", limit_per_group=3, all_surnames=True)
            self.assertEqual(len(capped), 3)
            self.assertGreater(len(all_s), len(capped))
        finally:
            b.close()

    def test_indian_separate_from_asian(self):
        b = NSOPWEthnicDatabaseBuilder(db_path=":memory:", delay=2.0, report_delay=0.25)
        try:
            asian = b.surnames_for_ethnicity("asian", all_surnames=True)
            indian = b.surnames_for_ethnicity("indian", all_surnames=True)
            self.assertTrue(len(asian) >= 1)
            self.assertTrue(len(indian) >= 1)
            asian_names = {s.lower() for s, _ in asian}
            indian_names = {s.lower() for s, _ in indian}
            self.assertIn("patel", indian_names)
            self.assertNotIn("patel", asian_names)
            self.assertIn("chen", asian_names)
            self.assertTrue(all(label.startswith("Asian") for _, label in asian))
            self.assertTrue(all(label.startswith("Indian") for _, label in indian))
            # Asian should include multiple East/SE groups after expansion
            self.assertTrue(any("chinese" in lab.lower() for _, lab in asian))
        finally:
            b.close()

    def test_subcategory_filter(self):
        b = NSOPWEthnicDatabaseBuilder(db_path=":memory:", delay=2.0, report_delay=0.25)
        try:
            all_asian = b.surnames_for_ethnicity("asian", all_surnames=True, subcategory="all")
            chinese = b.surnames_for_ethnicity("asian", all_surnames=True, subcategory="chinese")
            self.assertGreater(len(all_asian), len(chinese))
            self.assertTrue(len(chinese) >= 1)
            self.assertTrue(all("chinese" in lab.lower() for _, lab in chinese))
            self.assertIn("chen", {s.lower() for s, _ in chinese})
            # Subcategories helper
            from scraper.ethnic_names import get_ethnic_database
            db = get_ethnic_database()
            subs = db.subcategories("asian")
            self.assertIn("all", subs)
            self.assertIn("chinese", subs)
            self.assertTrue(db.has_subcategories("asian"))
            self.assertFalse(db.has_subcategories("hispanic"))
        finally:
            b.close()

    def test_query_log_resume(self):
        b = NSOPWEthnicDatabaseBuilder(db_path=":memory:", delay=2.0, report_delay=0.25)
        try:
            self.assertFalse(b._query_done("A", "Garcia", "hispanic"))
            b._mark_query_done("A", "Garcia", "hispanic", hit_count=5)
            self.assertTrue(b._query_done("A", "Garcia", "hispanic"))
            self.assertTrue(b._query_done("a", "garcia", "HISPANIC"))  # normalized
            self.assertFalse(b._query_done("B", "Garcia", "hispanic"))
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
