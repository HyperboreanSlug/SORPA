from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from scraper.database.csv_helpers import *  # noqa: F401,F403
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

class NormalizeRecordCsvMixin:
    def _normalize_record(self, record: Dict[str, Any]) -> None:
        """Normalize common column name variations (incl. FDLE FL SOR bulk)."""
        name_map = {
            "Name": "full_name",
            "Offender Name": "full_name",
            "First Name": "first_name",
            "FirstName": "first_name",
            "FIRST_NAME": "first_name",
            "Middle Name": "middle_name",
            "MiddleName": "middle_name",
            "MIDDLE_NAME": "middle_name",
            "Middle": "middle_name",
            "Last Name": "last_name",
            "LastName": "last_name",
            "LAST_NAME": "last_name",
            "Race": "race",
            "RACE": "race",
            "Ethnicity": "ethnicity",
            "Gender": "gender",
            "SEX": "gender",
            "Sex": "gender",
            "Age": "age",
            "DOB": "date_of_birth",
            "Date of Birth": "date_of_birth",
            "BIRTH_DATE": "date_of_birth",
            "Height": "height",
            "HEIGHT": "height",
            "Weight": "weight",
            "WEIGHT": "weight",
            "Eye Color": "eye_color",
            "EYE_COLOR": "eye_color",
            "EYECOLOR": "eye_color",
            "Hair Color": "hair_color",
            "HAIR": "hair_color",
            "HAIR_COLOR": "hair_color",
            "HAIRCOLOR": "hair_color",
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
            "IMAGE_URL": "photo_url",
            "PERSON_NBR": "external_id",
            "PERSON_NUMBER": "external_id",
            # FL permanent address columns
            "PERM_ADDRESS_LINE_1": "address",
            "PERM_CITY": "city",
            "PERM_STATE": "state",
            "PERM_ZIP5": "zip_code",
            "PERM_COUNTY": "county",
        }

        new_record: Dict[str, Any] = {}
        for key, value in record.items():
            if key is None:
                continue
            key_str = str(key).strip()
            if not key_str:
                continue
            normalized_key = name_map.get(
                key_str, name_map.get(key_str.upper(), key_str.lower().replace(" ", "_"))
            )
            if value is None or (isinstance(value, str) and not value.strip()):
                # Don't overwrite a mapped field with empty later columns
                if normalized_key not in new_record:
                    new_record[normalized_key] = None
            else:
                # Prefer first non-empty for address-style maps
                if normalized_key in new_record and new_record[normalized_key]:
                    continue
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

        # Do NOT copy residential state (e.g. PERM_STATE) into source_state
        # here — bulk CSV import sets source_state to the *publishing*
        # jurisdiction (FL for fl_sor.csv) after normalize.

        # Preserve already-attached sources_json if present on the dict
        if record.get("sources_json") and not new_record.get("sources_json"):
            new_record["sources_json"] = record.get("sources_json")

        record.clear()
        record.update(new_record)

