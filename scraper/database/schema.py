"""Schema init and connection lifecycle for Database."""
from __future__ import annotations

import shutil

from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from scraper.database.constants import (
    SCHEMA_VERSION,
    DUPLICATE_STRATEGIES,
    DEFAULT_DEDUPE_STRATEGIES,
    _VOLATILE_URL_PARAMS,
    _MERGE_SEP,
    _MERGE_UNION_FIELDS,
    DEFAULT_DB_PATH,
    _OFFENDER_INSERT_COLUMNS,
    _OFFENDER_INSERT_SQL,
    _record_to_insert_tuple,
    _utc_now_iso,
    _escape_like,
)


class SchemaMixin:
    def __init__(
        self,
        db_path: Optional[str] = None,
        *,
        busy_timeout_ms: int = 8000,
    ):
        # check_same_thread=False: GUI workers + CLI importers share one connection
        # under application-level coordination (same pattern as mapa).
        # Keep busy_timeout modest so a locked DB cannot freeze a thread for a minute
        # (UI must not open connections on the main thread either).
        timeout_s = max(0.5, float(busy_timeout_ms) / 1000.0)
        if db_path == ":memory:":
            self.db_path = Path(":memory:")
            self._conn = sqlite3.connect(
                ":memory:", check_same_thread=False, timeout=timeout_s
            )
        else:
            self.db_path = Path(db_path) if db_path else Path(DEFAULT_DB_PATH)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False, timeout=timeout_s
            )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        self._init_schema()

    def _init_schema(self):
        """Create tables if they don't exist."""
        cursor = self._conn.cursor()

        # Schema version tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create base table first so upgrades can ALTER it safely
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS offenders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                -- Personal info
                first_name TEXT,
                middle_name TEXT,
                last_name TEXT,
                full_name TEXT,
                race TEXT,
                ethnicity TEXT,
                gender TEXT,
                age INTEGER,
                date_of_birth TEXT,

                -- Physical description
                height TEXT,
                weight TEXT,
                eye_color TEXT,
                hair_color TEXT,
                build TEXT,
                skin_tone TEXT,

                -- Location info
                state TEXT,
                county TEXT,
                city TEXT,
                address TEXT,
                zip_code TEXT,
                latitude REAL,
                longitude REAL,

                -- Offense info
                offense_type TEXT,
                offense_description TEXT,
                crime TEXT,
                risk_level TEXT,
                conviction_date TEXT,
                registration_date TEXT,
                last_verified TEXT,

                -- Metadata
                source_state TEXT,
                source_url TEXT,
                scraped_at TEXT DEFAULT CURRENT_TIMESTAMP,
                external_id TEXT,
                raw_data_json TEXT,

                -- For misclassification detection
                likely_ethnicity TEXT,
                name_confidence REAL,
                flags TEXT,

                -- Local archive of the jurisdiction report HTML (for validation)
                report_html_path TEXT,
                -- Local photo archive + remote URL
                photo_path TEXT,
                photo_url TEXT
            )
        """)

        # Apply migrations BEFORE indexes that depend on newer columns.
        # CREATE TABLE IF NOT EXISTS does not add columns to an existing older table.
        current_version = 0
        for row in cursor.execute("SELECT MAX(version) FROM schema_version"):
            current_version = row[0] or 0

        # Always reconcile missing columns (safe if already present).
        # Handles DBs that failed mid-init before schema_version was bumped.
        self._ensure_offender_columns(cursor)

        if current_version < SCHEMA_VERSION:
            self._upgrade_schema(cursor, current_version)
            cursor.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _utc_now_iso())
            )

        # Indexes after columns exist (old DBs upgrade first)
        self._ensure_indexes(cursor)

        # DeepFace scan persistence (skip already-scored mugshots)
        try:
            self._ensure_deepface_scans_table(cursor)
        except Exception:
            # Mixin may not be on class during partial imports; table created lazily
            pass

        # Note: FTS5 was previously created but never queried and had no sync
        # triggers — removed to avoid a false sense of full-text search and
        # avoid extra schema work on every open.

        self._conn.commit()

    def _ensure_offender_columns(self, cursor: sqlite3.Cursor) -> None:
        """Add any missing columns introduced after the original schema."""
        cols = {row[1] for row in cursor.execute("PRAGMA table_info(offenders)")}
        additions = {
            "report_html_path": "TEXT",
            "photo_path": "TEXT",
            "photo_url": "TEXT",
            "crime": "TEXT",
            "likely_ethnicity": "TEXT",
            "name_confidence": "REAL",
            "flags": "TEXT",
            "raw_data_json": "TEXT",
            "external_id": "TEXT",
            "middle_name": "TEXT",
            "sources_json": "TEXT",
        }
        for name, typ in additions.items():
            if name not in cols:
                cursor.execute(f"ALTER TABLE offenders ADD COLUMN {name} {typ}")

    def _ensure_indexes(self, cursor: sqlite3.Cursor) -> None:
        """Create search indexes only for columns that exist."""
        cols = {row[1] for row in cursor.execute("PRAGMA table_info(offenders)")}
        index_cols = {
            "idx_offenders_last_name": "last_name",
            "idx_offenders_first_name": "first_name",
            "idx_offenders_race": "race",
            "idx_offenders_state": "state",
            "idx_offenders_county": "county",
            "idx_offenders_risk_level": "risk_level",
            "idx_offenders_source_state": "source_state",
            "idx_offenders_source_url": "source_url",
            "idx_offenders_external_id": "external_id",
            "idx_offenders_report_html": "report_html_path",
            "idx_offenders_photo": "photo_path",
        }
        for idx_name, col in index_cols.items():
            if col in cols:
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON offenders({col})"
                )

    def _upgrade_schema(self, cursor: sqlite3.Cursor, from_version: int):
        """Upgrade schema to current version (version bookkeeping + column adds)."""
        # Columns are applied via _ensure_offender_columns; keep hooks for future work.
        if from_version < 1:
            pass
        if from_version < 2:
            pass
        if from_version < 3:
            pass
        if from_version < 4:
            pass
        if from_version < 5:
            # middle_name column via _ensure_offender_columns
            pass
        if from_version < 6:
            # sources_json multi-source provenance via _ensure_offender_columns
            pass
        if from_version < 7:
            # deepface_scans table for mugshot scan persistence
            try:
                self._ensure_deepface_scans_table(cursor)
            except Exception:
                pass

    def close(self):
        """Close the database connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def checkpoint(self) -> None:
        """Flush WAL into the main DB file (best-effort)."""
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.commit()
        except Exception:
            pass

    def backup_to(self, dest: Path, *, verify: bool = True) -> Path:
        """
        Online backup of this database to *dest* using SQLite's backup API.

        Writes to a temporary file first, optionally runs integrity_check, then
        renames into place so a failed backup never leaves a corrupt final file.
        """
        dest = Path(dest)
        if str(self.db_path) == ":memory:":
            raise ValueError("Cannot backup an in-memory database to a path")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        dest_conn = sqlite3.connect(str(tmp))
        try:
            with dest_conn:
                self._conn.backup(dest_conn)
            if verify:
                row = dest_conn.execute("PRAGMA integrity_check").fetchone()
                ok = row and str(row[0]).lower() == "ok"
                if not ok:
                    raise RuntimeError(
                        f"Backup integrity_check failed: {row[0] if row else 'unknown'}"
                    )
        except Exception:
            try:
                dest_conn.close()
            except Exception:
                pass
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise
        else:
            dest_conn.close()

        # Atomic replace where the OS allows it
        try:
            tmp.replace(dest)
        except OSError:
            if dest.exists():
                dest.unlink()
            shutil.move(str(tmp), str(dest))
        return dest

    def existing_source_urls(self) -> set:
        """Load all non-empty source_url values (for bulk dedupe).

        Includes both raw URLs and normalized forms so session ``uid`` variants
        of the same page are treated as already present.
        """
        rows = self._conn.execute(
            "SELECT source_url FROM offenders "
            "WHERE source_url IS NOT NULL AND TRIM(source_url) != ''"
        ).fetchall()
        out: set = set()
        for r in rows:
            if not r or not r[0]:
                continue
            raw = str(r[0]).strip()
            if not raw:
                continue
            out.add(raw)
            norm = self.normalize_identity_url(raw)
            if norm:
                out.add(norm)
        return out

    def iter_offenders(
        self,
        limit: Optional[int] = None,
        offset: int = 0,
        *,
        newest_first: bool = False,
    ):
        """Stream offender rows (dicts) without loading the whole table at once.

        When *limit* is set, ``newest_first=True`` scans highest ids first so
        recent scrapes/imports are included in misclassification Analyze.
        """
        offset = max(0, int(offset or 0))
        order = "DESC" if newest_first else "ASC"
        if limit is None or int(limit) <= 0:
            sql = f"SELECT * FROM offenders ORDER BY id {order}"
            params: tuple = ()
            if offset:
                sql += " LIMIT -1 OFFSET ?"
                params = (offset,)
        else:
            sql = f"SELECT * FROM offenders ORDER BY id {order} LIMIT ? OFFSET ?"
            params = (int(limit), offset)
        cur = self._conn.execute(sql, params)
        for row in cur:
            yield dict(row)

    @classmethod
    def create_in_memory(cls) -> "Database":
        """Create an in-memory database (useful for testing)."""
        return cls(db_path=":memory:")

