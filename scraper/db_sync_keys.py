"""Stable record keys + content hashes for public DB delta sync."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, Optional, Tuple

# Columns exported into delta upserts (no autoincrement id).
SYNC_ROW_COLUMNS: Tuple[str, ...] = (
    "first_name",
    "middle_name",
    "last_name",
    "full_name",
    "race",
    "ethnicity",
    "gender",
    "age",
    "date_of_birth",
    "height",
    "weight",
    "eye_color",
    "hair_color",
    "build",
    "skin_tone",
    "state",
    "county",
    "city",
    "address",
    "zip_code",
    "latitude",
    "longitude",
    "offense_type",
    "offense_description",
    "crime",
    "risk_level",
    "conviction_date",
    "registration_date",
    "last_verified",
    "source_state",
    "source_url",
    "scraped_at",
    "external_id",
    "raw_data_json",
    "likely_ethnicity",
    "name_confidence",
    "flags",
    "report_html_path",
    "photo_path",
    "photo_url",
    "sources_json",
)

_HASH_FIELDS = SYNC_ROW_COLUMNS


def _norm(s: Any) -> str:
    return " ".join(str(s or "").strip().casefold().split())


def sync_record_key(record: Dict[str, Any]) -> str:
    """
    Stable public key for one offender row.

    Prefers Database.stable_external_key; falls back to a soft identity hash
    so rows without URL/id still participate in deltas.
    """
    rec = record or {}
    try:
        from scraper.database.dedupe_url_norm import DedupeUrlNormMixin

        sk = DedupeUrlNormMixin.stable_external_key(rec)
        if sk:
            return "k:" + hashlib.sha1(sk.encode("utf-8")).hexdigest()[:24]
    except Exception:
        pass

    soft = "|".join(
        (
            _norm(rec.get("first_name")),
            _norm(rec.get("last_name")),
            _norm(rec.get("date_of_birth")),
            _norm(rec.get("state") or rec.get("source_state")),
            _norm(rec.get("address")),
            _norm(rec.get("photo_url")),
            _norm(rec.get("full_name")),
        )
    )
    if soft.replace("|", ""):
        return "s:" + hashlib.sha1(soft.encode("utf-8")).hexdigest()[:24]
    # Last resort: hash of whatever JSON we have (unstable if empty)
    blob = json.dumps(
        {k: rec.get(k) for k in ("full_name", "crime", "raw_data_json")},
        sort_keys=True,
        default=str,
        ensure_ascii=False,
    )
    return "x:" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:24]


def row_content_hash(record: Dict[str, Any]) -> str:
    """SHA-1 of canonical field payload (change detector for deltas)."""
    payload = []
    for col in _HASH_FIELDS:
        v = record.get(col)
        if v is None:
            payload.append((col, None))
        elif isinstance(v, float):
            payload.append((col, round(v, 6)))
        else:
            payload.append((col, v))
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def row_to_sync_dict(record: Dict[str, Any]) -> Dict[str, Any]:
    """Export only known columns for delta transport."""
    out: Dict[str, Any] = {}
    for col in SYNC_ROW_COLUMNS:
        if col in record:
            out[col] = record.get(col)
    return out


def dict_from_sqlite_row(row: Any, columns: Iterable[str]) -> Dict[str, Any]:
    cols = list(columns)
    if hasattr(row, "keys"):
        return {c: row[c] for c in cols if c in row.keys()}
    return {c: row[i] for i, c in enumerate(cols)}
