"""Pending listing counter for auto-publish threshold."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scraper.db_publish_pending import (
    add_pending_listings,
    clear_pending_listings,
    get_pending_listings,
    should_publish,
)


class PendingListingsTests(unittest.TestCase):
    def test_add_and_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(get_pending_listings(root), 0)
            self.assertEqual(add_pending_listings(100, root), 100)
            self.assertEqual(add_pending_listings(2400, root), 2500)
            self.assertTrue(should_publish(2500, root))
            self.assertFalse(should_publish(2501, root))
            clear_pending_listings(root)
            self.assertEqual(get_pending_listings(root), 0)
            self.assertFalse(should_publish(1, root))


if __name__ == "__main__":
    unittest.main()
