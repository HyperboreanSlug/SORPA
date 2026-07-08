"""
Database layer for storing and querying sex offender records.

Uses SQLite with indexes on name and race columns for fast searching.
Supports both direct CSV import and record-by-record insertion.
"""

import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone


# Schema version - increment when schema changes
SCHEMA_VERSION = 2

# Default database path (relative to project root)
DEFAULT_DB_PATH = "data/offenders.db"

# Columns written by insert helpers (must match INSERT placeholders 1:1)
_OFFENDER_INSERT_COLUMNS = (
    "first_name", "last_name", "full_name", "race", "ethnicity", "gender",
    "age", "date_of_birth", "height", "weight", "eye_color", "hair_color", "build", "skin_tone",
    "state", "county", "city", "address", "zip_code", "latitude", "longitude",
    "offense_type", "offense_description", "risk_level", "conviction_date",
    "registration_date", "last_verified", "source_state", "source_url", "external_id", "raw_data_json",
    "likely_ethnicity", "name_confidence", "flags",
    "report_html_path",
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
                report_html_path TEXT
            )
        """)

        # Indexes for fast searching
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_offenders_last_name ON offenders(last_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_offenders_first_name ON offenders(first_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_offenders_race ON offenders(race)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_offenders_state ON offenders(state)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_offenders_county ON offenders(county)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_offenders_risk_level ON offenders(risk_level)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_offenders_source_state ON offenders(source_state)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_offenders_source_url ON offenders(source_url)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_offenders_report_html ON offenders(report_html_path)"
        )

        # Full-text search virtual table (optional, created on demand)
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS offenders_fts USING fts5(
                full_name, race, ethnicity, offense_type, offense_description, address, city, county, state, flags,
                content='offenders',
                content_rowid='rowid'
            )
        """)

        # Apply migrations after table exists
        current_version = 0
        for row in cursor.execute("SELECT MAX(version) FROM schema_version"):
            current_version = row[0] or 0

        if current_version < SCHEMA_VERSION:
            self._upgrade_schema(cursor, current_version)
            cursor.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _utc_now_iso())
            )

        self._conn.commit()

    def _upgrade_schema(self, cursor: sqlite3.Cursor, from_version: int):
        """Upgrade schema to current version."""
        if from_version < 1:
            pass
        if from_version < 2:
            # Add local HTML archive path for report pages
            cols = {row[1] for row in cursor.execute("PRAGMA table_info(offenders)")}
            if "report_html_path" not in cols:
                cursor.execute("ALTER TABLE offenders ADD COLUMN report_html_path TEXT")

    def close(self):
        """Close the database connection."""
        self._conn.close()

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

        search_term = f"%{name}%"
        query += " AND (full_name LIKE ? OR first_name LIKE ? OR last_name LIKE ?)"
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

    def import_csv(self, csv_path: str, state: Optional[str] = None) -> int:
        """Import records from a CSV file. Returns count imported."""
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
                # Infer source_state from filename like fl_offenders.csv
                if not record.get("source_state") and not record.get("state"):
                    stem = path.stem.lower().replace("_offenders", "").replace("_data", "")
                    if len(stem) == 2 and stem.isalpha():
                        record["state"] = stem.upper()
                        record["source_state"] = stem.upper()
                elif not record.get("source_state") and record.get("state"):
                    record["source_state"] = record["state"]
                records.append(record)

        return self.insert_offenders_batch(records)

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
                    "(full_name LIKE ? OR first_name LIKE ? OR last_name LIKE ?)"
                )
                term = f"%{filters['name']}%"
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


# Convenience function to get a database instance
def get_database(db_path: Optional[str] = None) -> Database:
    """Get a database connection, creating it if needed."""
    return Database(db_path or DEFAULT_DB_PATH)