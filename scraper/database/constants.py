"""Database constants, insert column maps, and helpers."""
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


