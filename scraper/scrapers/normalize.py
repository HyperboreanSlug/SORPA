"""Shared record normalization helpers for scrapers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def clean_key(key: Any) -> str:
    """Normalize CSV/JSON keys (strip BOM, whitespace)."""
    if key is None:
        return ""
    text = str(key).replace("\ufeff", "").strip()
    return text


def clean_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def normalize_record(record: Dict[str, Any], state: Optional[str] = None) -> Dict[str, Any]:
    """Clean keys/values and map common field aliases to standard names."""
    out: Dict[str, Any] = {}
    for key, value in record.items():
        k = clean_key(key)
        if not k:
            continue
        out[k] = clean_value(value)

    # Common aliases → standard fields (only fill if missing)
    alias_map = {
        "LASTNAME": "last_name",
        "LastName": "last_name",
        "LAST_NAME": "last_name",
        "Last Name": "last_name",
        "FIRSTNAME": "first_name",
        "FirstName": "first_name",
        "FIRST_NAME": "first_name",
        "First Name": "first_name",
        "MIDDLENAME": "middle_name",
        "MiddleName": "middle_name",
        "MIDDLE_NAME": "middle_name",
        "Middle Name": "middle_name",
        "Middle": "middle_name",
        "NAME": "full_name",
        "Name": "full_name",
        "RACE": "race",
        "Race": "race",
        "SEX": "gender",
        "Sex": "gender",
        "GENDER": "gender",
        "Gender": "gender",
        "COUNTY": "county",
        "County": "county",
        "CITY": "city",
        "City": "city",
        "ADDRESS": "address",
        "Address": "address",
        "STREET": "address",
        "YEAR OF BIRTH": "date_of_birth",
        "BIRTHDATE": "date_of_birth",
        "DOB": "date_of_birth",
        "HEIGHT": "height",
        "WEIGHT": "weight",
        "EYE COLOR": "eye_color",
        "EYECOLOR": "eye_color",
        "HAIR COLOR": "hair_color",
        "HAIRCOLOR": "hair_color",
    }
    for src, dest in alias_map.items():
        if src in out and dest not in out:
            out[dest] = out[src]

    # Split full_name when parts missing
    full = out.get("full_name") or out.get("NAME")
    if full and not out.get("last_name"):
        parts = str(full).replace(",", " ").split()
        if len(parts) >= 3:
            out.setdefault("first_name", parts[0])
            out.setdefault("middle_name", " ".join(parts[1:-1]))
            out.setdefault("last_name", parts[-1])
        elif len(parts) >= 2:
            # Prefer "LAST, FIRST" style already handled by replace
            out.setdefault("first_name", parts[0] if "," not in str(full) else " ".join(parts[1:]))
            out.setdefault("last_name", parts[-1] if "," not in str(full) else parts[0])
        elif parts:
            out.setdefault("last_name", parts[0])

    # Handle "LAST, FIRST MIDDLE" in NAME
    name_raw = out.get("full_name") or out.get("NAME")
    if name_raw and "," in str(name_raw) and not out.get("first_name"):
        last, rest = str(name_raw).split(",", 1)
        out["last_name"] = last.strip()
        rest_parts = rest.strip().split()
        out["first_name"] = rest_parts[0] if rest_parts else None
        if len(rest_parts) >= 2 and not out.get("middle_name"):
            out["middle_name"] = " ".join(rest_parts[1:])
        mid = (out.get("middle_name") or "").strip()
        out["full_name"] = " ".join(
            p for p in (out.get("first_name"), mid or None, out.get("last_name")) if p
        ).strip()

    # Multi-token first_name → first + middle when middle empty
    first = str(out.get("first_name") or "").strip()
    mid = str(out.get("middle_name") or "").strip()
    if first and not mid:
        fparts = first.split()
        if len(fparts) >= 2:
            out["first_name"] = fparts[0]
            out["middle_name"] = " ".join(fparts[1:])

    if state:
        out.setdefault("state", state)
        out.setdefault("source_state", state)

    return out


def normalize_records(
    records: List[Dict[str, Any]],
    state: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return [normalize_record(r, state=state) for r in records if r]
