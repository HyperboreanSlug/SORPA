"""
Database layer for storing and querying sex offender records.

Uses SQLite with indexes on name and race columns for fast searching.
Supports both direct CSV import and record-by-record insertion.
"""

import re
import shutil
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone


# Schema version - increment when schema changes
SCHEMA_VERSION = 4

# Default database path (relative to project root)
DEFAULT_DB_PATH = "data/offenders.db"

# Columns written by insert helpers (must match INSERT placeholders 1:1)
_OFFENDER_INSERT_COLUMNS = (
    "first_name", "last_name", "full_name", "race", "ethnicity", "gender",
    "age", "date_of_birth", "height", "weight", "eye_color", "hair_color", "build", "skin_tone",
    "state", "county", "city", "address", "zip_code", "latitude", "longitude",
    "offense_type", "offense_description", "crime", "risk_level", "conviction_date",
    "registration_date", "last_verified", "source_state", "source_url", "external_id", "raw_data_json",
    "likely_ethnicity", "name_confidence", "flags",
    "report_html_path",
    "photo_path",
    "photo_url",
)
_OFFENDER_INSERT_SQL = (
    "INSERT INTO offenders ("
    + ", ".join(_OFFENDER_INSERT_COLUMNS)
    + ") VALUES ("
    + ", ".join("?" * len(_OFFENDER_INSERT_COLUMNS))
    + ")"
)


def _record_to_insert_tuple(record: Dict[str, Any]) -> tuple:
    """Map a record dict to the INSERT column order."""
    return tuple(record.get(col) for col in _OFFENDER_INSERT_COLUMNS)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _escape_like(value: str) -> str:
    """Escape \\, %, and _ for use with SQLite LIKE ... ESCAPE '\\'."""
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


