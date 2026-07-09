"""Unit tests for NSOPW client parsing and ethnic builder surname selection."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.nsopw_client import NSOPWClient, NSOPWOffender
from scraper.nsopw_builder import (
    NSOPWEthnicDatabaseBuilder,
    compact_search_plan,
    last_matches_target_surnames,
    last_name_search_prefix,
)
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
          <tr><th>Offense</th><td>Sexual Assault of a Child</td></tr>
        </table>
        <p>Ethnicity: Hispanic</p>
        </body></html>
        """
        fetcher = ReportFetcher(delay=0)
        data = fetcher._from_html(html)
        self.assertEqual(data.get("race"), "White")
        self.assertEqual(data.get("height"), "5'10\"")
        self.assertIn(data.get("ethnicity"), ("Hispanic", "Hispanic"))
        self.assertIn("Sexual Assault", data.get("crime") or "")
        fetcher.close()

    def test_crime_from_offense_table(self):
        html = """
        <html><body>
        <table>
          <tr><th>Offense</th><th>Statute</th></tr>
          <tr><td>Lewd Act with Child</td><td>PC 288(a)</td></tr>
          <tr><td>Failure to Register</td><td>PC 290</td></tr>
        </table>
        <table>
          <tr><th>Race</th><td>White</td></tr>
        </table>
        </body></html>
        """
        fetcher = ReportFetcher(delay=0)
        data = fetcher._from_html(html)
        crime = data.get("crime") or ""
        self.assertIn("Lewd Act", crime)
        self.assertIn("288", crime)
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

    def test_normalize_url_uppercase_scheme(self):
        from scraper.report_fetcher import _normalize_url
        u = _normalize_url(
            "HTTPS://SEXOFFENDER.ND.GOV/OFFENDER/DETAILS/ABC"
        )
        self.assertTrue(u.startswith("https://"))
        self.assertIn("SEXOFFENDER.ND.GOV", u)

    def test_header_row_table_extraction(self):
        html = """
        <html><body><table>
        <tr><th id="Race">Race</th><th>Sex</th><th>Height</th><th>Hair Color</th></tr>
        <tr><td>Hispanic</td><td>Male</td><td>5 Feet 08 Inches</td><td>Brown</td></tr>
        </table></body></html>
        """
        f = ReportFetcher(delay=0)
        data = f._from_html(html)
        self.assertEqual(data.get("race"), "Hispanic")
        self.assertEqual(data.get("gender"), "Male")
        self.assertIn("5", data.get("height") or "")
        self.assertEqual(data.get("hair_color"), "Brown")
        f.close()

    def test_bootstrap_label_div_extraction(self):
        html = """
        <div>Gender:</div>
        <div class="col-6">MALE</div>
        <div>Ethnicity:</div>
        <div class="col-6">BLACK</div>
        <div>Height:</div>
        <div class="col-6">6'00"</div>
        """
        f = ReportFetcher(delay=0)
        data = f._from_html(html)
        self.assertEqual(data.get("gender"), "MALE")
        self.assertEqual(data.get("ethnicity"), "BLACK")
        self.assertEqual(data.get("race"), "BLACK")  # ethnicity fallback
        self.assertEqual(data.get("height"), "6'00\"")
        f.close()

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


