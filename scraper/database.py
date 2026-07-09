"""
Database layer for storing and querying sex offender records.

Uses SQLite with indexes on name and race columns for fast searching.
Supports both direct CSV import and record-by-record insertion.
"""

import json
import re
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


# Schema version - increment when schema changes
SCHEMA_VERSION = 5

# Strategies for find/remove_duplicates
DUPLICATE_STRATEGIES = (
    "source_url",       # same report URL (strongest)
    "external_id",      # same external_id within state
    "name_state_dob",   # same first+last+state+DOB
    "name_dob",         # same first+last+DOB across states (multi-state registration)
    "name_state_soft",  # same first+last+state when photo_url or address also matches
    "name_state",       # same first+last+state (weaker — different people can share names)
)

# Default Integrity / CLI "all" order (strongest → multi-state name+DOB → soft name+state)
DEFAULT_DEDUPE_STRATEGIES = (
    "source_url",
    "external_id",
    "name_state_dob",
    "name_dob",
    "name_state_soft",
)

# Query params that are session/cache noise (same person → different URL)
_VOLATILE_URL_PARAMS = frozenset({
    "uid", "session", "sessionid", "sid", "token", "auth", "access_token",
    "t", "ts", "timestamp", "_", "cache", "cb", "nonce", "requestid",
    "request_id", "correlationid", "correlation_id",
})

# When merging duplicates, union distinct values with this separator
_MERGE_SEP = " | "

# Fields where distinct non-empty values from all rows are unioned (not just fill blanks)
_MERGE_UNION_FIELDS = frozenset({
    "state",
    "source_state",
    "county",
    "city",
    "address",
    "zip_code",
    "crime",
    "offense_type",
    "offense_description",
    "source_url",
    "external_id",
    "risk_level",
    "photo_url",
    "conviction_date",
    "registration_date",
})

# Default database path (relative to project root)
DEFAULT_DB_PATH = "data/offenders.db"

