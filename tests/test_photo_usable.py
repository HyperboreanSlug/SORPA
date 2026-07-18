"""Usable mugshot path checks for Reports Photos-only filter."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image


class UsableMugshotTests(unittest.TestCase):
    def test_missing_and_tiny(self):
        from gui_app.shared.export_card_photo import is_usable_mugshot_path

        self.assertFalse(is_usable_mugshot_path(""))
        self.assertFalse(is_usable_mugshot_path("data/does_not_exist.jpg"))
        with tempfile.TemporaryDirectory() as td:
            tiny = Path(td) / "tiny.jpg"
            tiny.write_bytes(b"not-an-image")
            self.assertFalse(is_usable_mugshot_path(tiny))

    def test_valid_jpeg(self):
        from gui_app.shared.export_card_photo import is_usable_mugshot_path

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ok.jpg"
            img = Image.new("RGB", (120, 160), (40, 50, 60))
            img.save(p, format="JPEG", quality=85)
            self.assertTrue(is_usable_mugshot_path(p))

    def test_truncated_jpeg_rejected(self):
        from gui_app.shared.export_card_photo import is_usable_mugshot_path

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trunc.jpg"
            buf = io.BytesIO()
            Image.new("RGB", (200, 240), (10, 20, 30)).save(
                buf, format="JPEG", quality=90
            )
            data = buf.getvalue()
            # Chop the file so PIL cannot fully decode
            p.write_bytes(data[: max(50, len(data) // 4)])
            self.assertFalse(is_usable_mugshot_path(p))


if __name__ == "__main__":
    unittest.main()