class CompactPrefixTests(unittest.TestCase):
    def test_last_prefix_min_combined_3(self):
        self.assertEqual(last_name_search_prefix("Ahmed", "M"), "Ah")
        self.assertEqual(last_name_search_prefix("Ahmed", "MO"), "A")
        self.assertEqual(last_name_search_prefix("Li", "M"), "Li")
        self.assertEqual(last_name_search_prefix("O", "M"), "O")  # still short

    def test_compact_plan_collapses_shared_prefix(self):
        pairs = [("Ahmed", "Arabic"), ("Ahmad", "Arabic"), ("Ali", "Arabic")]
        plan = compact_search_plan(pairs, ["M"])
        # M+Ah covers Ahmed+Ahmad; M+Al covers Ali → 2 queries not 3
        keys = {(f.upper(), p.upper()) for f, p, _e, _s in plan}
        self.assertIn(("M", "AH"), keys)
        self.assertIn(("M", "AL"), keys)
        self.assertEqual(len(plan), 2)
        for _f, pref, _e, covered in plan:
            if pref.upper() == "AH":
                self.assertEqual(set(c.lower() for c in covered), {"ahmed", "ahmad"})

    def test_last_matches_targets_filters_off_list(self):
        self.assertTrue(last_matches_target_surnames("Ahmed", ["Ahmed", "Ahmad"]))
        self.assertTrue(last_matches_target_surnames("AHMAD", ["Ahmed", "Ahmad"]))
        self.assertFalse(last_matches_target_surnames("Ahern", ["Ahmed", "Ahmad"]))
        self.assertTrue(last_matches_target_surnames("Garciaz", ["Garcia"]))

    def test_ethnicity_bucket_split(self):
        """Hits with list surnames vs other surnames for the same short prefix."""
        eth_list = ["Ahmed", "Ahmad"]
        samples = [
            ("MOMEN", "AHMED", True),
            ("MICHAEL", "AHERN", False),
            ("MUBASHAR", "AHMAD", True),
            ("MATTHEW", "ASHLEY", False),
        ]
        matched, other = [], []
        for _f, last, expect_match in samples:
            is_m = last_matches_target_surnames(last, eth_list)
            self.assertEqual(is_m, expect_match, last)
            (matched if is_m else other).append(last)
        self.assertEqual(matched, ["AHMED", "AHMAD"])
        self.assertEqual(other, ["AHERN", "ASHLEY"])

    def test_compact_fewer_than_naive(self):
        pairs = [(f"Name{i:03d}xyz", "X") for i in range(50)]
        # Many unique 2-letter prefixes from Name### - actually all start with "Na"
        # Better: varied surnames
        pairs = [(s, "H") for s in ("Garcia", "Garza", "Martinez", "Marquez", "Lopez", "Long")]
        plan = compact_search_plan(pairs, list("ABC"))
        naive = len(pairs) * 3
        self.assertLess(len(plan), naive)
        # Ga* collapse Garcia+Garza; Ma* Martinez+Marquez; Lo* Lopez+Long
        self.assertEqual(len(plan), 9)  # 3 prefixes × 3 firsts


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
            self.assertIn("photo_path", cols)
            self.assertIn("photo_url", cols)
            self.assertIn("crime", cols)
            rid = db.insert_offender({
                "first_name": "Test",
                "last_name": "User",
                "source_url": "https://example.gov/r/1",
                "report_html_path": "data/report_pages/TX/abc.html",
                "photo_path": "data/report_pages/TX/photos/x.jpg",
                "photo_url": "https://example.gov/photo/1",
            })
            self.assertEqual(rid, 1)
            row = db._conn.execute(
                "SELECT report_html_path, photo_path, photo_url FROM offenders WHERE id=1"
            ).fetchone()
            self.assertEqual(row["report_html_path"], "data/report_pages/TX/abc.html")
            self.assertEqual(row["photo_path"], "data/report_pages/TX/photos/x.jpg")
            self.assertEqual(row["photo_url"], "https://example.gov/photo/1")
        finally:
            db.close()

    def test_download_photo_retries_ssl_verify_false(self):
        """TLS failures must retry with verify=False (TN/VA hosts on Windows)."""
        from scraper.report_fetcher import ReportFetcher
        import tempfile

        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 3000 + b"\xff\xd9"

        class FakeResp:
            status_code = 200
            headers = {"Content-Type": "image/jpeg"}
            content = jpeg

        calls = {"n": 0, "verify": []}

        def fake_get(url, **kwargs):
            calls["n"] += 1
            calls["verify"].append(kwargs.get("verify", True))
            if kwargs.get("verify", True) is True:
                raise Exception("curl: (60) SSL certificate problem: unable to get local issuer certificate")
            return FakeResp()

        f = ReportFetcher(delay=0)
        f.session.get = fake_get  # type: ignore
        try:
            with tempfile.TemporaryDirectory() as td:
                path = f.download_photo(
                    "https://sor.tbi.tn.gov/api/sorimage/X",
                    Path(td),
                    referer="https://www.nsopw.gov/",
                    stem="ssltest",
                )
                self.assertIsNotNone(path)
                self.assertTrue(Path(path).is_file())
                self.assertIn(False, calls["verify"])
        finally:
            f.close()

    def test_embed_images_rewrites_img_src(self):
        """Archived HTML should point at local assets when images download."""
        from scraper.report_fetcher import ReportFetcher
        import tempfile

        # Tiny valid JPEG (1x1)
        jpeg = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
            b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
            b"\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342"
            b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
            b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
            b"\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04"
            b"\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07\"q"
            b"\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82"
            b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xaa\x00\xff\xd9"
        )
        # Pad past MIN_PRIMARY_PHOTO_BYTES so this is treated as a real mugshot
        jpeg = jpeg + b"\x00" * 2500

        class FakeResp:
            status_code = 200
            headers = {"Content-Type": "image/jpeg"}
            content = jpeg

            def __init__(self, *a, **k):
                pass

        f = ReportFetcher(delay=0)

        def _fake_get(*a, **k):
            return FakeResp()

        f.session.get = _fake_get  # type: ignore
        try:
            with tempfile.TemporaryDirectory() as td:
                html = (
                    '<html><body><img src="https://example.gov/offender/photo.jpg" '
                    'alt="photo"/><p>Race: White</p></body></html>'
                )
                assets = Path(td) / "assets"
                out, primary = f._embed_images_in_html(
                    html,
                    base_url="https://example.gov/report/1",
                    assets_dir=assets,
                    assets_rel_name="assets",
                    referer="https://example.gov/report/1",
                )
                self.assertIn('src="assets/', out)
                self.assertNotIn("https://example.gov/offender/photo.jpg", out)
                self.assertIsNotNone(primary)
                self.assertTrue(Path(primary).is_file())
        finally:
            f.close()


if __name__ == "__main__":
    unittest.main()
