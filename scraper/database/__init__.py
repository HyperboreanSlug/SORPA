"""SQLite database package for offender records.

Public API matches the former scraper.database module:
    from scraper.database import Database, get_database, ...
"""
from __future__ import annotations

from typing import Optional

from scraper.database.constants import (
    SCHEMA_VERSION,
    DUPLICATE_STRATEGIES,
    DEFAULT_DEDUPE_STRATEGIES,
    DEFAULT_DB_PATH,
)
from scraper.database.backup import backup_database_file, _prune_backups
from scraper.database.schema import SchemaMixin
from scraper.database.inserts import InsertMixin
from scraper.database.queries import QueryMixin
from scraper.database.dedupe import DedupeMixin
from scraper.database.csv_io import CsvMixin
from scraper.database.deepface_scans import DeepfaceScanMixin, photo_fingerprint


class Database(
    SchemaMixin,
    InsertMixin,
    QueryMixin,
    DedupeMixin,
    CsvMixin,
    DeepfaceScanMixin,
):
    """SQLite database wrapper for sex offender records."""


def get_database(db_path: Optional[str] = None) -> Database:
    return Database(db_path)


__all__ = [
    "Database",
    "get_database",
    "backup_database_file",
    "photo_fingerprint",
    "SCHEMA_VERSION",
    "DUPLICATE_STRATEGIES",
    "DEFAULT_DEDUPE_STRATEGIES",
    "DEFAULT_DB_PATH",
]
