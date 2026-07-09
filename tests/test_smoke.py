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

from scraper.database import Database, backup_database_file
from scraper.app_settings import load_settings, save_settings, DEFAULTS
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

    def test_file_backup_and_prune(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbp = Path(tmp) / "offenders.db"
            bdir = Path(tmp) / "backups"
            db = Database(str(dbp))
            db.insert_offender({
                "first_name": "A", "last_name": "Backup", "race": "WHITE", "state": "TX",
            })
            db.close()
            for _ in range(4):
                dest, _note = backup_database_file(dbp, bdir, keep=2, prefix="offenders")
                self.assertTrue(dest.is_file())
            kept = list(bdir.glob("offenders_*.db"))
            self.assertEqual(len(kept), 2)
            restored = Database(str(kept[0]))
            self.assertEqual(restored.get_total_count(), 1)
            restored.close()

    def test_import_csv_normalizes_and_infers_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ga_offenders.csv"
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["First Name", "Last Name", "Race"])
                w.writeheader()
                w.writerow({"First Name": "Ana", "Last Name": "Lopez", "Race": "Hispanic"})
            n = self.db.import_csv(str(path))
            imported = n["imported"] if isinstance(n, dict) else n
            self.assertEqual(imported, 1)
            rows = self.db.search_by_name("Lopez")
            self.assertEqual(rows[0]["first_name"], "Ana")
            self.assertEqual(rows[0]["state"], "GA")

    def test_integrity_and_incomplete_and_update(self):
        self.db.insert_offender({
            "first_name": "A", "last_name": "One", "state": "FL",
            "race": "White", "crime": "X", "source_url": "https://ex/1",
            "report_html_path": "data/x.html", "photo_path": "data/p.jpg",
        })
        self.db.insert_offender({
            "first_name": "B", "last_name": "Two", "state": "FL",
            "source_url": "https://ex/2",
        })
        self.db.insert_offender({
            "first_name": "C", "last_name": "Three", "state": "GA",
            "race": "Black",
        })
        rep = self.db.get_integrity_report()
        self.assertEqual(rep["overall"]["total"], 3)
        self.assertEqual(rep["overall"]["with_race"], 2)
        self.assertEqual(rep["overall"]["with_crime"], 1)
        self.assertEqual(rep["overall"]["with_url"], 2)
        states = {s["state"]: s for s in rep["by_state"]}
        self.assertIn("FL", states)
        self.assertEqual(states["FL"]["total"], 2)
        incomplete = self.db.find_incomplete_reports(
            need_race=True, need_crime=True, need_photo=True, limit=50
        )
        # B Two has URL but missing race/crime/photo
        urls = {r["source_url"] for r in incomplete}
        self.assertIn("https://ex/2", urls)
        self.assertNotIn("https://ex/1", urls)
        ok = self.db.update_offender(2, {"race": "White", "crime": "Offense Z"})
        self.assertTrue(ok)
        row = self.db.get_offender_by_id(2)
        self.assertEqual(row["race"], "White")
        self.assertEqual(row["crime"], "Offense Z")

    def test_import_csv_skips_existing_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "az_offenders.csv"
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(
                    f, fieldnames=["First Name", "Last Name", "Source URL"]
                )
                w.writeheader()
                w.writerow({
                    "First Name": "X", "Last Name": "Y",
                    "Source URL": "https://example.gov/r/1",
                })
            r1 = self.db.import_csv(str(path), skip_existing_urls=True)
            self.assertEqual(r1["imported"], 1)
            r2 = self.db.import_csv(str(path), skip_existing_urls=True)
            self.assertEqual(r2["imported"], 0)
            self.assertEqual(r2["skipped"], 1)


class AppSettingsTests(unittest.TestCase):
    def test_load_save_normalize(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app_settings.json"
            s = load_settings(path)
            self.assertTrue(s["backup_on_close"])
            self.assertTrue(s["nsopw_compact_prefixes"])
            self.assertEqual(s["nsopw_min_combined_len"], 3)
            s["backup_on_close"] = False
            s["max_backups"] = 5
            s["nsopw_min_combined_len"] = 2  # clamp to >= 3
            save_settings(s, path)
            s2 = load_settings(path)
            self.assertFalse(s2["backup_on_close"])
            self.assertEqual(s2["max_backups"], 5)
            self.assertEqual(s2["nsopw_min_combined_len"], 3)
            self.assertIn("db_path", DEFAULTS)


class BackupIntegrityTests(unittest.TestCase):
    def test_backup_verifies_and_is_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbp = Path(tmp) / "offenders.db"
            bdir = Path(tmp) / "backups"
            db = Database(str(dbp))
            db.insert_offender({
                "first_name": "Safe",
                "last_name": "Child",
                "race": "WHITE",
                "state": "CA",
                "source_url": "https://example.gov/r/unique-1",
            })
            # literal underscore must not act as LIKE wildcard
            db.insert_offender({
                "first_name": "X",
                "last_name": "A_B",
                "race": "WHITE",
                "state": "CA",
            })
            db.close()

            dest, note = backup_database_file(dbp, bdir, keep=5, prefix="offenders", verify=True)
            self.assertTrue(dest.is_file())
            self.assertGreater(dest.stat().st_size, 0)

            restored = Database(str(dest))
            self.assertEqual(restored.get_total_count(), 2)
            # LIKE escape: searching "A_B" should find the literal underscore name
            rows = restored.search_by_name("A_B")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["last_name"], "A_B")
            # Searching "A%B" as literal should not expand % as wildcard to match A_B only by chance
            # (A_B does not contain a percent sign)
            rows2 = restored.search_by_name("A%B")
            self.assertEqual(len(rows2), 0)
            restored.close()

    def test_import_csv_dedupes_batch_and_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "t.db"))
            path = Path(tmp) / "in.csv"
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(
                    f, fieldnames=["First Name", "Last Name", "Source URL"]
                )
                w.writeheader()
                w.writerow({
                    "First Name": "A", "Last Name": "One",
                    "Source URL": "https://ex/1",
                })
                w.writerow({
                    "First Name": "B", "Last Name": "Two",
                    "Source URL": "https://ex/1",  # same URL twice in file
                })
            r = db.import_csv(str(path), skip_existing_urls=True)
            self.assertEqual(r["imported"], 1)
            self.assertEqual(r["skipped"], 1)
            r2 = db.import_csv(str(path), skip_existing_urls=True)
            self.assertEqual(r2["imported"], 0)
            self.assertEqual(r2["skipped"], 2)
            db.close()


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
            # White/WHITE collapsed to one display label
            self.assertEqual(h[0].expected_race, "White")
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

    def test_indian_other_and_other_asian_not_mismatch(self):
        from scraper.searcher import (
            _is_compatible,
            _canonical_race_key,
            format_race_label,
        )

        # Other / Other Asian must not flag Indian surnames
        self.assertTrue(_is_compatible("Indian", "Other"))
        self.assertTrue(_is_compatible("Indian", "OTHER"))
        self.assertTrue(_is_compatible("Indian", "Other Asian"))
        self.assertTrue(_is_compatible("Indian (india)", "OTHER ASIAN"))
        self.assertTrue(_is_compatible("Indian", "Asian"))
        # Still a mismatch when coded White
        self.assertFalse(_is_compatible("Indian", "White"))
        self.assertFalse(_is_compatible("Indian", "WHITE"))
        # White case merge
        self.assertEqual(_canonical_race_key("White"), "WHITE")
        self.assertEqual(_canonical_race_key("WHITE"), "WHITE")
        self.assertEqual(format_race_label("White"), "White")
        self.assertEqual(format_race_label("WHITE"), "White")


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
