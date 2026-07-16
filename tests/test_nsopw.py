"""Unit tests for NSOPW client parsing and ethnic builder surname selection."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.nsopw_client import NSOPWClient, NSOPWOffender, offender_matches_name_prefixes
from scraper.nsopw_builder import (
    FIRST_INITIALS,
    FIRST_INITIALS_INDIAN,
    FIRST_INITIALS_INDIAN_WIDE,
    INDIAN_LAST_DIGRAPHS_ABBREV,
    INDIAN_LAST_DIGRAPHS_WIDE,
    NSOPWEthnicDatabaseBuilder,
    RateLimiter,
    compact_search_plan,
    estimate_compact_query_count,
    first_initials_for_mode,
    indian_surname_digraphs,
    is_abbreviated_first_mode,
    last_matches_target_surnames,
    last_name_search_prefix,
    last_prefix_whitelist_for,
    top_surname_digraphs,
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

    def test_offender_matches_name_prefixes_helper(self):
        """Helper for optional purity mode — default scrape keeps all API hits."""
        self.assertFalse(
            offender_matches_name_prefixes(
                "A", "PA",
                first_name="JOSE", middle_name="A", last_name="ALANIZ",
            )
        )
        self.assertTrue(
            offender_matches_name_prefixes(
                "A", "PA",
                first_name="JOSE", middle_name="A", last_name="ALANIZ",
                alias_dicts=[{"givenName": "ANGEL", "surName": "PARRA"}],
                allow_aliases=True,
            )
        )
        self.assertTrue(
            offender_matches_name_prefixes(
                "M", "AH",
                first_name="MOHAMED", last_name="AHMED",
            )
        )

    def test_search_by_name_keeps_all_api_hits_by_default(self):
        """Maximize yield: do not drop alias/fuzzy API rows unless strict_prefix."""
        client = NSOPWClient(delay=0)
        raw_hits = [
            {
                "name": {"givenName": "JOSE", "middleName": "A", "surName": "ALANIZ"},
                "aliases": [{"givenName": "ANGEL", "surName": "PARRA"}],
                "jurisdictionId": "FL",
                "offenderUri": "https://example.gov/1",
            },
            {
                "name": {"givenName": "NAMNUEL", "surName": "CHAVEZ"},
                "aliases": [],
                "jurisdictionId": "TX",
                "offenderUri": "https://example.gov/2",
            },
            {
                "name": {"givenName": "ANTHONY", "surName": "PATEL"},
                "aliases": [],
                "jurisdictionId": "CA",
                "offenderUri": "https://example.gov/3",
            },
        ]

        class FakeResp:
            status_code = 200
            text = "{}"

            def json(self):
                return {"offenders": raw_hits}

        client._ensure_warm = lambda: None  # type: ignore
        client.session.post = lambda *a, **k: FakeResp()  # type: ignore
        client.delay = 0
        try:
            hits = client.search_by_name("A", "PA", jurisdictions=["FL", "TX", "CA"])
            self.assertEqual(len(hits), 3)
            # Optional purity mode still available
            hits_strict = client.search_by_name(
                "A", "PA", jurisdictions=["FL", "TX", "CA"], strict_prefix=True
            )
            names = {(h.first_name, h.last_name) for h in hits_strict}
            self.assertEqual(names, {("ANTHONY", "PATEL")})
        finally:
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

    def test_ma_sorb_offense_table_and_url(self):
        """MA SORB: Jurisdiction column is the offense; path casing is significant."""
        html = """
        <html><body>
        <table class="data-table">
          <tr><td class="label">Name:</td><td class="data">AMAYA, JAIME</td>
              <td class="label">Level:</td><td class="data">Level 2</td></tr>
          <tr><td class="label">Race:</td><td class="data">White</td></tr>
        </table>
        <table class="data-table-list">
          <caption class="accessibility">Offense(s)</caption>
          <thead><tr>
            <th>Row</th><th>Jurisdiction</th><th>Chapter/Section</th>
            <th>Conviction/Adjudication Date</th><th>No. of Convictions</th>
          </tr></thead>
          <tbody>
            <tr class="odd">
              <td>1</td><td>Rape of child with force</td><td>265/22A</td>
              <td>12/16/2021</td><td>1</td>
            </tr>
            <tr class="even">
              <td>2</td><td>Rape and abuse of child</td><td>265/23</td>
              <td>12/16/2021</td><td>3</td>
            </tr>
          </tbody>
        </table>
        </body></html>
        """
        fetcher = ReportFetcher(delay=0)
        data = fetcher._from_html(html)
        crime = data.get("crime") or ""
        self.assertIn("Rape of child with force", crime)
        self.assertIn("Rape and abuse of child", crime)
        self.assertNotIn("Photo Date", crime)
        self.assertNotIn("Year Of Birth", crime)
        self.assertEqual(data.get("race"), "White")
        fetcher.close()

        from scraper.public_links import (
            normalize_ma_sorb_url,
            openable_url_for_record,
        )
        from scraper.database import Database

        bad = (
            "https://sorb.chs.state.ma.us/sorbpublic/direct/"
            "viewnsoproffenderdetails.action?m=view&son=528253"
        )
        good = normalize_ma_sorb_url(bad)
        self.assertIn("viewNsoprOffenderDetails.action", good)
        self.assertNotIn("viewnsoproffenderdetails", good)
        self.assertEqual(
            openable_url_for_record({"source_url": bad, "state": "MA"}),
            good,
        )
        self.assertIn(
            "viewNsoprOffenderDetails.action",
            Database.normalize_identity_url(bad),
        )

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


class RateLimiterTests(unittest.TestCase):
    def test_wait_cancelled_mid_sleep(self):
        import threading
        import time

        lim = RateLimiter(2.0)
        lim.wait()  # prime so next wait actually sleeps
        flag = {"c": False}

        def fire():
            time.sleep(0.12)
            flag["c"] = True

        t0 = time.monotonic()
        threading.Thread(target=fire, daemon=True).start()
        cancelled = lim.wait(lambda: flag["c"])
        dt = time.monotonic() - t0
        self.assertTrue(cancelled)
        self.assertLess(dt, 0.6)
        self.assertGreaterEqual(dt, 0.1)


class CompactPrefixTests(unittest.TestCase):
    def test_default_first_mode_is_full_az(self):
        """Abbreviated Indian letters are optional — default is A–Z."""
        self.assertEqual(first_initials_for_mode("initials"), list(FIRST_INITIALS))
        self.assertEqual(first_initials_for_mode(""), list(FIRST_INITIALS))
        self.assertEqual(first_initials_for_mode(None), list(FIRST_INITIALS))
        self.assertFalse(is_abbreviated_first_mode("initials"))
        self.assertTrue(is_abbreviated_first_mode("indian"))
        self.assertTrue(is_abbreviated_first_mode("common"))  # alias

    def test_indian_firsts_not_us_letters(self):
        """Abbreviated mode uses Indian given-name letters, not US SSA (JMACRDSBLT)."""
        indian = first_initials_for_mode("indian")
        self.assertEqual(indian, list(FIRST_INITIALS_INDIAN))
        self.assertEqual(len(FIRST_INITIALS_INDIAN), 10)
        # Indian set should include A/S/R/P/K common Indic starts
        for letter in "ASRPMKVNBD":
            self.assertIn(letter, indian)
        # US-heavy-only pattern must not be the abbreviated set
        self.assertNotEqual("".join(indian), "JMACRDSBLT")
        # common/common_wide are aliases for indian/indian_wide
        self.assertEqual(first_initials_for_mode("common"), list(FIRST_INITIALS_INDIAN))
        self.assertEqual(
            first_initials_for_mode("indian_wide"), list(FIRST_INITIALS_INDIAN_WIDE)
        )

    def test_indian_firsts_cut_query_count(self):
        pairs = [(s, "Indian") for s in ("Patel", "Singh", "Sharma", "Kumar", "Reddy")]
        full = estimate_compact_query_count(pairs, FIRST_INITIALS, min_combined=3)
        indian = estimate_compact_query_count(
            pairs, FIRST_INITIALS_INDIAN, min_combined=3
        )
        self.assertLess(indian, full)
        self.assertGreater(indian, 0)

    def test_surname_digraphs_are_indian_only(self):
        """Last prefixes come from Indian surnames — never brute-force AA–ZZ."""
        digs = indian_surname_digraphs(
            ["Patel", "Singh", "Sharma", "Kumar", "Reddy", "Chatterjee"]
        )
        self.assertIn("PA", digs)
        self.assertIn("SI", digs)
        self.assertIn("SH", digs)
        self.assertIn("KU", digs)
        self.assertIn("RE", digs)
        self.assertIn("CH", digs)
        # Unlikely Western digraphs not in this set
        self.assertNotIn("ZZ", digs)
        self.assertNotIn("XQ", digs)
        # Corpus digraphs should include common Indian starts
        corpus = indian_surname_digraphs()
        self.assertIn("PA", corpus)
        self.assertIn("SI", corpus)
        self.assertNotIn("ZZ", corpus)
        # Default non-abbreviated: no extra filter (None) → all list digraphs
        pairs = [(s, "Indian") for s in ("Patel", "Singh")]
        self.assertIsNone(
            last_prefix_whitelist_for("indian", pairs, abbreviated=False)
        )
        # Compact plan with explicit whitelist drops non-allowed digraphs
        mixed = [(s, "Indian") for s in ("Patel", "Singh", "Smith")]
        plan = compact_search_plan(
            mixed, ["A"], min_combined=3, allowed_last_prefixes={"PA", "SI"}
        )
        prefs = {p.upper() for _, p, _, _ in plan}
        self.assertEqual(prefs, {"PA", "SI"})
        self.assertNotIn("SM", prefs)

    def test_abbreviated_mode_cuts_first_and_last_letters(self):
        """Abbreviated mode shortens both first initials and surname digraphs."""
        self.assertEqual(len(INDIAN_LAST_DIGRAPHS_ABBREV), 30)
        self.assertEqual(len(INDIAN_LAST_DIGRAPHS_WIDE), 50)
        # Top digraphs from a small list
        tops = top_surname_digraphs(
            ["Patel", "Patel", "Singh", "Sharma", "Kumar", "Reddy", "Iyer"],
            limit=3,
        )
        self.assertEqual(tops[0], "PA")  # Patel x2
        self.assertEqual(len(tops), 3)

        # Build a list with many digraphs including rare ones
        common = ["Patel", "Singh", "Sharma", "Kumar", "Reddy", "Chatterjee",
                  "Banerjee", "Mukherjee", "Nair", "Rao", "Gupta", "Mehta"]
        rare = ["Xylophone", "Zwicky", "Quibble"]  # not in Indian abbrev seed
        pairs = [(s, "Indian") for s in common + rare]
        wl = last_prefix_whitelist_for(
            "indian", pairs, abbreviated=True, mode="indian"
        )
        self.assertIsNotNone(wl)
        # Common Indian digraphs kept
        self.assertIn("PA", wl)
        self.assertIn("SI", wl)
        self.assertIn("SH", wl)
        self.assertIn("CH", wl)  # Chatterjee
        # Rare / non-Indian digraphs dropped by abbreviated seed
        self.assertNotIn("XY", wl)
        self.assertNotIn("ZW", wl)
        self.assertNotIn("QU", wl)
        # Full plan (all digraphs) > abbreviated plan
        full = estimate_compact_query_count(
            pairs, FIRST_INITIALS_INDIAN, min_combined=3
        )
        abbr = estimate_compact_query_count(
            pairs,
            FIRST_INITIALS_INDIAN,
            min_combined=3,
            allowed_last_prefixes=wl,
        )
        self.assertLess(abbr, full)
        self.assertGreater(abbr, 0)
        # Wide allows more digraphs than narrow
        wl_wide = last_prefix_whitelist_for(
            "indian", pairs, abbreviated=True, mode="indian_wide"
        )
        self.assertGreaterEqual(len(wl_wide or set()), len(wl or set()))

    def test_last_prefix_min_combined_3(self):
        # Shortest legal last token for max coverage per search
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
        # Short list surnames must not prefix-match unrelated Western names
        # (Indian list includes De / John — caused false "matched" buckets)
        self.assertTrue(last_matches_target_surnames("De", ["De", "Dev", "John"]))
        self.assertTrue(last_matches_target_surnames("John", ["De", "John"]))
        self.assertFalse(last_matches_target_surnames("Delosantos", ["De", "Dev"]))
        self.assertFalse(last_matches_target_surnames("De-Vries", ["De", "Dev"]))
        self.assertFalse(last_matches_target_surnames("Devries", ["De"]))
        self.assertFalse(last_matches_target_surnames("Johnson", ["John"]))
        self.assertFalse(last_matches_target_surnames("Anthony", ["Anand", "Ali"]))
        # Longer list names still allow slight suffix variants
        self.assertTrue(last_matches_target_surnames("Sharma", ["Sharma", "Patel"]))
        self.assertTrue(last_matches_target_surnames("Patelx", ["Patel"]))  # len(Patel)>=5

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

    def test_indian_short_names_do_not_false_match(self):
        """Real Indian list: short names must not claim Johnson / De-Vries as Indian."""
        from scraper.ethnic_names import get_ethnic_database

        # Fresh load (module may cache; EthnicNameDatabase reads JSON on init)
        targets = list(get_ethnic_database().indian_surnames)
        self.assertIn("De", targets)
        self.assertTrue(last_matches_target_surnames("Das", targets))
        self.assertTrue(last_matches_target_surnames("Patel", targets))
        self.assertTrue(last_matches_target_surnames("Singh", targets))
        self.assertFalse(last_matches_target_surnames("Johnson", targets))
        self.assertFalse(last_matches_target_surnames("Delosantos", targets))
        self.assertFalse(last_matches_target_surnames("De-Vries", targets))
        self.assertFalse(last_matches_target_surnames("Anthony", targets))
        # Western names that used to sit on the Indian list
        self.assertFalse(last_matches_target_surnames("John", targets))
        self.assertFalse(last_matches_target_surnames("Abraham", targets))
        self.assertFalse(last_matches_target_surnames("Joseph", targets))

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
    def test_indian_high_confidence_list(self):
        """Curated high-confidence Indians exclude Western noise like Dwayne."""
        from scraper.ethnic_names import EthnicNameDatabase

        db = EthnicNameDatabase()
        self.assertGreater(len(db.indian_high_confidence_surnames), 50)
        self.assertIn("Patel", db.indian_high_confidence_surnames)
        self.assertIn("Singh", db.indian_high_confidence_surnames)
        self.assertNotIn("Dwayne", db.indian_high_confidence_surnames)
        for bad in ("Dwayne", "George", "Paul", "Thomas", "Jacob", "John"):
            self.assertFalse(
                any(x.lower() == bad.lower() for x in db.indian_surnames),
                f"{bad} must not be on broad Indian list",
            )
        b = NSOPWEthnicDatabaseBuilder(db_path=":memory:", delay=1.5, report_delay=0.1)
        try:
            pairs_ind = b.surnames_for_ethnicity("indian", all_surnames=True)
            names_ind = {s for s, _ in pairs_ind}
            self.assertIn("Patel", names_ind)
            self.assertNotIn("Dwayne", names_ind)
            self.assertTrue(
                all(lab.startswith("Indian/MENA") for _, lab in pairs_ind)
            )
            # HC surnames appear under indian pool (not a separate abandoned filter)
            self.assertTrue(
                any("high_confidence" in lab for _, lab in pairs_ind)
            )
            pairs_mena = b.surnames_for_ethnicity("mena", all_surnames=True)
            self.assertTrue(
                all(lab == "Indian/MENA (arabic)" for _, lab in pairs_mena)
            )
            pairs_full = b.surnames_for_ethnicity(
                "indian/mena (merged)", all_surnames=True
            )
            names_full = {s for s, _ in pairs_full}
            self.assertTrue(names_ind.issubset(names_full))
            if pairs_mena:
                self.assertTrue(
                    {s for s, _ in pairs_mena}.issubset(names_full)
                )
            # No abandoned arabic-only labels under indian-only filter
            self.assertFalse(
                any("(arabic)" in lab for _, lab in pairs_ind)
            )
        finally:
            b.close()

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
            # Same first+last under a different ethnicity must still count as done
            # (NSOPW API is not ethnicity-filtered — do not re-hit the network)
            self.assertTrue(b._query_done("A", "Garcia", "asian"))
            self.assertTrue(b._query_done("A", "garcia", "indian"))
            self.assertFalse(b._query_done("B", "Garcia", "hispanic"))
            # Preload set also sees the pair
            loaded = b._load_completed_queries()
            self.assertIn(("A", "garcia"), loaded)
        finally:
            b.close()

    def test_live_options_raise_search_cap_mid_run(self):
        """Raising max_searches via live_options must allow more API calls."""
        b = NSOPWEthnicDatabaseBuilder(db_path=":memory:", delay=0.0, report_delay=0.0)
        try:
            pairs = [("Garcia", "Hispanic"), ("Gomez", "Hispanic"), ("Lopez", "Hispanic"),
                     ("Martinez", "Hispanic"), ("Rodriguez", "Hispanic")]
            mock_client = MagicMock()
            mock_client.search_by_name.return_value = []
            b.client = mock_client
            b.search_limiter.wait = lambda *a, **k: False  # type: ignore
            b.report_limiter.wait = lambda *a, **k: False  # type: ignore
            b.search_limiter.set_interval(0.0)
            # Start with cap 1; after first search, live_options raises cap to 3
            state = {"n": 0}

            def live():
                state["n"] += 1
                # First few polls: cap 1; then raise to 3
                cap = 1 if state["n"] < 3 else 3
                return {
                    "max_searches": cap,
                    "max_names": 0,
                    "search_delay": 2.0,
                    "report_delay": 0.25,
                    "skip_existing_urls": True,
                    "skip_completed_searches": False,
                    "new_files_only": True,
                    "enrich_reports": False,
                    "save_html": False,
                }

            with patch.object(b, "surnames_for_ethnicity", return_value=pairs):
                stats = b.build(
                    ethnicity="hispanic",
                    surnames_limit=10,
                    first_mode="initials",
                    first_names=["A"],
                    max_searches=1,
                    max_names=0,
                    skip_completed_searches=False,
                    enrich_reports=False,
                    save_html=False,
                    use_compact_prefixes=True,
                    live_options=live,
                )
            # Without live bump would be 1; with bump should be > 1
            self.assertGreaterEqual(stats.searches, 2)
            self.assertGreaterEqual(mock_client.search_by_name.call_count, 2)
            # Delay live-update applied
            self.assertGreaterEqual(b.search_delay, 2.0)
        finally:
            b.close()

    def test_build_skips_completed_unless_repeat(self):
        """Build must not call search_by_name for logged queries unless repeat is on."""
        b = NSOPWEthnicDatabaseBuilder(db_path=":memory:", delay=0.0, report_delay=0.0)
        try:
            pairs = [("Garcia", "Hispanic"), ("Gomez", "Hispanic"), ("Lopez", "Hispanic")]
            # Compact plan with first=A → last prefixes "ga", "go", "lo"
            b._mark_query_done("A", "ga", "hispanic", hit_count=0)
            b._mark_query_done("A", "go", "asian", hit_count=0)  # other ethnicity still blocks
            mock_client = MagicMock()
            mock_client.search_by_name.return_value = []
            b.client = mock_client
            b.search_limiter.wait = lambda *a, **k: False  # type: ignore
            b.report_limiter.wait = lambda *a, **k: False  # type: ignore

            with patch.object(b, "surnames_for_ethnicity", return_value=pairs):
                stats = b.build(
                    ethnicity="hispanic",
                    surnames_limit=10,
                    all_surnames=False,
                    first_mode="initials",
                    first_names=["A"],
                    max_searches=50,
                    max_names=0,
                    skip_completed_searches=True,
                    enrich_reports=False,
                    save_html=False,
                    use_compact_prefixes=True,
                )
            searched = []
            for call in mock_client.search_by_name.call_args_list:
                args, _kwargs = call
                first = str(args[0] if args else "").strip().upper()
                last = str(args[1] if len(args) > 1 else "").strip().lower()
                searched.append((first, last))
                self.assertNotIn(
                    (first, last),
                    {("A", "ga"), ("A", "go")},
                    "must not re-search completed first+last pairs",
                )
            self.assertGreaterEqual(stats.searches_skipped, 2)
            # Only Lopez / "lo" should be new among the three prefixes
            self.assertIn(("A", "lo"), searched)
            self.assertEqual(stats.searches, 1)

            # Explicit repeat: skip_completed_searches=False must re-hit completed pairs
            mock_client.search_by_name.reset_mock()
            mock_client.search_by_name.return_value = []
            with patch.object(b, "surnames_for_ethnicity", return_value=pairs):
                stats2 = b.build(
                    ethnicity="hispanic",
                    surnames_limit=10,
                    first_mode="initials",
                    first_names=["A"],
                    max_searches=10,
                    max_names=0,
                    skip_completed_searches=False,
                    enrich_reports=False,
                    save_html=False,
                    use_compact_prefixes=True,
                )
            self.assertEqual(stats2.searches_skipped, 0)
            self.assertGreaterEqual(stats2.searches, 2)
            repeated = {
                (str(c.args[0]).upper(), str(c.args[1]).lower())
                for c in mock_client.search_by_name.call_args_list
            }
            self.assertTrue({("A", "ga"), ("A", "go")}.issubset(repeated))
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

    def test_sc_displayimage_thumb_fallback(self):
        """SC DisplayImage Thumb=false empty GIF → try Thumb=true PNG."""
        from scraper.report_fetcher import ReportFetcher, photo_url_variants, photo_state_from_url
        import tempfile

        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000

        class EmptyGif:
            status_code = 200
            headers = {"Content-Type": "Image/gif"}
            content = b""

        class PngAsGif:
            status_code = 200
            headers = {"Content-Type": "Image/gif"}
            content = png

        def fake_get(url, **kwargs):
            if "Thumb=true" in url or "thumb=true" in url.lower():
                return PngAsGif()
            return EmptyGif()

        base = (
            "https://scor.sled.sc.gov/DisplayImage.aspx?"
            "OffenderId=2279254&ImageId=805619&Thumb=false"
        )
        variants = photo_url_variants(base)
        self.assertTrue(any("Thumb=true" in v or "thumb=true" in v.lower() for v in variants))
        self.assertEqual(photo_state_from_url(base), "SC")
        self.assertEqual(
            photo_state_from_url("https://sor.tbi.tn.gov/api/sorimage/001"), "TN"
        )

        f = ReportFetcher(delay=0)
        f.session.get = fake_get  # type: ignore
        try:
            with tempfile.TemporaryDirectory() as td:
                path = f.download_photo(
                    base,
                    Path(td),
                    referer="https://scor.sled.sc.gov/OffenderDetails.aspx",
                    stem="sctest",
                    reject_gif=True,
                )
                self.assertIsNotNone(path)
                self.assertTrue(str(path).endswith(".png"))
                self.assertTrue(Path(path).is_file())
                self.assertGreaterEqual(Path(path).stat().st_size, 2000)
        finally:
            f.close()

    def test_al_watchsystems_host_variants_and_extract(self):
        """AL iCrimewatch: docs↔wsdocs aliases + extract /pictures/ from HTML."""
        from scraper.report_fetcher import (
            photo_url_variants,
            extract_dedicated_photo_urls,
            ReportFetcher,
        )
        import tempfile

        ws = (
            "https://wsdocs.watchsystems.com/pictures/54174/"
            "1601693-6b547b0b-d945-45c7-a1ef-df9e92c4b0ed.jpg"
        )
        variants = photo_url_variants(ws)
        self.assertTrue(any("docs.watchsystems.com" in v for v in variants))
        self.assertTrue(any("wsdocs.watchsystems.com" in v for v in variants))

        html = """
        <html><body>
        <img src="https://docs.watchsystems.com/offices/54174/54174-8020.jpg"
             alt="Etowah County AL Sheriff's Office" width="800" height="200">
        <img src='https://docs.watchsystems.com/pictures/54174/1601693-abc.jpg'
             width='200px' alt='Offender photo'>
        </body></html>
        """
        found = extract_dedicated_photo_urls(html)
        self.assertTrue(found)
        self.assertIn("/pictures/", found[0].lower())
        self.assertNotIn("/offices/", found[0].lower())

        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 3000 + b"\xff\xd9"

        class OkResp:
            status_code = 200
            headers = {"Content-Type": "image/jpeg"}
            content = jpeg

        class FailResp:
            status_code = 404
            headers = {"Content-Type": "text/html"}
            content = b"not found"

        def fake_get(url, **kwargs):
            # Only docs host "works" — forces variant retry off wsdocs
            if "docs.watchsystems.com" in url and "/pictures/" in url:
                return OkResp()
            return FailResp()

        f = ReportFetcher(delay=0)
        f.session.get = fake_get  # type: ignore
        # Avoid stock-requests last-ditch path in tests
        import scraper.report_fetcher as rfmod

        real_requests_get = rfmod.requests.get
        rfmod.requests.get = fake_get  # type: ignore
        try:
            with tempfile.TemporaryDirectory() as td:
                path = f.download_photo(
                    ws,  # wsdocs first
                    Path(td),
                    referer="https://www.icrimewatch.net/offenderdetails.php?OfndrID=1",
                    stem="altest",
                    reject_gif=True,
                )
                self.assertIsNotNone(path)
                self.assertTrue(Path(path).is_file())
        finally:
            rfmod.requests.get = real_requests_get
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