class Database:
    """SQLite database wrapper for sex offender records."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path == ":memory:":
            self.db_path = Path(":memory:")
            self._conn = sqlite3.connect(":memory:")
        else:
            self.db_path = Path(db_path) if db_path else Path(DEFAULT_DB_PATH)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency
        self._conn.execute("PRAGMA foreign_keys=ON")
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
        """Load all non-empty source_url values (for bulk dedupe)."""
        rows = self._conn.execute(
            "SELECT source_url FROM offenders "
            "WHERE source_url IS NOT NULL AND TRIM(source_url) != ''"
        ).fetchall()
        return {str(r[0]).strip() for r in rows if r and r[0]}

    def iter_offenders(self, limit: Optional[int] = None, offset: int = 0):
        """Stream offender rows (dicts) without loading the whole table at once."""
        offset = max(0, int(offset or 0))
        if limit is None or int(limit) <= 0:
            sql = "SELECT * FROM offenders ORDER BY id ASC"
            params: tuple = ()
            if offset:
                sql += " LIMIT -1 OFFSET ?"
                params = (offset,)
        else:
            sql = "SELECT * FROM offenders ORDER BY id ASC LIMIT ? OFFSET ?"
            params = (int(limit), offset)
        cur = self._conn.execute(sql, params)
        for row in cur:
            yield dict(row)

    @classmethod
    def create_in_memory(cls) -> "Database":
        """Create an in-memory database (useful for testing)."""
        return cls(db_path=":memory:")

    # ---- Insert operations ----

    def insert_offender(self, record: Dict[str, Any]) -> int:
        """Insert a single offender record. Returns the row id."""
        cursor = self._conn.cursor()
        cursor.execute(_OFFENDER_INSERT_SQL, _record_to_insert_tuple(record))
        self._conn.commit()
        return cursor.lastrowid

    def insert_offenders_batch(self, records: List[Dict[str, Any]]) -> int:
        """Insert multiple offender records. Returns count inserted."""
        if not records:
            return 0
        cursor = self._conn.cursor()
        cursor.executemany(
            _OFFENDER_INSERT_SQL,
            [_record_to_insert_tuple(r) for r in records],
        )
        self._conn.commit()
        return cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else len(records)

    # ---- Query operations ----

    def search_by_name(
        self,
        name: str,
        state: Optional[str] = None,
        race: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Search offenders by name (partial match on first and last)."""
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        query = "SELECT * FROM offenders WHERE 1=1"
        params: List[Any] = []

        # Escape LIKE metacharacters so user input is literal (not wildcards)
        escaped = _escape_like(name or "")
        search_term = f"%{escaped}%"
        query += (
            " AND (full_name LIKE ? ESCAPE '\\' OR first_name LIKE ? ESCAPE '\\' "
            "OR last_name LIKE ? ESCAPE '\\')"
        )
        params.extend([search_term, search_term, search_term])

        if state and state.upper() != "ALL":
            query += " AND UPPER(state) = UPPER(?)"
            params.append(state)

        if race:
            query += " AND UPPER(race) = UPPER(?)"
            params.append(race)

        query += " ORDER BY last_name ASC, first_name ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def search_by_race(
        self,
        race: str,
        state: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Search offenders by race (case-insensitive)."""
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        query = "SELECT * FROM offenders WHERE UPPER(race) = UPPER(?)"
        params: List[Any] = [race]

        if state and state.upper() != "ALL":
            query += " AND UPPER(state) = UPPER(?)"
            params.append(state)

        query += " ORDER BY last_name ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def search_by_state(
        self,
        state: str,
        limit: int = 1000,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Search offenders by state. Use state='ALL' (or empty) to return any state."""
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        if not state or state.upper() == "ALL":
            query = "SELECT * FROM offenders ORDER BY last_name ASC LIMIT ? OFFSET ?"
            rows = self._conn.execute(query, (limit, offset)).fetchall()
        else:
            query = (
                "SELECT * FROM offenders WHERE UPPER(state) = UPPER(?) "
                "ORDER BY last_name ASC LIMIT ? OFFSET ?"
            )
            rows = self._conn.execute(query, (state, limit, offset)).fetchall()
        return [dict(row) for row in rows]

    def get_race_distribution(self) -> List[Dict[str, Any]]:
        """Get count of offenders by race."""
        query = """
            SELECT race, COUNT(*) as count
            FROM offenders
            GROUP BY race
            ORDER BY count DESC
        """
        rows = self._conn.execute(query).fetchall()
        return [dict(row) for row in rows]

    def get_state_distribution(self) -> List[Dict[str, Any]]:
        """Get count of offenders by state."""
        query = """
            SELECT state, COUNT(*) as count
            FROM offenders
            GROUP BY state
            ORDER BY count DESC
        """
        rows = self._conn.execute(query).fetchall()
        return [dict(row) for row in rows]

    def get_total_count(self) -> int:
        """Get total number of offender records."""
        result = self._conn.execute("SELECT COUNT(*) FROM offenders").fetchone()
        return result[0] if result else 0

    def get_offender_by_id(self, row_id: int) -> Optional[Dict[str, Any]]:
        """Return one offender row by primary key."""
        row = self._conn.execute(
            "SELECT * FROM offenders WHERE id = ?", (int(row_id),)
        ).fetchone()
        return dict(row) if row else None

    def update_offender(self, row_id: int, fields: Dict[str, Any]) -> bool:
        """Update selected columns on an offender row. Returns True if a row changed."""
        if not fields:
            return False
        # Only allow known columns
        allowed = set(_OFFENDER_INSERT_COLUMNS) | {"scraped_at"}
        cols = [k for k in fields if k in allowed and k != "id"]
        if not cols:
            return False
        sets = ", ".join(f"{c} = ?" for c in cols)
        vals = [fields[c] for c in cols]
        vals.append(int(row_id))
        cur = self._conn.execute(
            f"UPDATE offenders SET {sets} WHERE id = ?",
            vals,
        )
        self._conn.commit()
        return (cur.rowcount or 0) > 0

    def get_integrity_report(self) -> Dict[str, Any]:
        """
        Coverage stats for archive quality: race, crime, photo, HTML by state.

        Returns:
          {
            total, with_race, with_crime, with_photo, with_html, with_url,
            by_state: [{state, total, with_race, with_crime, with_photo, with_html, ...pct}],
          }
        """
        def _pct(n: int, d: int) -> float:
            return round(100.0 * n / d, 1) if d else 0.0

        total = self.get_total_count()
        row = self._conn.execute(
            """
            SELECT
              SUM(CASE WHEN race IS NOT NULL AND TRIM(race) != '' THEN 1 ELSE 0 END) AS with_race,
              SUM(CASE WHEN
                    (crime IS NOT NULL AND TRIM(crime) != '')
                    OR (offense_description IS NOT NULL AND TRIM(offense_description) != '')
                    OR (offense_type IS NOT NULL AND TRIM(offense_type) != '')
                  THEN 1 ELSE 0 END) AS with_crime,
              SUM(CASE WHEN photo_path IS NOT NULL AND TRIM(photo_path) != '' THEN 1 ELSE 0 END) AS with_photo,
              SUM(CASE WHEN report_html_path IS NOT NULL AND TRIM(report_html_path) != '' THEN 1 ELSE 0 END) AS with_html,
              SUM(CASE WHEN source_url IS NOT NULL AND TRIM(source_url) != '' THEN 1 ELSE 0 END) AS with_url,
              SUM(CASE WHEN
                    race IS NOT NULL AND TRIM(race) != ''
                    AND (
                      (crime IS NOT NULL AND TRIM(crime) != '')
                      OR (offense_description IS NOT NULL AND TRIM(offense_description) != '')
                      OR (offense_type IS NOT NULL AND TRIM(offense_type) != '')
                    )
                    AND photo_path IS NOT NULL AND TRIM(photo_path) != ''
                    AND report_html_path IS NOT NULL AND TRIM(report_html_path) != ''
                  THEN 1 ELSE 0 END) AS with_everything
            FROM offenders
            """
        ).fetchone()
        overall = {
            "total": total,
            "with_race": int(row["with_race"] or 0) if row else 0,
            "with_crime": int(row["with_crime"] or 0) if row else 0,
            "with_photo": int(row["with_photo"] or 0) if row else 0,
            "with_html": int(row["with_html"] or 0) if row else 0,
            "with_url": int(row["with_url"] or 0) if row else 0,
            "with_everything": int(row["with_everything"] or 0) if row else 0,
        }
        for key in ("with_race", "with_crime", "with_photo", "with_html", "with_url", "with_everything"):
            overall[f"pct_{key[5:]}"] = _pct(overall[key], total)

        by_state_rows = self._conn.execute(
            """
            SELECT
              COALESCE(NULLIF(TRIM(UPPER(state)), ''), NULLIF(TRIM(UPPER(source_state)), ''), 'UNK') AS st,
              COUNT(*) AS total,
              SUM(CASE WHEN race IS NOT NULL AND TRIM(race) != '' THEN 1 ELSE 0 END) AS with_race,
              SUM(CASE WHEN
                    (crime IS NOT NULL AND TRIM(crime) != '')
                    OR (offense_description IS NOT NULL AND TRIM(offense_description) != '')
                    OR (offense_type IS NOT NULL AND TRIM(offense_type) != '')
                  THEN 1 ELSE 0 END) AS with_crime,
              SUM(CASE WHEN photo_path IS NOT NULL AND TRIM(photo_path) != '' THEN 1 ELSE 0 END) AS with_photo,
              SUM(CASE WHEN report_html_path IS NOT NULL AND TRIM(report_html_path) != '' THEN 1 ELSE 0 END) AS with_html,
              SUM(CASE WHEN source_url IS NOT NULL AND TRIM(source_url) != '' THEN 1 ELSE 0 END) AS with_url
            FROM offenders
            GROUP BY st
            ORDER BY total DESC
            """
        ).fetchall()
        by_state = []
        for r in by_state_rows:
            d = dict(r)
            t = int(d.get("total") or 0)
            entry = {
                "state": d.get("st") or "UNK",
                "total": t,
                "with_race": int(d.get("with_race") or 0),
                "with_crime": int(d.get("with_crime") or 0),
                "with_photo": int(d.get("with_photo") or 0),
                "with_html": int(d.get("with_html") or 0),
                "with_url": int(d.get("with_url") or 0),
            }
            entry["pct_race"] = _pct(entry["with_race"], t)
            entry["pct_crime"] = _pct(entry["with_crime"], t)
            entry["pct_photo"] = _pct(entry["with_photo"], t)
            entry["pct_html"] = _pct(entry["with_html"], t)
            by_state.append(entry)

        return {"overall": overall, "by_state": by_state}

    def find_incomplete_reports(
        self,
        *,
        need_race: bool = True,
        need_crime: bool = True,
        need_photo: bool = True,
        need_html: bool = False,
        require_url: bool = True,
        limit: int = 500,
        state: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Records that have a report URL but are missing selected enrichments.
        Used for failed-report requeue.
        """
        limit = max(1, min(int(limit), 5000))
        clauses = ["1=1"]
        params: List[Any] = []
        if require_url:
            clauses.append("source_url IS NOT NULL AND TRIM(source_url) != ''")
        missing = []
        if need_race:
            missing.append("(race IS NULL OR TRIM(race) = '')")
        if need_crime:
            missing.append(
                "("
                "(crime IS NULL OR TRIM(crime) = '') AND "
                "(offense_description IS NULL OR TRIM(offense_description) = '') AND "
                "(offense_type IS NULL OR TRIM(offense_type) = '')"
                ")"
            )
        if need_photo:
            missing.append("(photo_path IS NULL OR TRIM(photo_path) = '')")
        if need_html:
            missing.append("(report_html_path IS NULL OR TRIM(report_html_path) = '')")
        if missing:
            clauses.append("(" + " OR ".join(missing) + ")")
        if state and state.upper() != "ALL":
            clauses.append("UPPER(COALESCE(state, source_state, '')) = UPPER(?)")
            params.append(state)
        sql = (
            f"SELECT * FROM offenders WHERE {' AND '.join(clauses)} "
            f"ORDER BY id DESC LIMIT ?"
        )
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ---- Ethnicity analysis ----

    def find_misclassifications(
        self,
        expected_race: str,
        likely_ethnicities: Optional[List[str]] = None,
        min_confidence: float = 0.5,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Find offenders whose stored likely_ethnicity differs from recorded race.

        Note: most analysis uses SexOffenderSearcher.analyze_ethnicities() which
        classifies names at query time. This method only queries pre-computed columns.
        """
        params: List[Any] = [min_confidence]
        if likely_ethnicities is None:
            query = """
                SELECT * FROM offenders
                WHERE likely_ethnicity IS NOT NULL
                    AND name_confidence >= ?
                    AND (race IS NULL OR UPPER(likely_ethnicity) != UPPER(race))
                ORDER BY name_confidence DESC
                LIMIT ?
            """
            params.append(limit)
        else:
            placeholders = ",".join(["?"] * len(likely_ethnicities))
            query = f"""
                SELECT * FROM offenders
                WHERE likely_ethnicity IN ({placeholders})
                    AND name_confidence >= ?
                ORDER BY name_confidence DESC
                LIMIT ?
            """
            params = list(likely_ethnicities) + [min_confidence, limit]
        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    # ---- CSV import/export ----

    def import_csv(
        self,
        csv_path: str,
        state: Optional[str] = None,
        *,
        skip_existing_urls: bool = True,
    ) -> Dict[str, int]:
        """
        Import records from a CSV file.

        Returns dict: {imported, skipped, total_rows}.
        When skip_existing_urls is True, rows with a source_url already in the DB
        are skipped (avoids duplicates from re-importing scrape downloads).
        """
        import csv as csv_module

        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_path}")

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv_module.DictReader(f)
            records = []
            for row in reader:
                record = dict(row)
                self._normalize_record(record)
                if state:
                    record["state"] = state
                    record.setdefault("source_state", state)
                # Infer source_state from filename like fl_offenders.csv / ga_offenders.csv
                if not record.get("source_state") and not record.get("state"):
                    stem = path.stem.lower().replace("_offenders", "").replace("_data", "")
                    if len(stem) == 2 and stem.isalpha():
                        record["state"] = stem.upper()
                        record["source_state"] = stem.upper()
                elif not record.get("source_state") and record.get("state"):
                    record["source_state"] = record["state"]
                # Crime aliases
                if not record.get("crime"):
                    record["crime"] = (
                        record.get("offense_description")
                        or record.get("offense_type")
                        or record.get("offense")
                        or record.get("charge")
                    )
                records.append(record)

        total_rows = len(records)
        if skip_existing_urls:
            # One set load beats N SELECT 1 queries (critical for large re-imports)
            existing_urls = self.existing_source_urls()
            kept = []
            skipped = 0
            for rec in records:
                url = (rec.get("source_url") or rec.get("external_id") or "").strip()
                if url and url in existing_urls:
                    skipped += 1
                    continue
                if url:
                    existing_urls.add(url)  # de-dupe within the same CSV batch
                kept.append(rec)
            records = kept
        else:
            skipped = 0

        imported = self.insert_offenders_batch(records) if records else 0
        return {"imported": imported, "skipped": skipped, "total_rows": total_rows}

    def import_csv_directory(
        self,
        directory: str,
        *,
        skip_existing_urls: bool = True,
        pattern: str = "*.csv",
    ) -> Dict[str, Any]:
        """Import all CSVs in a directory (e.g. data/downloads)."""
        root = Path(directory)
        if not root.is_dir():
            raise NotADirectoryError(str(root))
        files = sorted(root.glob(pattern))
        summary = {"files": 0, "imported": 0, "skipped": 0, "total_rows": 0, "errors": []}
        for f in files:
            try:
                # Infer state from ga_offenders.csv
                stem = f.stem.lower().replace("_offenders", "").replace("_data", "")
                st = stem.upper() if len(stem) == 2 and stem.isalpha() else None
                r = self.import_csv(str(f), state=st, skip_existing_urls=skip_existing_urls)
                summary["files"] += 1
                summary["imported"] += r["imported"]
                summary["skipped"] += r["skipped"]
                summary["total_rows"] += r["total_rows"]
            except Exception as e:
                summary["errors"].append(f"{f.name}: {e}")
        return summary

    def export_to_csv(
        self,
        output_path: str,
        filters: Optional[Dict[str, Any]] = None
    ) -> int:
        """Export records to CSV. Returns count exported."""
        import csv as csv_module

        query = "SELECT * FROM offenders"
        params: List[Any] = []

        if filters:
            conditions = []
            if filters.get("state") and str(filters["state"]).upper() != "ALL":
                conditions.append("UPPER(state) = UPPER(?)")
                params.append(filters["state"])
            if filters.get("race"):
                conditions.append("UPPER(race) = UPPER(?)")
                params.append(filters["race"])
            if filters.get("name"):
                conditions.append(
                    "(full_name LIKE ? ESCAPE '\\' OR first_name LIKE ? ESCAPE '\\' "
                    "OR last_name LIKE ? ESCAPE '\\')"
                )
                term = f"%{_escape_like(str(filters['name']))}%"
                params.extend([term, term, term])
            if conditions:
                query += " WHERE " + " AND ".join(conditions)

        rows = self._conn.execute(query, params).fetchall()
        if not rows:
            # Write header-only file from known columns so callers don't crash
            fieldnames = ["id", *_OFFENDER_INSERT_COLUMNS, "scraped_at"]
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv_module.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
            return 0

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv_module.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))

        return len(rows)

    def _normalize_record(self, record: Dict[str, Any]) -> None:
        """Normalize common column name variations."""
        name_map = {
            "Name": "full_name",
            "Offender Name": "full_name",
            "First Name": "first_name",
            "FirstName": "first_name",
            "Last Name": "last_name",
            "LastName": "last_name",
            "Race": "race",
            "Ethnicity": "ethnicity",
            "Gender": "gender",
            "Age": "age",
            "DOB": "date_of_birth",
            "Date of Birth": "date_of_birth",
            "Height": "height",
            "Weight": "weight",
            "Eye Color": "eye_color",
            "Hair Color": "hair_color",
            "State": "state",
            "County": "county",
            "City": "city",
            "Address": "address",
            "Zip Code": "zip_code",
            "Zip": "zip_code",
            "ZIP": "zip_code",
            "Risk Level": "risk_level",
            "Crime": "crime",
            "Offense": "crime",
            "Offense Type": "offense_type",
            "Offense Description": "offense_description",
            "Charge": "crime",
            "Charges": "crime",
            "Source URL": "source_url",
            "URL": "source_url",
            "Photo": "photo_url",
            "Image": "photo_url",
        }

        new_record: Dict[str, Any] = {}
        for key, value in record.items():
            if key is None:
                continue
            key_str = str(key).strip()
            if not key_str:
                continue
            normalized_key = name_map.get(key_str, key_str.lower().replace(" ", "_"))
            if value is None or (isinstance(value, str) and not value.strip()):
                new_record[normalized_key] = None
            else:
                new_record[normalized_key] = str(value).strip()

        # Coerce age to int when possible
        if new_record.get("age") is not None:
            try:
                new_record["age"] = int(float(str(new_record["age"]).strip()))
            except (TypeError, ValueError):
                pass

        # Derive name parts from full_name when missing
        if not new_record.get("last_name") and new_record.get("full_name"):
            parts = str(new_record["full_name"]).split()
            if len(parts) >= 2:
                new_record.setdefault("first_name", parts[0])
                new_record.setdefault("last_name", parts[-1])
            elif parts:
                new_record.setdefault("last_name", parts[0])

        record.clear()
        record.update(new_record)


