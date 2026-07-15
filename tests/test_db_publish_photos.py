"""Stable photo-shard packing tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scraper.db_publish_photos import (
    choose_shard_count,
    shard_for_arc,
    write_photo_parts,
    TARGET_PHOTO_PART_BYTES,
)


class PhotoShardTests(unittest.TestCase):
    def test_shard_count_bounds(self):
        self.assertGreaterEqual(choose_shard_count(0), 8)
        n = choose_shard_count(TARGET_PHOTO_PART_BYTES * 20)
        self.assertLessEqual(n, 48)
        self.assertGreaterEqual(n, 8)

    def test_stable_shard_for_path(self):
        a = shard_for_arc("data/report_pages/FL/photos/a.jpg", 16)
        b = shard_for_arc("data/report_pages/FL/photos/a.jpg", 16)
        self.assertEqual(a, b)
        self.assertTrue(0 <= a < 16)

    def test_reuse_unchanged_shard(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            photos = root / "data" / "report_pages" / "TX" / "photos"
            photos.mkdir(parents=True)
            out = root / "releases"
            out.mkdir()
            files = []
            for i in range(6):
                p = photos / f"p{i}.jpg"
                p.write_bytes(b"\xff\xd8\xff" + bytes([i]) * 50_000)
                files.append(p)
            parts1 = write_photo_parts(root, files, out_dir=out, force_rebuild=True)
            self.assertGreaterEqual(len(parts1), 1)
            sha1 = {p["name"]: p["sha256"] for p in parts1}
            # Second pack without changes should reuse
            parts2 = write_photo_parts(root, files, out_dir=out, force_rebuild=False)
            sha2 = {p["name"]: p["sha256"] for p in parts2}
            self.assertEqual(sha1, sha2)
            # Add one new photo — at least one part changes, not necessarily all
            p_new = photos / "new.jpg"
            p_new.write_bytes(b"\xff\xd8\xff" + b"x" * 40_000)
            parts3 = write_photo_parts(
                root, files + [p_new], out_dir=out, force_rebuild=False
            )
            sha3 = {p["name"]: p["sha256"] for p in parts3}
            changed = [n for n in sha3 if sha3.get(n) != sha1.get(n)]
            self.assertGreaterEqual(len(changed), 1)


if __name__ == "__main__":
    unittest.main()
