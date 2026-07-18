"""Statute number → human charge label reference.

Bare registry dumps like ``Statute Number(s): 28-320.01`` must never ship as
the only crime text. Lookup expands codes to real offense names for Reports
and export cards.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

_LABELS_PATH = Path(__file__).resolve().parent / "statute_labels.json"

# Attempt / conspiracy prefixes (state-specific)
_ATTEMPT_CODES = frozenset(
    {
        "28-201",  # Nebraska criminal attempt
        "18-2-101",  # CO attempt (partial)
    }
)

# Pull statute tokens from free text / "Statute Number(s): …"
_CODE_TOKEN = re.compile(
    r"(?ix)"
    r"(?:F\.?S\.?\s*|N\.?R\.?S\.?\s*|C\.?R\.?S\.?\s*|§\s*)?"
    r"("
    r"18\s*U\.?S\.?C\.?\s*§?\s*\d{3,4}[A-Za-z]?(?:\([a-z0-9]+\))*"
    r"|\d{1,2}\.\d{1,2}(?:-\d+(?:\.\d+)*)+(?:\([a-z0-9]+\))*"  # VA 18.2-67.3
    r"|\d{2,3}-\d{1,4}(?:\.\d+)?(?:\([a-z0-9]+\))*"  # NE 28-320.01 / CO 18-3-402
    r"|\d{3}\.\d{1,4}(?:\([a-z0-9]+\))*"  # FL 800.04 / IA 709.8
    r")"
)

_STATUTE_ONLY_LINE = re.compile(
    r"(?ix)^\s*(?:statute\s*number\(s\)?\s*:?\s*)?"
    r"[\d.\-()A-Za-z\s,;|/§]+\s*$"
)


@lru_cache(maxsize=1)
def _labels() -> dict:
    if not _LABELS_PATH.is_file():
        return {}
    try:
        raw = json.loads(_LABELS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        nk = normalize_statute_key(str(k))
        lab = " ".join(str(v or "").split()).strip()
        if nk and lab:
            out[nk] = lab
    return out


def normalize_statute_key(code: str) -> str:
    """Canonical key for lookup (uppercase, no spaces/§)."""
    s = (code or "").strip().upper()
    if not s:
        return ""
    s = re.sub(r"^(?:F\.?S\.?|N\.?R\.?S\.?|C\.?R\.?S\.?)\s*", "", s)
    s = s.replace("§", "")
    s = re.sub(r"\s+", "", s)
    # 18USC2252A form
    s = re.sub(r"18U\.?S\.?C\.?", "18USC", s)
    s = s.replace("USC.", "USC")
    return s


def _fallback_keys(key: str) -> List[str]:
    """Progressive keys: full → strip paren subsections only."""
    keys = [key]
    k = key
    while True:
        m = re.match(r"^(.+)(\([a-z0-9]+\))$", k, flags=re.I)
        if not m:
            break
        k = m.group(1)
        keys.append(k)
    return keys


def lookup_statute(code: str) -> Optional[str]:
    """Return regular-case charge label for a statute code, or None."""
    key = normalize_statute_key(code)
    if not key:
        return None
    table = _labels()
    for k in _fallback_keys(key):
        lab = table.get(k)
        if lab:
            return lab
    return None


def extract_statute_codes(text: str) -> List[str]:
    """Ordered unique statute codes found in text."""
    seen = set()
    out: List[str] = []
    for m in _CODE_TOKEN.finditer(text or ""):
        raw = m.group(1)
        key = normalize_statute_key(raw)
        if not key or key in seen:
            continue
        # Skip tiny bare numbers that are not real codes
        if re.fullmatch(r"\d{1,2}", key):
            continue
        seen.add(key)
        out.append(key)
    return out


def _apply_attempt(label: str) -> str:
    low = label.casefold()
    if low.startswith("attempt"):
        return label
    # "First degree…" → "Attempted first degree…"
    return "Attempted " + label[:1].lower() + label[1:]


def labels_for_statute_text(text: str) -> List[str]:
    """Expand one clause of statute codes into charge labels."""
    codes = extract_statute_codes(text)
    if not codes:
        return []
    labels: List[str] = []
    pending_attempt = False
    for code in codes:
        base = re.sub(r"\([^)]*\)", "", code)  # 28-201(1) → 28-201 for attempt set
        if base in _ATTEMPT_CODES or code in _ATTEMPT_CODES:
            pending_attempt = True
            continue
        lab = lookup_statute(code)
        if not lab:
            continue
        if pending_attempt:
            lab = _apply_attempt(lab)
            pending_attempt = False
        if lab not in labels:
            labels.append(lab)
    # Attempt with no following code — leave off rather than "Criminal attempt" alone
    # unless it was the only meaningful expansion
    if not labels and pending_attempt:
        lab = lookup_statute("28-201")
        if lab:
            labels.append(lab)
    return labels


# FL/others attach English next to F.S. / s. cites — never expand those alone
_FS_CITE_PREFIX = re.compile(r"(?i)\b(?:f\.?s\.?|s\.)\s*\d")


def is_statute_number_dump(text: str) -> bool:
    """True for bare registry statute fields (not F.S. cites next to English)."""
    raw = " ".join((text or "").split()).strip()
    if not raw:
        return False
    low = raw.casefold()
    if re.search(r"statute\s*number", low):
        return True
    # Explicit F.S. / s. cites belong to multi-clause dumps with English text
    if _FS_CITE_PREFIX.search(raw):
        return False
    letters = sum(1 for c in raw if c.isalpha())
    digits = sum(1 for c in raw if c.isdigit())
    # Pure / nearly pure code lists: "28-201 28-319(1)(c)" / "28-320.01"
    if digits >= 3 and letters <= max(digits + 6, 14):
        return True
    if _STATUTE_ONLY_LINE.match(raw) and letters < 28 and digits >= 3:
        return True
    return False


def expand_statutes(text: str) -> Optional[str]:
    """
    If *text* is a bare statute-number dump, return joined human labels.
    Returns None when normal English offense parsing should run instead.
    """
    raw = " ".join((text or "").split()).strip()
    if not raw or not is_statute_number_dump(raw):
        return None
    labels = labels_for_statute_text(raw)
    if not labels:
        return None
    return " · ".join(labels)