def backup_database_file(
    db_path: str | Path,
    backup_dir: str | Path,
    *,
    keep: int = 10,
    prefix: str = "offenders",
    open_db: Optional["Database"] = None,
    verify: bool = True,
) -> Tuple[Path, Optional[str]]:
    """
    Copy/backup the SQLite DB into backup_dir with a timestamped name.

    Prefer SQLite online backup (consistent snapshot). Optionally verify with
    PRAGMA integrity_check. Atomic write via .tmp then rename.

    Returns (backup_path, pruned_note). Prunes older backups when keep > 0.
    """
    src = Path(db_path)
    if not src.exists() and open_db is None:
        raise FileNotFoundError(f"Database not found: {src}")

    bdir = Path(backup_dir)
    bdir.mkdir(parents=True, exist_ok=True)
    # Microseconds avoid same-second collisions when backing up in a loop
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = bdir / f"{prefix}_{stamp}.db"
    n = 0
    while dest.exists():
        n += 1
        dest = bdir / f"{prefix}_{stamp}_{n}.db"

    owned_db: Optional[Database] = None
    try:
        if open_db is not None and str(open_db.db_path) != ":memory:":
            try:
                open_db.checkpoint()
            except Exception:
                pass
            open_db.backup_to(dest, verify=verify)
        elif src.exists():
            # Short-lived connection so concurrent GUI readers stay consistent
            owned_db = Database(str(src))
            try:
                owned_db.checkpoint()
            except Exception:
                pass
            owned_db.backup_to(dest, verify=verify)
        else:
            raise FileNotFoundError(f"Database not found: {src}")
    finally:
        if owned_db is not None:
            try:
                owned_db.close()
            except Exception:
                pass

    # Post-verify on final path (paranoia: catch rename/filesystem issues)
    if verify and dest.exists():
        try:
            vconn = sqlite3.connect(str(dest))
            try:
                row = vconn.execute("PRAGMA integrity_check").fetchone()
                if not row or str(row[0]).lower() != "ok":
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                    raise RuntimeError(
                        f"Backup verification failed: {row[0] if row else 'unknown'}"
                    )
            finally:
                vconn.close()
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Could not verify backup {dest}: {e}") from e

    pruned_note = None
    if keep and keep > 0:
        pruned = _prune_backups(bdir, prefix=prefix, keep=keep)
        if pruned:
            pruned_note = f"pruned {pruned} old backup(s)"
    return dest, pruned_note


def _prune_backups(backup_dir: Path, *, prefix: str, keep: int) -> int:
    """Keep the newest *keep* timestamped backups; delete older ones."""
    if keep <= 0:
        return 0
    # Match prefix_YYYYMMDD_HHMMSS.db, with µs, or collision suffix
    pat = re.compile(
        rf"^{re.escape(prefix)}_\d{{8}}_\d{{6}}(?:_\d+)?(?:_\d+)?\.db$", re.I
    )
    files = sorted(
        [p for p in backup_dir.iterdir() if p.is_file() and pat.match(p.name)],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in files[keep:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed

# Convenience function to get a database instance
def get_database(db_path: Optional[str] = None) -> Database:
    """Get a database connection, creating it if needed."""
    return Database(db_path or DEFAULT_DB_PATH)