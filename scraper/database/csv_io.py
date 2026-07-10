"""CSV import/export for offender records."""
from __future__ import annotations

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


class CsvMixin:
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


