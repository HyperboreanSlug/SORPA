"""Detect deported status from registry address / location fields."""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

# DEPORTED TO MEXICO · 000 DEPORTED · UNKNOWN - DEPORTED · city/county DEPORTED
_DEPORT_RE = re.compile(
    r"(?i)\bdeport(?:ed|ation|ee|ing)?\b|\bdeportee\b"
)

_FIELDS = (
    "address",
    "city",
    "county",
    "residence",
    "residential_address",
    "location",
    "status",
    "registration_status",
    "absconder",
)


def is_deported(record: Optional[Mapping[str, Any]]) -> bool:
    """True when any location/status field marks the person as deported."""
    if not record:
        return False
    chunks: list[str] = []
    for key in _FIELDS:
        val = record.get(key)
        if val is None:
            continue
        s = str(val).strip()
        if s:
            chunks.append(s)
    # flags may carry a free-text tag
    flags = record.get("flags")
    if isinstance(flags, list):
        chunks.extend(str(t) for t in flags if t)
    elif isinstance(flags, str) and flags.strip():
        chunks.append(flags)
    blob = " | ".join(chunks)
    if not blob:
        return False
    return bool(_DEPORT_RE.search(blob))


def format_listed_banner(race: str, record: Optional[Mapping[str, Any]] = None) -> str:
    """Block-letter race banner: ``LISTED WHITE`` or ``LISTED WHITE - DEPORTED``."""
    race_u = str(race or "").strip().upper()
    # Strip checkmark / verified suffix from race labels (e.g. "White ✓")
    for junk in (" ✓", "✔", "☑", "✓"):
        race_u = race_u.replace(junk, "")
    race_u = race_u.strip()
    if race_u in ("", "UNKNOWN", "-", "N/A", "NA", "—", "–"):
        race_u = ""
    if is_deported(record):
        if race_u:
            return f"LISTED {race_u} - DEPORTED"
        return "LISTED - DEPORTED"
    return f"LISTED {race_u}" if race_u else "LISTED -"


def format_export_race_label(race: str, record: Optional[Mapping[str, Any]] = None) -> str:
    """Export-card race line: ``WHITE`` or ``WHITE - DEPORTED``."""
    race_u = str(race or "").strip().upper()
    for junk in (" ✓", "✔", "☑", "✓"):
        race_u = race_u.replace(junk, "")
    race_u = race_u.strip()
    if not race_u or race_u in ("UNKNOWN", "-", "N/A", "NA", "—", "–"):
        race_u = ""
    if is_deported(record):
        return f"{race_u} - DEPORTED" if race_u else "DEPORTED"
    return race_u
