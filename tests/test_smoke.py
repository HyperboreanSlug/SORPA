"""Smoke tests for database, search, ethnic names, scrapers, and archiver core."""

import csv
import tempfile
import unittest
from pathlib import Path
import sys

# Project root on path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.database import Database
from scraper.searcher import SexOffenderSearcher
from scraper.ethnic_names import EthnicNameDatabase
from scraper.scrapers.base import ScraperFactory
from scraper.scrapers.api_scraper import APIScraper
from scraper.config import REGISTRIES, get_registry_by_abbr
import core


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = Database.create_in_memory()

    def tearDown(self):
        self.db.close()

    def test_insert_and_search(self):
        rid = self.db.insert_offender({
            "first_name": "Juan",
            "last_name": "Garcia",
            "race": "WHITE",
            "state": "FL",
            "age": "42",
        })
        self.assertEqual(rid, 1)
        self.assertEqual(self.db.get_total_count(), 1)
        rows = self.db.search_by_name("Garcia")
        self.assertEqual(len(rows), 1)
        # case-insensitive race/state
        self.assertEqual(len(self.db.search_by_race("white")), 1)
        self.assertEqual(len(self.db.search_by_state("fl")), 1)
        self.assertEqual(len(self.db.search_by_state("ALL")), 1)

    def test_batch_and_export_empty(self):
        n = self.db.insert_offenders_batch([
            {"first_name": "A", "last_name": "Chen", "race": "ASIAN", "state": "CA"},
            {"first_name": "B", "last_name": "Smith", "race": "WHITE", "state": "NY"},
        ])
        self.assertEqual(n, 2)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            count = self.db.export_to_csv(str(path), filters={"state": "ZZ"})
            self.assertEqual(count, 0)
            self.assertTrue(path.exists())
            count2 = self.db.export_to_csv(str(path), filters={"state": "CA"})
            self.assertEqual(count2, 1)

    def test_import_csv_normalizes_and_infers_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ga_offenders.csv"
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["First Name", "Last Name", "Race"])
                w.writeheader()
                w.writerow({"First Name": "Ana", "Last Name": "Lopez", "Race": "Hispanic"})
            n = self.db.import_csv(str(path))
            self.assertEqual(n, 1)
            rows = self.db.search_by_name("Lopez")
            self.assertEqual(rows[0]["first_name"], "Ana")
            self.assertEqual(rows[0]["state"], "GA")


class EthnicAndSearchTests(unittest.TestCase):
    def test_classify_common_names(self):
        eth = EthnicNameDatabase()
        self.assertEqual(eth.classify_by_name("Garcia")[0], "Hispanic")
        self.assertTrue(eth.classify_by_name("Chen")[0].startswith("Asian"))
        patel = eth.classify_by_name("Patel")[0]
        self.assertTrue(patel == "Indian" or patel.startswith("Indian ("))
        self.assertTrue(eth.is_indian_surname("Singh"))
        self.assertFalse(eth.is_asian_surname("Patel")[0])
        # Smith is a common Anglo surname (may match European lists after expansion)
        smith_eth = eth.classify_by_name("Smith")[0]
        self.assertTrue(
            smith_eth == "Unknown" or smith_eth.startswith("European"),
            msg=f"unexpected Smith ethnicity: {smith_eth}",
        )

    def test_misclassification_filters(self):
        s = SexOffenderSearcher(db_path=":memory:")
        try:
            s.db.insert_offenders_batch([
                {"first_name": "Juan", "last_name": "Garcia", "race": "WHITE", "state": "FL"},
                {"first_name": "Maria", "last_name": "Rodriguez", "race": "HISPANIC", "state": "TX"},
                {"first_name": "Wei", "last_name": "Chen", "race": "WHITE", "state": "CA"},
                {"first_name": "John", "last_name": "Smith", "race": "WHITE", "state": "NY"},
            ])
            h = s.find_hispanic_misclassifications()
            self.assertEqual(len(h), 1)
            self.assertEqual(h[0].record["last_name"], "Garcia")
            a = s.find_asian_misclassifications()
            self.assertEqual(len(a), 1)
            self.assertEqual(a[0].record["last_name"], "Chen")
            # Correctly labeled Hispanic is not a misclassification
            all_mc = s.analyze_ethnicities()
            self.assertFalse(any(m.record["last_name"] == "Rodriguez" for m in all_mc))
            names = {r["last_name"] for r in s.filter_by_hispanic_names()}
            self.assertEqual(names, {"Garcia", "Rodriguez"})
        finally:
            s.close()


class ScraperFactoryTests(unittest.TestCase):
    def test_factory_routes(self):
        self.assertEqual(type(ScraperFactory.create("AZ")).__name__, "DirectDownloadScraper")
        self.assertEqual(type(ScraperFactory.create("GA")).__name__, "DirectDownloadScraper")
        self.assertEqual(type(ScraperFactory.create("DC")).__name__, "ArcGISScraper")
        self.assertEqual(type(ScraperFactory.create("FL")).__name__, "HybridScraper")
        self.assertEqual(type(ScraperFactory.create("AL")).__name__, "HTMLScraper")
        s = APIScraper("AK")
        self.assertEqual(s.get_direct_download_urls(), [])
        s.close()

    def test_dc_has_arcgis_endpoint(self):
        reg = get_registry_by_abbr("DC")
        self.assertIsNotNone(reg)
        self.assertEqual(reg.scrape_method, "arcgis")
        self.assertIn("FeatureServer", reg.search_api or "")

    def test_registry_count(self):
        self.assertGreaterEqual(len(REGISTRIES), 51)
        self.assertIsNotNone(get_registry_by_abbr("az"))
        self.assertIsNone(get_registry_by_abbr("XX"))

    def test_interactive_scrape_returns_empty(self):
        s = ScraperFactory.create("AL", delay=0)
        try:
            recs = s.scrape()
            self.assertEqual(recs, [])
        finally:
            s.close()


class CoreArchiverTests(unittest.TestCase):
    def test_load_sources(self):
        sources = core.load_sources()
        self.assertEqual(len(sources), 52)
        direct = core.get_direct_sources(sources)
        self.assertEqual(len(direct), 3)
        abbrs = {s["abbr"] for s in direct}
        self.assertEqual(abbrs, {"AZ", "DC", "GA"})

    def test_snapshot_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = core.get_snapshot_dir(Path(tmp), date_str="2099-01-01")
            self.assertTrue(d.is_dir())
            self.assertEqual(d.name, "2099-01-01")


if __name__ == "__main__":
    unittest.main()
