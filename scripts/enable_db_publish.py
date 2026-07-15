#!/usr/bin/env python3
"""Enable public DB upload on THIS machine only (creates data/db_publish.allow)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from scraper.db_publish_gate import enable_publish, read_allow_meta

    path = enable_publish(ROOT)
    meta = read_allow_meta(ROOT) or {}
    print(f"Publisher enabled: {path}")
    print(f"  hostname={meta.get('hostname')}")
    print("Do not copy data/db_publish.allow to other machines or releases.")
    print("Clients download only via the app; upload stays on this instance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