# Columns written by insert helpers (must match INSERT placeholders 1:1)
_OFFENDER_INSERT_COLUMNS = (
    "first_name", "middle_name", "last_name", "full_name", "race", "ethnicity", "gender",
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
        # Wait for other writers (GUI + repair scripts) instead of failing immediately
        self._conn.execute("PRAGMA busy_timeout=60000")
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
        if from_version < 5:
            # middle_name column via _ensure_offender_columns
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

    # ---- Insert operations ----

    def normalize_record_identity(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Write-time identity cleanup: strip session tokens from URLs and prefer
        stable registry Ids in external_id (prevents NSOPW uid= duplicates).
        Also fixes NSOPW junk state codes like ``YY`` using source_state.
        """
        out = dict(record or {})
        url = str(out.get("source_url") or "").strip()
        ext = str(out.get("external_id") or "").strip()
        if url:
            norm = self.normalize_identity_url(url)
            if norm:
                # Prefer original scheme/host casing from norm (already lowercased)
                out["source_url"] = norm
        # Stable external id from URL Id= when possible
        key = self.stable_external_key(out)
        if key and "|reg:" in key:
            out["external_id"] = key.split("|reg:", 1)[1]
        elif ext:
            norm_ext = self.normalize_identity_url(ext)
            if norm_ext:
                out["external_id"] = norm_ext
        elif url:
            norm = self.normalize_identity_url(url)
            if norm:
                out["external_id"] = norm
        # Drop junk NSOPW location.state codes (YY seen on many FL rows)
        try:
            from .nsopw_client import normalize_jurisdiction_code

            fixed = normalize_jurisdiction_code(out.get("state"), out.get("source_state"))
            if fixed:
                out["state"] = fixed
                if not out.get("source_state") or str(out.get("source_state")).upper() in (
                    "YY", "XX", "ZZ", "US",
                ):
                    out["source_state"] = fixed
            elif str(out.get("state") or "").strip().upper() in ("YY", "XX", "ZZ"):
                # Prefer URL host inference for FDLE etc.
                u = str(out.get("source_url") or "").lower()
                if "fdle.state.fl" in u or "florida" in u:
                    out["state"] = "FL"
                    out["source_state"] = out.get("source_state") or "FL"
                else:
                    out["state"] = None
        except Exception:
            st = str(out.get("state") or "").strip().upper()
            if st in ("YY", "XX", "ZZ"):
                src = str(out.get("source_state") or "").strip().upper()
                if src and src not in ("YY", "XX", "ZZ", "US"):
                    out["state"] = src
        return out

    def repair_bogus_states(self) -> int:
        """
        Fix existing rows where state is YY/XX/ZZ but source_state (or URL) is real.

        Returns number of rows updated.
        """
        try:
            from .nsopw_client import normalize_jurisdiction_code
        except Exception:
            normalize_jurisdiction_code = None  # type: ignore

        rows = self._conn.execute(
            """
            SELECT id, state, source_state, source_url FROM offenders
            WHERE UPPER(TRIM(COALESCE(state, ''))) IN ('YY', 'XX', 'ZZ', 'NA', 'UN')
               OR (
                 (state IS NULL OR TRIM(state) = '')
                 AND source_state IS NOT NULL AND TRIM(source_state) != ''
               )
            """
        ).fetchall()
        n = 0
        for row in rows:
            d = dict(row)
            fixed = None
            if normalize_jurisdiction_code:
                fixed = normalize_jurisdiction_code(d.get("state"), d.get("source_state"))
            if not fixed:
                src = str(d.get("source_state") or "").strip().upper()
                if src and src not in ("YY", "XX", "ZZ", "US", "NA"):
                    fixed = src
            if not fixed:
                u = str(d.get("source_url") or "").lower()
                if "fdle.state.fl" in u:
                    fixed = "FL"
            if not fixed:
                continue
            self._conn.execute(
                "UPDATE offenders SET state = ?, source_state = COALESCE(NULLIF(TRIM(source_state), ''), ?) "
                "WHERE id = ?",
                (fixed, fixed, int(d["id"])),
            )
            n += 1
        if n:
            self._conn.commit()
        return n

    def insert_offender(self, record: Dict[str, Any]) -> int:
        """Insert a single offender record. Returns the row id."""
        record = self.normalize_record_identity(record)
        cursor = self._conn.cursor()
        cursor.execute(_OFFENDER_INSERT_SQL, _record_to_insert_tuple(record))
        self._conn.commit()
        return cursor.lastrowid

    def insert_offenders_batch(self, records: List[Dict[str, Any]]) -> int:
        """Insert multiple offender records. Returns count inserted."""
        if not records:
            return 0
        cleaned = [self.normalize_record_identity(r) for r in records]
        cursor = self._conn.cursor()
        cursor.executemany(
            _OFFENDER_INSERT_SQL,
            [_record_to_insert_tuple(r) for r in cleaned],
        )
        self._conn.commit()
        return cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else len(cleaned)

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
            query = self._append_state_filter(query, params, state)

        if race:
            query += " AND UPPER(COALESCE(race, '')) = UPPER(?)"
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
        """Search offenders by race (case-insensitive).

        Special token ``INDIAN`` matches race/ethnicity/likely_ethnicity fields
        that look South Asian / Indian (not only exact race = INDIAN).
        """
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        race_key = (race or "").strip().upper()

        if race_key in ("INDIAN", "ASIAN INDIAN", "SOUTH ASIAN", "ASIAN/INDIAN"):
            # Broad match: registry race codes + stored name-ethnicity tags
            query = """
                SELECT * FROM offenders WHERE (
                    UPPER(COALESCE(race, '')) LIKE '%INDIAN%'
                    OR UPPER(COALESCE(race, '')) LIKE '%SOUTH ASIAN%'
                    OR UPPER(COALESCE(ethnicity, '')) LIKE '%INDIAN%'
                    OR UPPER(COALESCE(ethnicity, '')) LIKE '%SOUTH ASIAN%'
                    OR UPPER(COALESCE(likely_ethnicity, '')) LIKE '%INDIAN%'
                )
            """
            params: List[Any] = []
        else:
            query = "SELECT * FROM offenders WHERE UPPER(COALESCE(race, '')) = UPPER(?)"
            params = [race]

        if state and state.upper() != "ALL":
            query = self._append_state_filter(query, params, state)

        query += " ORDER BY last_name ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def search_by_surname_list(
        self,
        surnames: List[str],
        state: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Return offenders whose last_name (or full_name tail) is in *surnames*."""
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        cleaned = sorted({
            (s or "").strip() for s in (surnames or []) if (s or "").strip()
        }, key=str.lower)
        if not cleaned:
            return []
        # Case-insensitive IN via lower() = ?
        placeholders = ",".join("?" for _ in cleaned)
        lowers = [s.lower() for s in cleaned]
        query = f"""
            SELECT * FROM offenders WHERE (
                LOWER(COALESCE(last_name, '')) IN ({placeholders})
                OR LOWER(TRIM(COALESCE(full_name, ''))) IN ({placeholders})
            )
        """
        params: List[Any] = list(lowers) + list(lowers)
        if state and state.upper() != "ALL":
            query = self._append_state_filter(query, params, state)
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
        """Search offenders by state. Use state='ALL' (or empty) to return any state.

        Matches either ``state`` or ``source_state`` so imports/scrapes that only
        set one column still appear in filters. Also matches multi-state merged
        values (e.g. ``FL | TX`` when filtering for FL).
        """
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        if not state or state.upper() == "ALL":
            query = "SELECT * FROM offenders ORDER BY last_name ASC LIMIT ? OFFSET ?"
            rows = self._conn.execute(query, (limit, offset)).fetchall()
        else:
            params: List[Any] = []
            query = "SELECT * FROM offenders WHERE 1=1"
            query = self._append_state_filter(query, params, state)
            query += " ORDER BY last_name ASC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = self._conn.execute(query, params).fetchall()
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
        sql = f"SELECT * FROM offenders WHERE {' AND '.join(clauses)}"
        if state and state.upper() != "ALL":
            sql = self._append_state_filter(sql, params, state)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ---- Duplicate detection / removal ----

    @staticmethod
    def normalize_identity_url(url: Optional[str]) -> str:
        """
        Canonical URL for dedupe.

        Strips session/uid/token query params so the same offender page with
        different NSOPW ``uid`` values groups together. Keeps stable ids
        (``Id``, ``ImageId``, path segments).
        """
        raw = (url or "").strip()
        if not raw:
            return ""
        try:
            p = urlparse(raw)
        except Exception:
            return raw.rstrip("/").lower()
        # Relative paths / non-http: still normalize query if present
        kept = [
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if k and k.lower() not in _VOLATILE_URL_PARAMS
        ]
        kept.sort(key=lambda kv: (kv[0].lower(), kv[1]))
        host = (p.netloc or "").lower()
        path = (p.path or "").rstrip("/") or "/"
        scheme = (p.scheme or "https").lower()
        if not host and not p.query and not p.path:
            return raw.rstrip("/").lower()
        return urlunparse((scheme, host, path, "", urlencode(kept), "")).lower()

    @classmethod
    def stable_external_key(
        cls,
        record: Dict[str, Any],
        *,
        state_hint: Optional[str] = None,
    ) -> str:
        """
        Stable person/listing key for external_id strategy.

        Prefers explicit registry Id query params (e.g. GA ``Id=50604``), then
        normalized URL, then raw external_id text.
        """
        ext = str(record.get("external_id") or "").strip()
        url = str(record.get("source_url") or "").strip()
        state = (
            state_hint
            or record.get("state")
            or record.get("source_state")
            or ""
        )
        state_u = str(state).strip().upper()

        def _id_from(s: str) -> str:
            if not s:
                return ""
            try:
                qs = dict(parse_qsl(urlparse(s).query, keep_blank_values=True))
            except Exception:
                return ""
            for key in ("Id", "ID", "id", "OffenderId", "offenderId", "offender_id"):
                if key in qs and str(qs[key]).strip():
                    return str(qs[key]).strip()
            # path tail numeric id: /offenders/12345
            try:
                path = urlparse(s).path or ""
            except Exception:
                path = ""
            m = re.search(r"/(\d{3,})/?$", path)
            if m:
                return m.group(1)
            return ""

        for candidate in (ext, url):
            oid = _id_from(candidate)
            if oid:
                return f"{state_u}|reg:{oid}".lower()

        norm = cls.normalize_identity_url(ext or url)
        if norm:
            return f"{state_u}|url:{norm}".lower()
        if ext:
            return f"{state_u}|raw:{ext.casefold()}"
        return ""

    # Shared CAPTCHA / search / portal URLs must not collapse many people into one.
    _GENERIC_URL_MARKERS = (
        "captcha",
        "login",
        "signin",
        "sign-in",
        "challenge",
        "cloudflare",
        "just a moment",
        "cf-browser",
        "accessdenied",
        "access-denied",
        "botdetect",
        "search-public",
        "publicregistrantsearch",
        "sor_public",
        "sort_public",
        "coveredoffender",  # Hawaii landing (often non-unique)
    )

    @classmethod
    def _url_has_stable_offender_id(cls, url: str) -> bool:
        """True if URL carries a person-specific Id (not a bare portal landing)."""
        raw = (url or "").strip()
        if not raw:
            return False
        try:
            p = urlparse(raw)
            qs = {k.lower(): v for k, v in parse_qsl(p.query, keep_blank_values=True)}
        except Exception:
            return False
        for key in (
            "id", "offenderid", "offender_id", "offenderid", "personid",
            "registrantid", "subjectid",
        ):
            val = (qs.get(key) or "").strip()
            if val and val.lower() not in ("0", "null", "none", "undefined"):
                return True
        # path …/offenders/12345
        path = (p.path or "").strip("/")
        if re.search(r"(?:^|/)(\d{3,})(?:/|$)", path):
            return True
        return False

    @classmethod
    def is_generic_source_url(cls, url: str, *, group_count: int = 1) -> bool:
        """
        True when *url* is likely a shared portal/CAPTCHA page, not a unique
        offender report. High fan-out groups are treated as generic too.

        Portal path markers (e.g. ``sort_public``) alone do **not** mark a URL
        generic when it includes a stable offender ``Id=`` query — those are
        real person pages that may only differ by session ``uid``.
        """
        u = (url or "").strip().lower()
        if not u:
            return True
        # Person-specific Id wins over portal path markers
        if cls._url_has_stable_offender_id(url):
            # Still unsafe if absurd fan-out (shared Id mis-scrape)
            if group_count >= 25:
                return True
            return False
        compact = re.sub(r"[\s_\-]+", "", u)
        for m in cls._GENERIC_URL_MARKERS:
            if m.replace("-", "").replace("_", "").replace(" ", "") in compact:
                return True
            if m in u:
                return True
        # Bare search home pages (no query / id segment)
        if group_count >= 8:
            return True
        # Extremely short path after host → landing page
        try:
            path = (urlparse(u).path or "").strip("/")
            if path.count("/") == 0 and len(path) < 12 and group_count > 2:
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _row_richness(row: Dict[str, Any]) -> int:
        """How complete a record is — higher is better when choosing a survivor."""
        score = 0
        for col, weight in (
            ("race", 3),
            ("crime", 2),
            ("offense_description", 2),
            ("offense_type", 1),
            ("photo_path", 3),
            ("report_html_path", 2),
            ("source_url", 2),
            ("photo_url", 1),
            ("ethnicity", 1),
            ("date_of_birth", 1),
            ("address", 1),
            ("county", 1),
            ("city", 1),
            ("gender", 1),
            ("risk_level", 1),
            ("state", 1),
        ):
            val = row.get(col)
            if val is not None and str(val).strip():
                score += weight
                # Slight boost for already-merged multi-value fields
                if _MERGE_SEP in str(val):
                    score += 1
        return score

    @staticmethod
    def _normalize_dup_key_part(value: Any) -> str:
        return " ".join(str(value or "").strip().lower().split())

    @staticmethod
    def _split_merged_values(value: Any) -> List[str]:
        """Split a field that may already contain ' | ' unions into distinct parts."""
        raw = str(value or "").strip()
        if not raw:
            return []
        parts: List[str] = []
        seen: set = set()
        for chunk in raw.split(_MERGE_SEP):
            # Also accept semicolon / newline lists from older scrapes
            for piece in re.split(r"[;\n]+", chunk):
                p = " ".join(piece.strip().split())
                if not p:
                    continue
                key = p.casefold()
                if key in seen:
                    continue
                seen.add(key)
                parts.append(p)
        return parts

    @classmethod
    def _union_field_values(cls, *values: Any) -> str:
        """Union distinct non-empty values, preserving first-seen order."""
        parts: List[str] = []
        seen: set = set()
        for v in values:
            for p in cls._split_merged_values(v):
                key = p.casefold()
                if key in seen:
                    continue
                seen.add(key)
                parts.append(p)
        return _MERGE_SEP.join(parts)

    @classmethod
    def merge_duplicate_members(
        cls,
        keep: Dict[str, Any],
        losers: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Build field updates that merge *losers* into *keep*.

        - Union multi-listing fields (states, crimes, addresses, URLs, …)
        - Fill blanks on identity/physical fields from any loser
        - Annotate flags with merged source row ids when useful

        Returns only columns that should change on the keeper.
        """
        if not losers:
            return {}

        updates: Dict[str, Any] = {}
        all_rows = [keep] + list(losers)

        # 1) Union multi-value / multi-listing fields
        for col in _MERGE_UNION_FIELDS:
            merged = cls._union_field_values(*(r.get(col) for r in all_rows))
            cur = str(keep.get(col) or "").strip()
            if merged and merged != cur:
                updates[col] = merged

        # 2) Fill blanks (prefer non-empty) for remaining insert columns
        for col in _OFFENDER_INSERT_COLUMNS:
            if col in _MERGE_UNION_FIELDS:
                continue
            if col == "flags":
                continue  # handled below
            if col == "raw_data_json":
                # Prefer non-empty JSON; do not concatenate
                cur = keep.get(col)
                if cur is not None and str(cur).strip():
                    continue
                for r in losers:
                    alt = r.get(col)
                    if alt is not None and str(alt).strip():
                        updates[col] = alt
                        break
                continue
            if col in ("photo_path", "report_html_path"):
                # Keep existing file path; only fill if blank
                cur = keep.get(col)
                if cur is not None and str(cur).strip():
                    continue
                for r in losers:
                    alt = r.get(col)
                    if alt is not None and str(alt).strip():
                        updates[col] = alt
                        break
                continue
            # Scalar: fill blank only
            cur = keep.get(col)
            if cur is not None and str(cur).strip():
                continue
            for r in losers:
                alt = r.get(col)
                if alt is not None and str(alt).strip():
                    updates[col] = alt
                    break

        # 3) flags: merge JSON lists/dicts + record merged ids
        flag_objs: List[Any] = []
        for r in all_rows:
            raw = r.get("flags")
            if raw is None or str(raw).strip() == "":
                continue
            if isinstance(raw, (list, dict)):
                flag_objs.append(raw)
                continue
            try:
                flag_objs.append(json.loads(str(raw)))
            except Exception:
                flag_objs.append(str(raw).strip())

        merged_ids = []
        for r in losers:
            try:
                merged_ids.append(int(r["id"]))
            except (KeyError, TypeError, ValueError):
                pass

        flag_out: Any = None
        if flag_objs:
            # Prefer a dict payload so we can attach metadata
            base: Dict[str, Any] = {}
            list_flags: List[str] = []
            for fo in flag_objs:
                if isinstance(fo, dict):
                    for k, v in fo.items():
                        if k in ("merged_from_ids", "merged_listings"):
                            continue
                        if k not in base:
                            base[k] = v
                        elif isinstance(base[k], list) and isinstance(v, list):
                            for item in v:
                                if item not in base[k]:
                                    base[k].append(item)
                elif isinstance(fo, list):
                    for item in fo:
                        s = str(item)
                        if s not in list_flags:
                            list_flags.append(s)
                else:
                    s = str(fo)
                    if s not in list_flags:
                        list_flags.append(s)
            if list_flags:
                base.setdefault("tags", list_flags)
            flag_out = base
        else:
            flag_out = {}

        if merged_ids:
            prev = flag_out.get("merged_from_ids") if isinstance(flag_out, dict) else None
            ids: List[int] = []
            if isinstance(prev, list):
                for x in prev:
                    try:
                        ids.append(int(x))
                    except (TypeError, ValueError):
                        pass
            for i in merged_ids:
                if i not in ids:
                    ids.append(i)
            flag_out["merged_from_ids"] = ids
            # Compact multi-state / multi-listing summary for UI
            states = cls._split_merged_values(
                updates.get("state", keep.get("state"))
            )
            crimes = cls._split_merged_values(
                updates.get("crime", keep.get("crime"))
            )
            urls = cls._split_merged_values(
                updates.get("source_url", keep.get("source_url"))
            )
            flag_out["merged_listings"] = {
                "states": states,
                "crimes": crimes[:20],
                "source_urls": urls[:20],
                "count": 1 + len(merged_ids),
            }

        if flag_out:
            try:
                new_flags = json.dumps(flag_out, ensure_ascii=False, sort_keys=True)
            except Exception:
                new_flags = str(flag_out)
            cur_flags = str(keep.get("flags") or "").strip()
            if new_flags != cur_flags:
                updates["flags"] = new_flags

        return updates

    @staticmethod
    def _state_match_sql(column_expr: str = "state") -> str:
        """
        SQL fragment: column matches a state code even when multi-state
        merged values use ' | ' separators (e.g. 'FL | TX').
        """
        # Normalize spaces around | then test token membership
        return (
            f"("
            f"UPPER(TRIM(COALESCE({column_expr}, ''))) = UPPER(?) "
            f"OR ('|' || REPLACE(REPLACE(UPPER(COALESCE({column_expr}, '')), ' ', ''), "
            f"'{_MERGE_SEP.strip()}', '|') || '|') "
            f"LIKE '%|' || UPPER(?) || '|%'"
            f")"
        )

    def _append_state_filter(self, query: str, params: List[Any], state: str) -> str:
        """Append OR of state / source_state match (supports merged multi-state)."""
        st = (state or "").strip()
        if not st or st.upper() == "ALL":
            return query
        frag_state = self._state_match_sql("state")
        frag_src = self._state_match_sql("source_state")
        query += f" AND ({frag_state} OR {frag_src})"
        # each fragment uses ? twice
        params.extend([st, st, st, st])
        return query

    def _duplicate_group_sql(self, strategy: str) -> Tuple[str, str]:
        """
        Return (select_sql, key_label) for a strategy.

        select_sql must yield rows with columns: dup_key, cnt, id_list
        (id_list = comma-separated ids).
        """
        s = (strategy or "source_url").strip().lower()
        if s == "source_url":
            sql = """
                SELECT TRIM(source_url) AS dup_key,
                       COUNT(*) AS cnt,
                       GROUP_CONCAT(id) AS id_list
                FROM offenders
                WHERE source_url IS NOT NULL AND TRIM(source_url) != ''
                GROUP BY TRIM(source_url)
                HAVING COUNT(*) > 1
                ORDER BY cnt DESC
            """
            return sql, "source_url"
        if s == "external_id":
            sql = """
                SELECT LOWER(TRIM(external_id)) || '|' ||
                       UPPER(COALESCE(NULLIF(TRIM(state), ''),
                                      NULLIF(TRIM(source_state), ''), '')) AS dup_key,
                       COUNT(*) AS cnt,
                       GROUP_CONCAT(id) AS id_list
                FROM offenders
                WHERE external_id IS NOT NULL AND TRIM(external_id) != ''
                GROUP BY LOWER(TRIM(external_id)),
                         UPPER(COALESCE(NULLIF(TRIM(state), ''),
                                        NULLIF(TRIM(source_state), ''), ''))
                HAVING COUNT(*) > 1
                ORDER BY cnt DESC
            """
            return sql, "external_id+state"
        if s == "name_state_dob":
            sql = """
                SELECT LOWER(TRIM(COALESCE(first_name, ''))) || '|' ||
                       LOWER(TRIM(COALESCE(last_name, ''))) || '|' ||
                       UPPER(COALESCE(NULLIF(TRIM(state), ''),
                                      NULLIF(TRIM(source_state), ''), '')) || '|' ||
                       LOWER(TRIM(date_of_birth)) AS dup_key,
                       COUNT(*) AS cnt,
                       GROUP_CONCAT(id) AS id_list
                FROM offenders
                WHERE last_name IS NOT NULL AND TRIM(last_name) != ''
                  AND date_of_birth IS NOT NULL AND TRIM(date_of_birth) != ''
                GROUP BY LOWER(TRIM(COALESCE(first_name, ''))),
                         LOWER(TRIM(COALESCE(last_name, ''))),
                         UPPER(COALESCE(NULLIF(TRIM(state), ''),
                                        NULLIF(TRIM(source_state), ''), '')),
                         LOWER(TRIM(date_of_birth))
                HAVING COUNT(*) > 1
                ORDER BY cnt DESC
            """
            return sql, "name+state+dob"
        if s == "name_dob":
            # Cross-state: same person registered in multiple states
            sql = """
                SELECT LOWER(TRIM(COALESCE(first_name, ''))) || '|' ||
                       LOWER(TRIM(COALESCE(last_name, ''))) || '|' ||
                       LOWER(TRIM(date_of_birth)) AS dup_key,
                       COUNT(*) AS cnt,
                       GROUP_CONCAT(id) AS id_list
                FROM offenders
                WHERE first_name IS NOT NULL AND TRIM(first_name) != ''
                  AND last_name IS NOT NULL AND TRIM(last_name) != ''
                  AND date_of_birth IS NOT NULL AND TRIM(date_of_birth) != ''
                GROUP BY LOWER(TRIM(COALESCE(first_name, ''))),
                         LOWER(TRIM(COALESCE(last_name, ''))),
                         LOWER(TRIM(date_of_birth))
                HAVING COUNT(*) > 1
                ORDER BY cnt DESC
            """
            return sql, "name+dob (multi-state)"
        if s in ("name_state", "name_state_soft"):
            sql = """
                SELECT LOWER(TRIM(COALESCE(first_name, ''))) || '|' ||
                       LOWER(TRIM(COALESCE(last_name, ''))) || '|' ||
                       UPPER(COALESCE(NULLIF(TRIM(state), ''),
                                      NULLIF(TRIM(source_state), ''), '')) AS dup_key,
                       COUNT(*) AS cnt,
                       GROUP_CONCAT(id) AS id_list
                FROM offenders
                WHERE last_name IS NOT NULL AND TRIM(last_name) != ''
                  AND (
                    (state IS NOT NULL AND TRIM(state) != '')
                    OR (source_state IS NOT NULL AND TRIM(source_state) != '')
                  )
                GROUP BY LOWER(TRIM(COALESCE(first_name, ''))),
                         LOWER(TRIM(COALESCE(last_name, ''))),
                         UPPER(COALESCE(NULLIF(TRIM(state), ''),
                                        NULLIF(TRIM(source_state), ''), ''))
                HAVING COUNT(*) > 1
                ORDER BY cnt DESC
            """
            label = (
                "name+state (photo/address corroborated)"
                if s == "name_state_soft"
                else "name+state"
            )
            return sql, label
        raise ValueError(
            f"Unknown duplicate strategy {strategy!r}; "
            f"choose one of {', '.join(DUPLICATE_STRATEGIES)}"
        )

    def _groups_from_member_map(
        self,
        strategy: str,
        key_label: str,
        buckets: Dict[str, List[Dict[str, Any]]],
        *,
        limit_groups: Optional[int] = None,
        include_unsafe: bool = True,
    ) -> List[Dict[str, Any]]:
        """Build sorted duplicate group dicts from pre-bucketed member rows."""
        groups: List[Dict[str, Any]] = []
        # Largest groups first (same as SQL ORDER BY cnt DESC)
        items = sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        for key, members in items:
            if len(members) < 2 or not key:
                continue
            members = list(members)
            members.sort(
                key=lambda r: (-self._row_richness(r), int(r.get("id") or 0))
            )
            keep = members[0]
            remove_ids = [int(m["id"]) for m in members[1:] if m.get("id") is not None]
            if not remove_ids:
                continue
            keep_name = (
                f"{keep.get('first_name') or ''} {keep.get('last_name') or ''}"
            ).strip() or (keep.get("full_name") or "—")
            safe = True
            if (strategy or "").lower() == "source_url":
                # Use a sample raw URL for portal/CAPTCHA detection
                sample_url = str(
                    keep.get("source_url") or members[0].get("source_url") or key
                )
                safe = not self.is_generic_source_url(
                    sample_url, group_count=len(members)
                )
            if not include_unsafe and not safe:
                continue
            groups.append({
                "strategy": strategy,
                "key_label": key_label,
                "key": key,
                "count": len(members),
                "ids": [int(m["id"]) for m in members if m.get("id") is not None],
                "keep_id": int(keep["id"]),
                "remove_ids": remove_ids,
                "keep_preview": keep_name,
                "richness": self._row_richness(keep),
                "safe": safe,
                "members": members,
            })
            if limit_groups is not None and len(groups) >= int(limit_groups):
                break
        return groups

    def _find_duplicate_groups_normalized_url(
        self,
        strategy: str,
        *,
        limit_groups: Optional[int] = None,
        include_unsafe: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Group by normalized source_url / stable external key in Python.

        Required because NSOPW and some state portals append session ``uid``
        tokens that make raw URL strings unique for the same person.
        """
        s = (strategy or "").strip().lower()
        buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        if s == "source_url":
            rows = self._conn.execute(
                "SELECT * FROM offenders "
                "WHERE source_url IS NOT NULL AND TRIM(source_url) != ''"
            ).fetchall()
            for row in rows:
                rec = dict(row)
                key = self.normalize_identity_url(rec.get("source_url"))
                if key:
                    buckets[key].append(rec)
            return self._groups_from_member_map(
                "source_url",
                "source_url (normalized)",
                buckets,
                limit_groups=limit_groups,
                include_unsafe=include_unsafe,
            )
        if s == "external_id":
            rows = self._conn.execute(
                "SELECT * FROM offenders WHERE "
                "(external_id IS NOT NULL AND TRIM(external_id) != '') "
                "OR (source_url IS NOT NULL AND TRIM(source_url) != '')"
            ).fetchall()
            for row in rows:
                rec = dict(row)
                key = self.stable_external_key(rec)
                if key:
                    buckets[key].append(rec)
            return self._groups_from_member_map(
                "external_id",
                "external_id (stable)",
                buckets,
                limit_groups=limit_groups,
                include_unsafe=include_unsafe,
            )
        raise ValueError(f"Normalized grouping not defined for {strategy!r}")

    def find_duplicate_groups(
        self,
        strategy: str = "source_url",
        *,
        limit_groups: Optional[int] = None,
        include_unsafe: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Find groups of duplicate offender rows for *strategy*.

        Each group: {
          strategy, key, count, ids, keep_id, remove_ids, keep_preview,
          richness, safe (False for shared CAPTCHA/portal URL clusters)
        }

        ``source_url`` / ``external_id`` use normalized identity keys so
        session tokens (e.g. ``uid=``) do not split the same person.
        """
        s = (strategy or "source_url").strip().lower()
        if s in ("source_url", "external_id"):
            return self._find_duplicate_groups_normalized_url(
                s, limit_groups=limit_groups, include_unsafe=include_unsafe
            )

        sql, key_label = self._duplicate_group_sql(strategy)
        rows = self._conn.execute(sql).fetchall()
        groups: List[Dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            id_list = str(d.get("id_list") or "")
            ids = [int(x) for x in id_list.split(",") if x.strip().isdigit()]
            if len(ids) < 2:
                continue
            members = []
            for rid in ids:
                rec = self.get_offender_by_id(rid)
                if rec:
                    members.append(rec)
            if len(members) < 2:
                continue
            # Soft name+state: only merge when photo_url or address corroborates
            if s == "name_state_soft":
                members = self._filter_name_state_soft_members(members)
                if len(members) < 2:
                    continue
            # Prefer richest row; break ties with lowest id (stable survivor)
            members.sort(
                key=lambda r: (-self._row_richness(r), int(r.get("id") or 0))
            )
            keep = members[0]
            remove_ids = [int(m["id"]) for m in members[1:]]
            keep_name = (
                f"{keep.get('first_name') or ''} {keep.get('last_name') or ''}"
            ).strip() or (keep.get("full_name") or "—")
            key = d.get("dup_key") or ""
            safe = True
            if not include_unsafe and not safe:
                continue
            groups.append({
                "strategy": strategy,
                "key_label": key_label,
                "key": key,
                "count": len(members),
                "ids": [int(m["id"]) for m in members],
                "keep_id": int(keep["id"]),
                "remove_ids": remove_ids,
                "keep_preview": keep_name,
                "richness": self._row_richness(keep),
                "safe": safe,
                "members": members,
            })
            if limit_groups is not None and len(groups) >= int(limit_groups):
                break
        return groups

    @classmethod
    def _corroboration_token(cls, record: Dict[str, Any]) -> str:
        """Shared address or photo identity used to soft-confirm name+state dups."""
        photo = cls.normalize_identity_url(record.get("photo_url") or "")
        if photo:
            return f"photo:{photo}"
        addr = " ".join(
            str(record.get("address") or "").strip().lower().split()
        )
        city = " ".join(str(record.get("city") or "").strip().lower().split())
        if addr and len(addr) >= 6:
            return f"addr:{addr}|{city}"
        return ""

    @classmethod
    def _filter_name_state_soft_members(
        cls, members: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Keep the largest subset that shares a photo_url or address token.

        Prevents collapsing different people who only share a common name+state.
        """
        buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for m in members:
            tok = cls._corroboration_token(m)
            if tok:
                buckets[tok].append(m)
        if not buckets:
            return []
        best = max(buckets.values(), key=len)
        return best if len(best) >= 2 else []

    def count_duplicates(
        self,
        strategies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Summary of duplicate groups/rows per strategy.

        Returns {
          total_offenders,
          by_strategy: {name: {groups, extra_rows, safe_groups, safe_extra_rows, unsafe_groups}},
          total_extra_rows, total_safe_extra_rows
        }
        """
        strats = list(strategies) if strategies else list(DEFAULT_DEDUPE_STRATEGIES)
        total = self.get_total_count()
        by: Dict[str, Dict[str, int]] = {}
        total_extra = 0
        total_safe_extra = 0
        for s in strats:
            try:
                groups = self.find_duplicate_groups(s, include_unsafe=True)
            except ValueError:
                continue
            groups_n = len(groups)
            extra = sum(max(0, g["count"] - 1) for g in groups)
            safe_groups = [g for g in groups if g.get("safe", True)]
            unsafe_groups = groups_n - len(safe_groups)
            safe_extra = sum(max(0, g["count"] - 1) for g in safe_groups)
            by[s] = {
                "groups": groups_n,
                "extra_rows": extra,
                "safe_groups": len(safe_groups),
                "safe_extra_rows": safe_extra,
                "unsafe_groups": unsafe_groups,
            }
            total_extra += extra
            total_safe_extra += safe_extra
        return {
            "total_offenders": total,
            "by_strategy": by,
            "total_extra_rows": total_extra,
            "total_safe_extra_rows": total_safe_extra,
        }

    def remove_duplicates(
        self,
        strategy: str = "source_url",
        *,
        dry_run: bool = False,
        merge_fields: bool = True,
        limit_groups: Optional[int] = None,
        safe_only: bool = True,
    ) -> Dict[str, Any]:
        """
        Remove duplicate rows for *strategy*, keeping the richest record per group.

        When *merge_fields* is True, non-empty fields from deleted rows fill blanks
        on the kept row before deletion.

        *safe_only* (default True): skip shared CAPTCHA/portal URL clusters so
        many different offenders are not collapsed into one row.

        Returns {
          strategy, dry_run, groups, kept, deleted, deleted_ids, merged_fields,
          skipped_unsafe
        }
        """
        groups = self.find_duplicate_groups(
            strategy, limit_groups=limit_groups, include_unsafe=True
        )
        deleted_ids: List[int] = []
        kept = 0
        merged_n = 0
        skipped_unsafe = 0
        acted_groups = 0

        for g in groups:
            if safe_only and not g.get("safe", True):
                skipped_unsafe += 1
                continue
            keep_id = int(g["keep_id"])
            remove_ids = list(g["remove_ids"])
            if not remove_ids:
                continue
            keep_row = self.get_offender_by_id(keep_id)
            if not keep_row:
                continue
            kept += 1
            acted_groups += 1

            if merge_fields:
                losers = []
                for rid in remove_ids:
                    loser = self.get_offender_by_id(rid)
                    if loser:
                        losers.append(loser)
                updates = self.merge_duplicate_members(keep_row, losers)
                if updates and not dry_run:
                    self.update_offender(keep_id, updates)
                    # Keep in-memory row current if later strategies re-read it
                    keep_row.update(updates)
                    merged_n += len(updates)
                elif updates and dry_run:
                    merged_n += len(updates)

            if not dry_run and remove_ids:
                placeholders = ",".join("?" for _ in remove_ids)
                self._conn.execute(
                    f"DELETE FROM offenders WHERE id IN ({placeholders})",
                    remove_ids,
                )
            deleted_ids.extend(remove_ids)

        if not dry_run and deleted_ids:
            self._conn.commit()

        return {
            "strategy": strategy,
            "dry_run": dry_run,
            "groups": acted_groups,
            "kept": kept,
            "deleted": len(deleted_ids),
            "deleted_ids": deleted_ids,
            "merged_fields": merged_n,
            "skipped_unsafe": skipped_unsafe,
        }

    def remove_duplicates_all(
        self,
        strategies: Optional[List[str]] = None,
        *,
        dry_run: bool = False,
        merge_fields: bool = True,
        safe_only: bool = True,
    ) -> Dict[str, Any]:
        """
        Run remove_duplicates for each strategy in order (strongest first).

        Default order: source_url → external_id → name_state_dob → name_dob
        (name_dob merges multi-state registrations; name_state is weaker and
        not included unless requested).
        """
        strats = list(strategies) if strategies else list(DEFAULT_DEDUPE_STRATEGIES)
        results = []
        total_deleted = 0
        total_skipped_unsafe = 0
        total_merged_fields = 0
        for s in strats:
            r = self.remove_duplicates(
                s,
                dry_run=dry_run,
                merge_fields=merge_fields,
                safe_only=safe_only,
            )
            results.append(r)
            total_deleted += int(r.get("deleted") or 0)
            total_skipped_unsafe += int(r.get("skipped_unsafe") or 0)
            total_merged_fields += int(r.get("merged_fields") or 0)
        return {
            "dry_run": dry_run,
            "strategies": results,
            "total_deleted": total_deleted,
            "total_skipped_unsafe": total_skipped_unsafe,
            "total_merged_fields": total_merged_fields,
            "total_offenders": self.get_total_count(),
        }

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

    def import_records(
        self,
        records: List[Dict[str, Any]],
        state: Optional[str] = None,
        *,
        skip_existing_urls: bool = True,
        source_hint: Optional[str] = None,
    ) -> Dict[str, int]:
        """
        Import in-memory offender dicts (e.g. scrape results) into the DB.

        Same normalization / de-dupe rules as ``import_csv``.
        Returns dict: {imported, skipped, total_rows}.
        """
        prepared: List[Dict[str, Any]] = []
        for row in records or []:
            if not isinstance(row, dict):
                continue
            record = dict(row)
            self._normalize_record(record)
            if state:
                record["state"] = state
                record.setdefault("source_state", state)
            if not record.get("source_state") and record.get("state"):
                record["source_state"] = record["state"]
            # Filename-style hint (e.g. "ga_offenders") when state not set
            if (
                not record.get("state")
                and not record.get("source_state")
                and source_hint
            ):
                stem = (
                    str(source_hint)
                    .lower()
                    .replace("_offenders", "")
                    .replace("_data", "")
                    .replace(".csv", "")
                )
                if len(stem) == 2 and stem.isalpha():
                    record["state"] = stem.upper()
                    record["source_state"] = stem.upper()
            if not record.get("crime"):
                record["crime"] = (
                    record.get("offense_description")
                    or record.get("offense_type")
                    or record.get("offense")
                    or record.get("charge")
                )
            prepared.append(record)

        total_rows = len(prepared)
        if skip_existing_urls:
            existing_urls = self.existing_source_urls()
            kept: List[Dict[str, Any]] = []
            skipped = 0
            for rec in prepared:
                url = (rec.get("source_url") or rec.get("external_id") or "").strip()
                norm = self.normalize_identity_url(url) if url else ""
                if url and (url in existing_urls or (norm and norm in existing_urls)):
                    skipped += 1
                    continue
                if url:
                    existing_urls.add(url)
                    if norm:
                        existing_urls.add(norm)
                kept.append(rec)
            prepared = kept
        else:
            skipped = 0

        imported = self.insert_offenders_batch(prepared) if prepared else 0
        return {"imported": imported, "skipped": skipped, "total_rows": total_rows}

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
            raw_rows = [dict(row) for row in reader]

        # Infer state from filename like fl_offenders.csv when not passed
        st = state
        if not st:
            stem = path.stem.lower().replace("_offenders", "").replace("_data", "")
            if len(stem) == 2 and stem.isalpha():
                st = stem.upper()

        return self.import_records(
            raw_rows,
            state=st,
            skip_existing_urls=skip_existing_urls,
            source_hint=path.stem,
        )

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
            "Middle Name": "middle_name",
            "MiddleName": "middle_name",
            "Middle": "middle_name",
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
            parts = str(new_record["full_name"]).replace(",", " ").split()
            if len(parts) >= 3:
                new_record.setdefault("first_name", parts[0])
                new_record.setdefault("middle_name", " ".join(parts[1:-1]))
                new_record.setdefault("last_name", parts[-1])
            elif len(parts) >= 2:
                new_record.setdefault("first_name", parts[0])
                new_record.setdefault("last_name", parts[-1])
            elif parts:
                new_record.setdefault("last_name", parts[0])

        # Split multi-token first_name into first + middle when middle empty
        first = str(new_record.get("first_name") or "").strip()
        mid = str(new_record.get("middle_name") or "").strip()
        if first and not mid:
            fparts = first.split()
            if len(fparts) >= 2:
                new_record["first_name"] = fparts[0]
                new_record["middle_name"] = " ".join(fparts[1:])

        # Derive full_name from first+middle+last when scrapers export split names only
        if not new_record.get("full_name"):
            parts = [
                str(p).strip()
                for p in (
                    new_record.get("first_name"),
                    new_record.get("middle_name"),
                    new_record.get("last_name"),
                )
                if p and str(p).strip()
            ]
            if parts:
                new_record["full_name"] = " ".join(parts)

        # Keep source_state in sync when only state is present
        if new_record.get("state") and not new_record.get("source_state"):
            new_record["source_state"] = new_record["state"]

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