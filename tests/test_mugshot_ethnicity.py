"""Mugshot ethnicity module tests (mock backend — no GPU/deps)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scraper.database import Database
from scraper.mugshot_ethnicity.backends import MockBackend
from scraper.mugshot_ethnicity.labels import (
    face_contradicts_recorded,
    name_ethnicity_to_face_labels,
    normalize_face_label,
    registry_race_to_face_labels,
)
from scraper.mugshot_ethnicity.scorer import MugshotEthnicityScorer
from scraper.mugshot_ethnicity.scanner import scan_gross_misclassifications
from scraper.mugshot_ethnicity.verify import verify_misclassifications, verify_record
from scraper.searcher import Misclassification


class LabelMapTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_face_label("latino hispanic"), "hispanic")
        self.assertEqual(normalize_face_label("South Asian"), "indian")
        self.assertEqual(normalize_face_label("african american"), "black")

    def test_registry_and_name_maps(self):
        self.assertIn("white", registry_race_to_face_labels("WHITE"))
        self.assertIn("black", registry_race_to_face_labels("Black"))
        self.assertTrue(face_contradicts_recorded("black", "WHITE"))
        self.assertFalse(face_contradicts_recorded("white", "WHITE"))
        self.assertIn("indian", name_ethnicity_to_face_labels("Indian"))


class MockScorerTests(unittest.TestCase):
    def test_mock_path_encoding(self):
        sc = MugshotEthnicityScorer(backend=MockBackend())
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "black__0.91.jpg"
            p.write_bytes(b"\xff\xd8\xff" + b"\x00" * 2000)  # fake jpeg header + size
            s = sc.score_path(str(p))
            self.assertEqual(s.top_label, "black")
            self.assertGreaterEqual(s.top_confidence, 0.9)

    def test_known_placeholder_md5_skipped(self):
        from scraper.mugshot_ethnicity.photo_quality import (
            KNOWN_PLACEHOLDER_MD5,
            clear_placeholder_cache,
            is_placeholder_photo,
            placeholder_reason,
        )

        clear_placeholder_cache()
        # Build a file whose MD5 matches the known CO silhouette hash
        # by writing exact bytes from a tiny synthetic only if we inject MD5.
        # Instead, monkeypatch: write any file and check known set membership via
        # is_placeholder_photo after putting real hash in KNOWN set for this file.
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "stub.jpg"
            payload = b"\xff\xd8\xff" + b"\x11" * 3000
            p.write_bytes(payload)
            import hashlib

            digest = hashlib.md5(payload).hexdigest()
            self.assertFalse(is_placeholder_photo(p))
            KNOWN_PLACEHOLDER_MD5.add(digest)
            try:
                clear_placeholder_cache()
                self.assertTrue(is_placeholder_photo(p))
                self.assertIn("known stub", placeholder_reason(p) or "")
                sc = MugshotEthnicityScorer(backend=MockBackend())
                s = sc.score_path(str(p))
                self.assertFalse(s.ok)
                self.assertIn("placeholder", (s.error or "").lower())
            finally:
                KNOWN_PLACEHOLDER_MD5.discard(digest)
                clear_placeholder_cache()

    def test_silhouette_heuristic(self):
        """White background + dark outline → placeholder."""
        from scraper.mugshot_ethnicity.photo_quality import (
            clear_placeholder_cache,
            is_placeholder_photo,
        )

        try:
            from PIL import Image, ImageDraw
        except ImportError:
            self.skipTest("Pillow required")

        clear_placeholder_cache()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "silhouette.jpg"
            # 200x200 mostly white with a thick black head outline
            img = Image.new("L", (200, 200), color=255)
            draw = ImageDraw.Draw(img)
            draw.ellipse((50, 30, 150, 140), outline=0, width=8)
            draw.rectangle((85, 140, 115, 190), outline=0, width=6)
            img.save(p, format="JPEG", quality=90)
            self.assertTrue(is_placeholder_photo(p))


class VerifyAndScanTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _photo(self, name: str) -> str:
        p = self.root / name
        p.write_bytes(b"\xff\xd8\xff" + b"\x00" * 3000)
        return str(p)

    def test_verify_confirms_indian_marked_white(self):
        photo = self._photo("indian__0.93.jpg")
        rec = {
            "id": 1,
            "first_name": "RAJ",
            "last_name": "PATEL",
            "race": "WHITE",
            "photo_path": photo,
            "state": "FL",
        }
        sc = MugshotEthnicityScorer(backend=MockBackend())
        r = verify_record(
            rec,
            scorer=sc,
            name_ethnicity="Indian",
            name_confidence=0.9,
            face_min_conf=0.75,
            combined_min_conf=0.8,
        )
        self.assertEqual(r.face.top_label, "indian")
        self.assertTrue(r.confirms_misclass)
        self.assertEqual(r.verdict, "disagree")

    def test_verify_white_face_supports_recorded(self):
        photo = self._photo("white__0.88.jpg")
        rec = {
            "first_name": "JOHN",
            "last_name": "SMITH",
            "race": "WHITE",
            "photo_path": photo,
        }
        sc = MugshotEthnicityScorer(backend=MockBackend())
        r = verify_record(
            rec,
            scorer=sc,
            name_ethnicity="European",
            name_confidence=0.6,
        )
        self.assertTrue(r.supports_recorded or r.verdict == "agree")

    def test_scan_finds_black_marked_white(self):
        black_photo = self._photo("subject_black__0.95.jpg")
        white_photo = self._photo("subject_white__0.90.jpg")
        self.db.insert_offenders_batch(
            [
                {
                    "first_name": "A",
                    "last_name": "One",
                    "race": "WHITE",
                    "photo_path": black_photo,
                    "state": "TX",
                },
                {
                    "first_name": "B",
                    "last_name": "Two",
                    "race": "WHITE",
                    "photo_path": white_photo,
                    "state": "TX",
                },
                {
                    "first_name": "C",
                    "last_name": "Three",
                    "race": "BLACK",
                    "photo_path": black_photo,
                    "state": "TX",
                },
            ]
        )
        sc = MugshotEthnicityScorer(backend=MockBackend())
        hits = scan_gross_misclassifications(
            db=self.db,
            scorer=sc,
            min_confidence=0.85,
            limit=50,
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].predicted_label, "black")
        self.assertEqual(hits[0].record["last_name"], "One")

    def test_verify_misclass_list(self):
        photo = self._photo("indian__0.92.jpg")
        mc = Misclassification(
            record={
                "first_name": "AMIT",
                "last_name": "SHARMA",
                "race": "WHITE",
                "photo_path": photo,
            },
            expected_race="White",
            likely_ethnicity="Indian",
            confidence=0.88,
            matching_names=["Sharma"],
        )
        sc = MugshotEthnicityScorer(backend=MockBackend())
        out = verify_misclassifications([mc], scorer=sc)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].confirms_misclass)


if __name__ == "__main__":
    unittest.main()
