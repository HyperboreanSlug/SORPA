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

    def test_qr_code_heuristic(self):
        """High-contrast square module grid → not a mugshot."""
        from scraper.mugshot_ethnicity.photo_quality import (
            clear_placeholder_cache,
            is_non_mugshot,
            non_mugshot_reason,
        )

        try:
            from PIL import Image, ImageDraw
        except ImportError:
            self.skipTest("Pillow required")

        clear_placeholder_cache()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "qr.jpg"
            # Synthetic QR-like: 125×125 checker of 5px modules, B/W only
            img = Image.new("L", (125, 125), color=255)
            draw = ImageDraw.Draw(img)
            mod = 5
            for y in range(0, 125, mod):
                for x in range(0, 125, mod):
                    if ((x // mod) + (y // mod)) % 2 == 0:
                        draw.rectangle((x, y, x + mod - 1, y + mod - 1), fill=0)
            # Finder-style solid blocks in corners (boost black + structure)
            for ox, oy in ((0, 0), (90, 0), (0, 90)):
                draw.rectangle((ox, oy, ox + 30, oy + 30), fill=0)
                draw.rectangle((ox + 5, oy + 5, ox + 25, oy + 25), fill=255)
                draw.rectangle((ox + 10, oy + 10, ox + 20, oy + 20), fill=0)
            img.save(p, format="JPEG", quality=95)
            self.assertTrue(is_non_mugshot(p))
            self.assertIn("QR", (non_mugshot_reason(p) or "").upper())

    def test_noimage_url_and_known_chrome_md5(self):
        """iCrimeWatch noimage URL + known shared chrome hashes are rejected."""
        from scraper.mugshot_ethnicity.photo_quality import (
            KNOWN_CHROME_MD5,
            clear_placeholder_cache,
            is_non_mugshot,
            url_looks_like_chrome,
        )
        from scraper.mugshot_ethnicity.photo_resolve import photo_usable_for_scan

        self.assertTrue(
            url_looks_like_chrome("https://icrimewatch.net/images/noimage.jpg")
        )
        self.assertTrue(
            url_looks_like_chrome(
                "https://example.com/photos/photo_not_available.png"
            )
        )
        self.assertFalse(
            url_looks_like_chrome(
                "https://offender.fdle.state.fl.us/offender/CallImage?imgID=123"
            )
        )
        # Known no-photo stub hash
        self.assertIn(
            "9850c1a72ea77853428c9bf906c57f44", KNOWN_CHROME_MD5
        )
        # SC help icon / WV badge hashes used as shared "mugshots"
        self.assertIn(
            "a94c8c8a42da56b64bdf75a7a47bbd49", KNOWN_CHROME_MD5
        )
        self.assertIn(
            "9ef0a2a39dad28f396d92f84a355ac8b", KNOWN_CHROME_MD5
        )

        clear_placeholder_cache()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "chrome.jpg"
            payload = b"\xff\xd8\xff" + b"\x22" * 2500
            p.write_bytes(payload)
            import hashlib

            digest = hashlib.md5(payload).hexdigest()
            self.assertTrue(photo_usable_for_scan(str(p)))
            KNOWN_CHROME_MD5.add(digest)
            try:
                clear_placeholder_cache()
                self.assertTrue(is_non_mugshot(p))
                self.assertFalse(photo_usable_for_scan(str(p)))
            finally:
                KNOWN_CHROME_MD5.discard(digest)
                clear_placeholder_cache()
            self.assertFalse(photo_usable_for_scan(None))
            self.assertFalse(photo_usable_for_scan(""))
            self.assertFalse(photo_usable_for_scan(str(Path(tmp) / "missing.jpg")))


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

    def test_scan_skips_known_chrome_photo(self):
        """Shared site chrome must never become a DeepFace hit."""
        from scraper.mugshot_ethnicity.photo_quality import (
            KNOWN_CHROME_MD5,
            clear_placeholder_cache,
        )

        chrome = self.root / "shared_chrome.jpg"
        payload = b"\xff\xd8\xff" + b"\x33" * 4000
        chrome.write_bytes(payload)
        import hashlib

        digest = hashlib.md5(payload).hexdigest()
        clear_placeholder_cache()
        KNOWN_CHROME_MD5.add(digest)
        try:
            self.db.insert_offenders_batch(
                [
                    {
                        "first_name": "ROBERT",
                        "last_name": "TAFTA",
                        "race": "WHITE",
                        "photo_path": str(chrome),
                        "state": "SC",
                    },
                    {
                        "first_name": "JOSE",
                        "last_name": "ARMENTA",
                        "race": "WHITE",
                        "photo_path": str(chrome),
                        "state": "SC",
                    },
                ]
            )
            sc = MugshotEthnicityScorer(backend=MockBackend())
            hits = scan_gross_misclassifications(
                db=self.db,
                scorer=sc,
                min_confidence=0.85,
                limit=50,
                force_rescan=True,
            )
            self.assertEqual(hits, [])
        finally:
            KNOWN_CHROME_MD5.discard(digest)
            clear_placeholder_cache()

    def test_recompute_hits_after_race_collapse(self):
        """Race collapsing Black|White → White ✓ must promote stored face hits."""
        photo = self._photo("subject_black__0.97.jpg")
        self.db.insert_offenders_batch(
            [
                {
                    "first_name": "MARLY",
                    "last_name": "CENELUS",
                    "race": "Black | White",
                    "photo_path": photo,
                    "state": "FL",
                }
            ]
        )
        oid = int(
            self.db._conn.execute(
                "SELECT id FROM offenders WHERE last_name='CENELUS'"
            ).fetchone()[0]
        )
        # Simulate first scan while multi-race (face black agrees with Black → not a hit)
        self.db.upsert_deepface_scan(
            oid,
            photo_path=photo,
            top_label="black",
            top_confidence=0.97,
            scores={"black": 0.97, "white": 0.03},
            backend="mock",
            is_hit=False,
            recorded_race="Black | White",
            predicted_label="black",
            scan_min_conf=0.85,
        )
        self.assertEqual(self.db.count_deepface_scans()["hits"], 0)
        # Later race display collapses to verified White only
        self.db._conn.execute(
            "UPDATE offenders SET race = ? WHERE id = ?",
            ("White ✓", oid),
        )
        self.db._conn.commit()
        stats = self.db.recompute_deepface_hits(
            recorded_races=["WHITE"],
            face_labels=["black", "indian", "asian"],
            min_confidence=0.85,
        )
        self.assertEqual(stats["promoted"], 1)
        self.assertEqual(self.db.count_deepface_scans()["hits"], 1)
        hits = self.db.list_deepface_hits(min_confidence=0.85, recompute=False)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].get("last_name"), "CENELUS")

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
