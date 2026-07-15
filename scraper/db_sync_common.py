"""Download / update the public offenders SQLite archive from GitHub.

Release assets (format 2):
  - ``offenders.db.zip`` base snapshot (+ ``MANIFEST.json``)
  - ``offenders.delta.NNNN.zip`` incremental upsert/delete packs
  - ``offenders.photos.NNN.zip`` mugshots under ``data/report_pages/*/photos/``

Clients only download. Upload is gated to the local publisher machine
(``data/db_publish.allow`` + ``scripts/publish_database_release.py``).

Default source: ``HyperboreanSlug/SORPA`` tag ``database-latest``.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_GITHUB_REPO = "HyperboreanSlug/SORPA"
DEFAULT_RELEASE_TAG = "database-latest"
DEFAULT_ASSET_NAME = "offenders.db.zip"
DEFAULT_MANIFEST_NAME = "MANIFEST.json"
DEFAULT_DB_REL = Path("data/offenders.db")
USER_AGENT = "SOR-Public-Archiver-DB-Sync/1.1"
PHOTO_ASSET_PREFIX = "offenders.photos."
DELTA_ASSET_PREFIX = "offenders.delta."


@dataclass
class SyncResult:
    ok: bool
    action: str  # skipped | downloaded | updated | error
    message: str
    record_count: Optional[int] = None
    sha256: Optional[str] = None
    bytes_written: int = 0
    photos_extracted: int = 0
    deltas_applied: int = 0


