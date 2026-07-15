"""Unit tests for public DB delta keys, zip IO, and apply."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scraper.database import Database
from scraper.db_publish_diff import build_delta_ops, should_publish_full_base
from scraper.db_publish_index import replace_index, load_key_hashes
from scraper.db_publish_package import package_db_release
from scraper.db_sync_apply import apply_delta_ops, apply_delta_zip
from scraper.db_sync_delta_io import load_delta_ops, write_delta_zip
from scraper.db_sync_keys import row_content_hash, sync_record_key


class SyncKeyTests(unittest.TestCase):
    def test_url_key_stable(self):
        a = {
            "state": "FL",
            "source_url": "https://example.com/offender?Id=123&uid=session",
            "first_name": "Ann",
            "last_name": "Lee",
        }
        b = {
            "state": "FL",
            "source_url": "https://example.com/offender?Id=123&uid=other",
            "first_name": "Ann",
            "last_name": "Lee",
        }
        self.assertEqual(sync_record_key(a), sync_record_key(b))
        self.assertTrue(sync_record_key(a).startswith("k:"))

    def test_content_hash_changes(self):
        r1 = {"first_name": "A", "last_name": "B", "crime": "x"}
        r2 = {"first_name": "A", "last_name": "B", "crime": "y"}
        self.assertNotEqual(row_content_hash(r1), row_content_hash(r2))


class DeltaApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "t.db"
        self.db = Database(str(self.db_path))
        self.db.insert_offender(
            {
                "first_name": "Jane",
                "last_name": "Doe",
                "state": "TX",
                "source_url": "https://ex.test/o?Id=1",
                "crime": "old",
            }
        )
        self.db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_upsert_and_delete(self):
        key = sync_record_key(
            {
                "first_name": "Jane",
                "last_name": "Doe",
                "state": "TX",
                "source_url": "https://ex.test/o?Id=1",
            }
        )
        ops = [
            {
                "op": "upsert",
                "key": key,
                "row": {
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "state": "TX",
                    "source_url": "https://ex.test/o?Id=1",
                    "crime": "new",
                },
            },
            {
                "op": "upsert",
                "key": "s:deadbeefdeadbeefdeadbeef",
                "row": {
                    "first_name": "New",
                    "last_name": "Person",
                    "state": "GA",
                    "source_url": "https://ex.test/o?Id=99",
                    "crime": "z",
                },
            },
        ]
        up, de, err = apply_delta_ops(self.db_path, ops)
        self.assertEqual(err, 0)
        self.assertEqual(up, 2)
        conn = sqlite3.connect(str(self.db_path))
        crimes = {
            r[0]: r[1]
            for r in conn.execute("SELECT last_name, crime FROM offenders")
        }
        self.assertEqual(crimes.get("Doe"), "new")
        self.assertEqual(crimes.get("Person"), "z")
        # delete Jane
        apply_delta_ops(self.db_path, [{"op": "delete", "key": key}])
        n = conn.execute(
            "SELECT COUNT(*) FROM offenders WHERE last_name='Doe'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(n, 0)

    def test_delta_zip_roundtrip(self):
        zpath = Path(self.tmp.name) / "offenders.delta.0001.zip"
        key = sync_record_key(
            {
                "state": "TX",
                "source_url": "https://ex.test/o?Id=1",
            }
        )
        write_delta_zip(
            zpath,
            [
                {
                    "op": "upsert",
                    "key": key,
                    "row": {
                        "first_name": "Jane",
                        "last_name": "Doe",
                        "state": "TX",
                        "source_url": "https://ex.test/o?Id=1",
                        "crime": "fromzip",
                    },
                }
            ],
        )
        ops = load_delta_ops(zpath)
        self.assertEqual(len(ops), 1)
        apply_delta_zip(self.db_path, zpath)
        conn = sqlite3.connect(str(self.db_path))
        crime = conn.execute(
            "SELECT crime FROM offenders WHERE last_name='Doe'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(crime, "fromzip")


class PublishPackageTests(unittest.TestCase):
    def test_delta_then_noop(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "releases").mkdir()
            db = root / "scrub.db"
            d = Database(str(db))
            d.insert_offender(
                {
                    "first_name": "A",
                    "last_name": "One",
                    "state": "FL",
                    "source_url": "https://ex.test/o?Id=10",
                    "crime": "c1",
                }
            )
            d.close()

            r1 = package_db_release(root, db, photo_parts=[], full_base=True)
            self.assertEqual(r1["mode"], "base")
            self.assertTrue((root / "releases" / "offenders.db.zip").is_file())

            # change one row
            d = Database(str(db))
            d.insert_offender(
                {
                    "first_name": "B",
                    "last_name": "Two",
                    "state": "GA",
                    "source_url": "https://ex.test/o?Id=11",
                    "crime": "c2",
                }
            )
            d.close()
            r2 = package_db_release(root, db, photo_parts=[], full_base=False)
            self.assertEqual(r2["mode"], "delta")
            self.assertGreaterEqual(r2["ops"], 1)
            self.assertTrue(Path(r2["delta_path"]).is_file())

            r3 = package_db_release(root, db, photo_parts=[], full_base=False)
            self.assertEqual(r3["mode"], "noop")

    def test_should_full_base_threshold(self):
        self.assertTrue(should_publish_full_base(0, 100, force=True))
        self.assertTrue(should_publish_full_base(50, 100, has_prior_index=False))
        self.assertFalse(should_publish_full_base(10, 100_000, has_prior_index=True))
        self.assertTrue(should_publish_full_base(50_000, 100_000, has_prior_index=True))


if __name__ == "__main__":
    unittest.main()
