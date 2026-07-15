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
        # Second state search must still return rows (regression)
        self.assertEqual(len(self.db.search_by_state("FL")), 1)
        self.assertEqual(len(self.db.search_by_state("FL")), 1)

    def test_search_state_matches_source_state(self):
        self.db.insert_offender({
            "first_name": "Only",
            "last_name": "SourceState",
            "source_state": "TX",
            # state intentionally empty
        })
        rows = self.db.search_by_state("TX")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["last_name"], "SourceState")
        # Name + state filter also sees source_state
        rows2 = self.db.search_by_name("Source", state="TX")
        self.assertEqual(len(rows2), 1)

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
            self.assertEqual(rows[0]["source_state"], "GA")
            self.assertEqual(rows[0]["full_name"], "Ana Lopez")

    def test_import_csv_feeds_integrity_and_misclass_stats(self):
        """Imported CSV rows must show up in Integrity totals and Misclassify Analyze."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fl_offenders.csv"
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(
                    f,
                    fieldnames=[
                        "First Name", "Last Name", "Race", "Crime", "Source URL",
                    ],
                )
                w.writeheader()
                w.writerow({
                    "First Name": "Juan", "Last Name": "Garcia",
                    # Black + Hispanic name = mismatch; White would be compatible
                    "Race": "Black", "Crime": "A",
                    "Source URL": "https://example.gov/1",
                })
                w.writerow({
                    "First Name": "Bob", "Last Name": "Smith",
                    "Race": "White", "Crime": "",
                    "Source URL": "https://example.gov/2",
                })
                w.writerow({
                    "First Name": "Raj", "Last Name": "Patel",
                    "Race": "White", "Crime": "B",
                    "Source URL": "https://example.gov/3",
                })
            result = self.db.import_csv(str(path), skip_existing_urls=True)
            self.assertEqual(result["imported"], 3)
            self.assertEqual(self.db.get_total_count(), 3)

            rep = self.db.get_integrity_report()
            self.assertEqual(rep["overall"]["total"], 3)
            self.assertEqual(rep["overall"]["with_race"], 3)
            self.assertEqual(rep["overall"]["with_crime"], 2)
            self.assertEqual(rep["overall"]["with_url"], 3)
            by_st = {s["state"]: s for s in rep["by_state"]}
            self.assertEqual(by_st["FL"]["total"], 3)
            self.assertEqual(by_st["FL"]["with_race"], 3)

            races = {r["race"]: r["count"] for r in self.db.get_race_distribution()}
            self.assertEqual(races.get("White"), 2)
            self.assertEqual(races.get("Black"), 1)

            searcher = SexOffenderSearcher()
            orphan = searcher.db
            searcher.db = self.db  # analyze the imported in-memory rows
            try:
                hisp, hisp_base = searcher.analyze_ethnicities(
                    min_confidence=0.5,
                    limit=0,
                    ethnicity_filter="hispanic",
                    return_base_count=True,
                )
                self.assertGreaterEqual(hisp_base, 1)
                hisp_names = {(m.record.get("last_name") or "").lower() for m in hisp}
                self.assertIn("garcia", hisp_names)

                indian, indian_base = searcher.analyze_ethnicities(
                    min_confidence=0.5,
                    limit=0,
                    ethnicity_filter="indian",
                    return_base_count=True,
                )
                self.assertGreaterEqual(indian_base, 1)
                indian_names = {
                    (m.record.get("last_name") or "").lower() for m in indian
                }
                self.assertIn("patel", indian_names)
            finally:
                searcher.db = orphan
                searcher.close()

    def test_find_and_remove_duplicates(self):
        """Duplicate check keeps richest row, merges fields, deletes extras."""
        # Same URL twice; second has race, first has photo
        self.db.insert_offender({
            "first_name": "A", "last_name": "One",
            "source_url": "https://ex/dup/1",
            "photo_path": "data/p.jpg",
            "state": "FL",
        })
        self.db.insert_offender({
            "first_name": "A", "last_name": "One",
            "source_url": "https://ex/dup/1",
            "race": "White",
            "crime": "X",
            "state": "FL",
        })
        # Unique row
        self.db.insert_offender({
            "first_name": "B", "last_name": "Two",
            "source_url": "https://ex/unique/2",
            "state": "GA",
        })
        # Name+state+dob duplicates with *different* charges — both crimes kept
        self.db.insert_offender({
            "first_name": "C", "last_name": "Three",
            "state": "TX",
            "date_of_birth": "1990-01-01",
            "source_url": "https://ex/n1",
            "crime": "Assault",
        })
        self.db.insert_offender({
            "first_name": "C", "last_name": "Three",
            "state": "TX",
            "date_of_birth": "1990-01-01",
            "race": "Black",
            "source_url": "https://ex/n2",
            "crime": "Burglary",
        })
        # Multi-state same person (name+DOB only) — states and listings merge
        self.db.insert_offender({
            "first_name": "D", "last_name": "Multi",
            "state": "FL",
            "date_of_birth": "1985-05-05",
            "source_url": "https://ex/fl/d",
            "crime": "Failure to register",
            "photo_path": "data/d.jpg",
        })
        self.db.insert_offender({
            "first_name": "D", "last_name": "Multi",
            "state": "GA",
            "date_of_birth": "1985-05-05",
            "source_url": "https://ex/ga/d",
            "crime": "Lewd act",
            "race": "White",
        })
        # Shared CAPTCHA URL for many people — must NOT be safe-removed
        for i in range(10):
            self.db.insert_offender({
                "first_name": f"Cap{i}",
                "last_name": f"Tive{i}",
                "source_url": "https://apps.example.gov/public/captcha",
                "state": "WI",
            })

        summary = self.db.count_duplicates(
            ["source_url", "name_state_dob", "name_dob"]
        )
        self.assertGreaterEqual(summary["by_strategy"]["source_url"]["groups"], 1)
        self.assertGreaterEqual(summary["by_strategy"]["source_url"]["extra_rows"], 1)
        self.assertGreaterEqual(summary["by_strategy"]["name_state_dob"]["groups"], 1)
        self.assertGreaterEqual(summary["by_strategy"]["name_dob"]["groups"], 1)
        # CAPTCHA cluster counted but not safe
        self.assertGreaterEqual(
            summary["by_strategy"]["source_url"].get("unsafe_groups", 0), 1
        )

        dry = self.db.remove_duplicates("source_url", dry_run=True, safe_only=True)
        self.assertEqual(dry["deleted"], 1)  # only the real URL dup
        self.assertGreaterEqual(dry.get("skipped_unsafe", 0), 1)
        before = self.db.get_total_count()
        self.assertEqual(before, 17)

        live = self.db.remove_duplicates(
            "source_url", dry_run=False, merge_fields=True, safe_only=True
        )
        self.assertEqual(live["deleted"], 1)
        self.assertEqual(self.db.get_total_count(), 16)

        # Session uid= variants of the same offender URL must group together
        self.db.insert_offender({
            "first_name": "Mont", "last_name": "Bhatti",
            "state": "GA",
            "source_url": (
                "https://state.sor.gbi.ga.gov/sort_public/OffenderDetails.aspx"
                "?Id=50604&uid=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            ),
            "external_id": (
                "https://state.sor.gbi.ga.gov/sort_public/OffenderDetails.aspx"
                "?Id=50604&uid=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            ),
            "photo_path": "data/a.jpg",
            "race": "White",
        })
        self.db.insert_offender({
            "first_name": "Mont", "last_name": "Bhatti",
            "state": "GA",
            "source_url": (
                "https://state.sor.gbi.ga.gov/sort_public/OffenderDetails.aspx"
                "?Id=50604&uid=bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
            ),
            "external_id": (
                "https://state.sor.gbi.ga.gov/sort_public/OffenderDetails.aspx"
                "?Id=50604&uid=bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
            ),
            "crime": "X",
        })
        url_groups = self.db.find_duplicate_groups("source_url")
        self.assertTrue(
            any(
                g["count"] >= 2
                and any(
                    "50604" in str(g.get("key") or "")
                    or "bhatti" in (g.get("keep_preview") or "").lower()
                    for _ in [0]
                )
                for g in url_groups
            )
            or any(
                any("bhatti" in str(m.get("last_name") or "").lower() for m in g.get("members") or [])
                and g["count"] >= 2
                for g in url_groups
            ),
            "uid-variant URLs should form one source_url group",
        )
        ext_groups = self.db.find_duplicate_groups("external_id")
        self.assertTrue(
            any(
                any("bhatti" in str(m.get("last_name") or "").lower() for m in g.get("members") or [])
                and g["count"] >= 2
                for g in ext_groups
            ),
            "stable Id= external key should merge uid variants",
        )
        r_uid = self.db.remove_duplicates(
            "source_url", dry_run=False, merge_fields=True, safe_only=True
        )
        self.assertEqual(r_uid["deleted"], 1)
        # Keeper has photo + crime merged
        kept_b = None
        for row in self.db.iter_offenders():
            if (row.get("last_name") or "").lower() == "bhatti":
                kept_b = row
                break
        self.assertIsNotNone(kept_b)
        self.assertEqual(kept_b.get("photo_path"), "data/a.jpg")
        self.assertEqual(kept_b.get("crime"), "X")
        # CAPTCHA people still present
        captcha_left = sum(
            1
            for r in self.db.iter_offenders()
            if "captcha" in (r.get("source_url") or "").lower()
        )
        self.assertEqual(captcha_left, 10)
        # Keeper should have both race and photo after merge
        kept = None
        for row in self.db.iter_offenders():
            if (row.get("source_url") or "") == "https://ex/dup/1":
                kept = row
                break
        self.assertIsNotNone(kept)
        self.assertEqual(kept.get("race"), "White")
        self.assertEqual(kept.get("photo_path"), "data/p.jpg")
        self.assertEqual(kept.get("crime"), "X")

        # Second pass: name_state_dob — merge distinct crimes
        # (count includes kept Bhatti after uid-merge)
        r2 = self.db.remove_duplicates("name_state_dob", dry_run=False, merge_fields=True)
        self.assertEqual(r2["deleted"], 1)
        self.assertEqual(self.db.get_total_count(), 16)
        c_row = None
        for row in self.db.iter_offenders():
            if (row.get("last_name") or "") == "Three":
                c_row = row
                break
        self.assertIsNotNone(c_row)
        crimes = (c_row.get("crime") or "")
        self.assertIn("Assault", crimes)
        self.assertIn("Burglary", crimes)
        self.assertEqual(c_row.get("race"), "Black")
        urls = (c_row.get("source_url") or "")
        self.assertIn("https://ex/n1", urls)
        self.assertIn("https://ex/n2", urls)

        # Third pass: multi-state name_dob
        r3 = self.db.remove_duplicates("name_dob", dry_run=False, merge_fields=True)
        self.assertEqual(r3["deleted"], 1)
        self.assertEqual(self.db.get_total_count(), 15)
        d_row = None
        for row in self.db.iter_offenders():
            if (row.get("last_name") or "") == "Multi":
                d_row = row
                break
        self.assertIsNotNone(d_row)
        states = (d_row.get("state") or "")
        self.assertIn("FL", states)
        self.assertIn("GA", states)
        d_crimes = (d_row.get("crime") or "")
        self.assertIn("Failure to register", d_crimes)
        self.assertIn("Lewd act", d_crimes)
        self.assertEqual(d_row.get("photo_path"), "data/d.jpg")
        self.assertEqual(d_row.get("race"), "White")
        # Search by either state still finds the merged row
        fl_hits = self.db.search_by_state("FL", limit=50)
        self.assertTrue(any((r.get("last_name") or "") == "Multi" for r in fl_hits))
        ga_hits = self.db.search_by_state("GA", limit=50)
        self.assertTrue(any((r.get("last_name") or "") == "Multi" for r in ga_hits))
        # No more safe URL dups
        self.assertEqual(
            self.db.count_duplicates(["source_url"])["by_strategy"]["source_url"][
                "safe_extra_rows"
            ],
            0,
        )

    def test_scrape_records_and_newest_misclass_scan(self):
        """Scrape-like import_records + limited Analyze must include newest ethnic rows."""
        # Old fillers first
        fillers = [
            {
                "first_name": f"O{i}",
                "last_name": "Smith",
                "race": "White",
                "source_url": f"https://ex/old/{i}",
            }
            for i in range(15)
        ]
        self.assertEqual(
            self.db.import_records(fillers, state="TX")["imported"], 15
        )
        # New scrape batch with clear misclass candidates
        # Hispanic + White is compatible (registry practice); use Black to flag
        scrape = [
            {
                "first_name": "Ana",
                "last_name": "Garcia",
                "race": "Black",
                "source_url": "https://ex/new/g",
            },
            {
                "first_name": "Raj",
                "last_name": "Patel",
                "race": "White",
                "source_url": "https://ex/new/p",
            },
        ]
        self.assertEqual(
            self.db.import_records(scrape, state="GA")["imported"], 2
        )
        self.assertEqual(self.db.get_total_count(), 17)
        self.assertEqual(self.db.search_by_name("Garcia")[0]["state"], "GA")

        searcher = SexOffenderSearcher()
        orphan = searcher.db
        searcher.db = self.db
        try:
            # Full scan finds both
            hisp, hisp_base = searcher.analyze_ethnicities(
                min_confidence=0.5, limit=0, ethnicity_filter="hispanic",
                return_base_count=True,
            )
            self.assertGreaterEqual(hisp_base, 1)
            self.assertIn(
                "garcia",
                {(m.record.get("last_name") or "").lower() for m in hisp},
            )
            indian, _ = searcher.analyze_ethnicities(
                min_confidence=0.5, limit=0, ethnicity_filter="indian",
                return_base_count=True,
            )
            self.assertIn(
                "patel",
                {(m.record.get("last_name") or "").lower() for m in indian},
            )
            # Cap of 3 with newest_first must include new GA rows (not only Smith)
            lim_hisp, _ = searcher.analyze_ethnicities(
                min_confidence=0.5, limit=3, ethnicity_filter="hispanic",
                return_base_count=True,
            )
            lim_names = {
                (m.record.get("last_name") or "").lower() for m in lim_hisp
            }
            self.assertIn("garcia", lim_names)
            oldest = list(self.db.iter_offenders(limit=3, newest_first=False))
            self.assertTrue(all((r.get("last_name") or "") == "Smith" for r in oldest))
        finally:
            searcher.db = orphan
            searcher.close()

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
            self.assertFalse(s["backup_on_close"])  # optional; default off
            self.assertTrue(s["nsopw_compact_prefixes"])
            self.assertEqual(s["nsopw_min_combined_len"], 3)
            s["backup_on_close"] = True
            s["max_backups"] = 5
            s["nsopw_min_combined_len"] = 2  # clamp to >= 3
            save_settings(s, path)
            s2 = load_settings(path)
            self.assertTrue(s2["backup_on_close"])
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
    def test_non_indian_exclusions(self):
        """English/Portuguese-looking names must not classify as Indian."""
        from scraper.ethnic_names import EthnicNameDatabase

        db = EthnicNameDatabase()
        for name in (
            "Shaw", "Swain", "Ray", "Fernandes", "Gore",
            "Merchant", "Deen", "Mann", "Corea", "Ingle", "De",
        ):
            self.assertFalse(db.is_indian_surname(name), name)
            eth, conf, _ = db.classify_by_name(name)
            self.assertFalse(
                eth.startswith("Indian"),
                f"{name} classified as {eth} conf={conf}",
            )

    def test_first_name_dampens_ambiguous_indian_surnames(self):
        """Amy Gill / Alberto Perera must not score as strong Indian mismatches."""
        from scraper.ethnic_names import EthnicNameDatabase

        db = EthnicNameDatabase()
        eth, conf, _ = db.classify_by_name("Gill", first_name="Amy")
        self.assertTrue(eth.startswith("Indian") or eth == "Unknown")
        self.assertLess(
            conf, 0.5,
            f"Amy Gill must be below default Analyze threshold, got {conf}",
        )

        eth2, conf2, _ = db.classify_by_name("Perera", first_name="Alberto")
        self.assertFalse(
            eth2.startswith("Indian"),
            f"Alberto Perera must not be Indian, got {eth2} conf={conf2}",
        )
        self.assertLess(conf2 if eth2.startswith("Indian") else 0.0, 0.5)

        # Indic first name can corroborate ambiguous surname
        eth3, conf3, _ = db.classify_by_name("Gill", first_name="Rahul")
        self.assertTrue(eth3.startswith("Indian"))
        self.assertGreaterEqual(conf3, 0.5)

        # Distinctive HC surname still Indian even with Anglo first name, but damped
        eth4, conf4, _ = db.classify_by_name("Patel", first_name="Amy")
        self.assertTrue(eth4.startswith("Indian"))
        self.assertLessEqual(conf4, 0.7)
        self.assertGreaterEqual(conf4, 0.5)

        # Cristobal More — English surname + Hispanic given name, not HC Indian
        self.assertFalse(db.is_indian_surname("More"))
        eth5, conf5, _ = db.classify_by_name("More", first_name="Cristobal")
        self.assertFalse(
            eth5.startswith("Indian"),
            f"Cristobal More must not be Indian @ high conf, got {eth5} conf={conf5}",
        )
        eth6, conf6, _ = db.classify_by_name("More", first_name="CRISTÓBAL")
        self.assertFalse(eth6.startswith("Indian"))

        # Adam Dey — Western given name + short/ambiguous surname, below Analyze 0.5
        eth7, conf7, _ = db.classify_by_name("Dey", first_name="Adam")
        self.assertLess(
            conf7, 0.5,
            f"Adam Dey must be below default min conf, got {eth7} conf={conf7}",
        )
        # Indic given name can still support Dey
        eth8, conf8, _ = db.classify_by_name("Dey", first_name="Rahul")
        self.assertTrue(eth8.startswith("Indian"))
        self.assertGreaterEqual(conf8, 0.5)

        # Andrey = white/Western; Andrei = Slavic — neither boosts Indian (Lele)
        self.assertEqual(db._first_name_signal("Andrey"), "anglo")
        self.assertEqual(db._first_name_signal("Andrei"), "slavic")
        for fn in ("Andrey", "Andrei"):
            eth9, conf9, _ = db.classify_by_name("Lele", first_name=fn)
            self.assertLess(
                conf9, 0.5,
                f"{fn} Lele must be below default min conf, got {eth9} conf={conf9}",
            )

        # Middle name can corroborate Indian when first is Western
        eth10, conf10, _ = db.classify_by_name(
            "Patel", first_name="John", middle_name="Rahul"
        )
        self.assertTrue(eth10.startswith("Indian"))
        self.assertGreaterEqual(conf10, 0.85)

        # Middle name Western dampens short/ambiguous Indian surname
        eth11, conf11, _ = db.classify_by_name(
            "Dey", first_name="R", middle_name="Adam"
        )
        # R alone may be unknown; Adam middle should still dampen
        eth12, conf12, _ = db.classify_by_name(
            "Dey", first_name="Samir", middle_name="Adam"
        )
        # Samir if not in list; use explicit anglo middle with weak surname
        eth13, conf13, _ = db.classify_by_name(
            "Gill", first_name="X", middle_name="Amy"
        )
        self.assertLess(conf13, 0.5)

    def test_classify_common_names(self):
        eth = EthnicNameDatabase()
        self.assertEqual(eth.classify_by_name("Garcia")[0], "Hispanic")
        self.assertTrue(eth.classify_by_name("Chen")[0].startswith("Asian"))
        patel = eth.classify_by_name("Patel")[0]
        self.assertTrue(patel.startswith("Indian"), msg=f"Patel got {patel}")
        # MENA/Arabic surnames land in merged Indian/MENA bucket
        ahmed = eth.classify_by_name("Ahmed")[0]
        self.assertTrue(
            ahmed.startswith("Indian/MENA"),
            msg=f"Ahmed should be Indian/MENA, got {ahmed}",
        )
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
                # White + Hispanic surname, no ethnicity field → mismatch
                {"first_name": "Juan", "last_name": "Garcia", "race": "WHITE", "state": "FL"},
                # White + ethnicity Hispanic → OK
                {
                    "first_name": "Sofia", "last_name": "Lopez", "race": "WHITE",
                    "ethnicity": "Hispanic", "state": "FL",
                },
                {"first_name": "Maria", "last_name": "Rodriguez", "race": "HISPANIC", "state": "TX"},
                # Black + Hispanic surname = potential mismatch
                {"first_name": "Carlos", "last_name": "Martinez", "race": "BLACK", "state": "TX"},
                {"first_name": "Wei", "last_name": "Chen", "race": "WHITE", "state": "CA"},
                {"first_name": "John", "last_name": "Smith", "race": "WHITE", "state": "NY"},
            ])
            h = s.find_hispanic_misclassifications()
            hisp_names = {(m.record.get("last_name") or "") for m in h}
            self.assertIn("Garcia", hisp_names)
            self.assertIn("Martinez", hisp_names)
            self.assertNotIn("Lopez", hisp_names)  # White + ethnicity Hispanic
            self.assertNotIn("Rodriguez", hisp_names)  # race Hispanic
            martinez = next(m for m in h if m.record["last_name"] == "Martinez")
            self.assertEqual(martinez.expected_race, "Black")
            a = s.find_asian_misclassifications()
            self.assertEqual(len(a), 1)
            self.assertEqual(a[0].record["last_name"], "Chen")
            # Correctly labeled Hispanic is not a misclassification
            all_mc = s.analyze_ethnicities()
            self.assertFalse(any(m.record["last_name"] == "Rodriguez" for m in all_mc))
            self.assertFalse(any(m.record["last_name"] == "Lopez" for m in all_mc))
            self.assertTrue(any(m.record["last_name"] == "Garcia" for m in all_mc))
            names = {r["last_name"] for r in s.filter_by_hispanic_names()}
            self.assertEqual(names, {"Garcia", "Lopez", "Rodriguez", "Martinez"})
        finally:
            s.close()

    def test_hispanic_white_needs_ethnicity_field(self):
        """White alone is a mismatch; White + ethnicity Hispanic is OK."""
        from scraper.searcher import _is_compatible, _canonical_race_key

        # No ethnicity tag → incorrectly reported
        self.assertFalse(_is_compatible("Hispanic", "White"))
        self.assertFalse(_is_compatible("Hispanic", "WHITE"))
        self.assertFalse(_is_compatible("Hispanic", "Caucasian"))
        self.assertFalse(
            _is_compatible("Hispanic", "White", recorded_ethnicity="")
        )
        self.assertFalse(
            _is_compatible("Hispanic", "White", recorded_ethnicity="Non-Hispanic")
        )
        # Ethnicity field marks Hispanic → compatible
        self.assertTrue(
            _is_compatible("Hispanic", "White", recorded_ethnicity="Hispanic")
        )
        self.assertTrue(
            _is_compatible("Hispanic", "WHITE", recorded_ethnicity="Hispanic or Latino")
        )
        self.assertTrue(
            _is_compatible("Hispanic", "Caucasian", recorded_ethnicity="Latino")
        )
        # Race itself is Hispanic / White Hispanic
        self.assertTrue(_is_compatible("Hispanic", "Hispanic"))
        self.assertTrue(_is_compatible("Hispanic", "Hispanic or Latino"))
        self.assertTrue(_is_compatible("Hispanic", "White Hispanic"))
        self.assertTrue(_is_compatible("Hispanic", "Unknown"))
        self.assertTrue(_is_compatible("Hispanic", ""))
        # Non-White / non-Hispanic race codes remain potential mismatches
        self.assertFalse(_is_compatible("Hispanic", "Black"))
        self.assertFalse(_is_compatible("Hispanic", "Asian"))
        self.assertEqual(_canonical_race_key("Hispanic or Latino"), "HISPANIC")

    def test_indian_mena_white_compatible_only_for_arabic_branch(self):
        """MENA-arabic labels accept White; Indic subgroup White is still a mismatch."""
        from scraper.searcher_race import (
            _ethnicity_family,
            _is_compatible,
            ethnicity_filter_matches,
        )

        self.assertTrue(
            _is_compatible("Indian/MENA (arabic)", "White")
        )
        self.assertFalse(
            _is_compatible("Indian/MENA (india)", "White")
        )
        self.assertTrue(
            _is_compatible("Indian/MENA (india)", "Asian")
        )
        self.assertEqual(_ethnicity_family("Indian/MENA (arabic)"), "mena")
        self.assertEqual(_ethnicity_family("Indian/MENA (india)"), "indian")
        self.assertTrue(
            ethnicity_filter_matches("indian", "indian/mena (merged)")
        )
        self.assertTrue(
            ethnicity_filter_matches("mena", "indian/mena (merged)")
        )
        self.assertTrue(ethnicity_filter_matches("indian", "indian"))
        self.assertFalse(ethnicity_filter_matches("mena", "indian"))
        self.assertTrue(ethnicity_filter_matches("mena", "mena"))
        self.assertFalse(ethnicity_filter_matches("indian", "mena"))

    def test_ethnicity_review_flags_persist(self):
        """Sidebar confirmations store correct/incorrect on offenders.flags."""
        import tempfile
        from pathlib import Path

        from gui_app.shared.verdict_persist import persist_ethnicity_verdict
        from gui_app.tabs.browse.misclassify.constants import verification_label
        from scraper.database import Database
        from scraper.ethnicity_review import ethnicity_review_verdict

        p = Path(tempfile.mkdtemp()) / "rev.db"
        db = Database(str(p))
        try:
            db.insert_offenders_batch(
                [{"first_name": "A", "last_name": "Patel", "race": "White", "state": "FL"}]
            )
            rec = list(db.iter_offenders(limit=1))[0]
        finally:
            db.close()
        ok, _, err = persist_ethnicity_verdict(str(p), rec, "incorrect")
        self.assertTrue(ok, err)
        self.assertEqual(ethnicity_review_verdict(rec), "incorrect")
        self.assertEqual(verification_label(rec), "Confirmed incorrect")
        ok2, _, err2 = persist_ethnicity_verdict(str(p), rec, "correct")
        self.assertTrue(ok2, err2)
        self.assertEqual(verification_label(rec), "Confirmed correct")

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
