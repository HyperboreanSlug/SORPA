"""Colorado CBI SOR URL canonicalization.

Public portal still advertises ``www.colorado.gov/apps/cdps/sor/…`` but the
live host is ``apps.colorado.gov/apps/dps/sor/…``. Offender ``id`` values are
**case-sensitive** (``XX55195899`` works; ``xx55195899`` does not).
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

CO_SOR_HOST = "apps.colorado.gov"
CO_SOR_SEARCH_HOME = "https://apps.colorado.gov/apps/dps/sor/"
CO_SOR_DETAIL_BASE = (
    "https://apps.colorado.gov/apps/dps/sor/search/search-detail.jsf"
)

_CO_HOST_MARKERS = (
    "apps.colorado.gov",
    "www.colorado.gov",
    "colorado.gov",
)
# X002449587 / XX55195899 (1–2 leading X + digits)
_CO_ID_RE = re.compile(r"(?i)\b(x{1,2}\d{6,14})\b")
_CO_ID_QUERY_RE = re.compile(r"(?i)(?:[?&])id=([^&#\s]+)")


def is_co_sor_url(url: str) -> bool:
    low = (url or "").lower()
    if not any(h in low for h in _CO_HOST_MARKERS):
        return False
    return "/apps/cdps/sor" in low or "/apps/dps/sor" in low or "search-detail" in low


def extract_co_offender_id(url_or_text: str) -> Optional[str]:
    """Return canonical uppercase CBI offender id, or None."""
    text = (url_or_text or "").strip()
    if not text:
        return None
    m = _CO_ID_QUERY_RE.search(text)
    if m:
        cand = (m.group(1) or "").strip()
        # strip accidental junk after id
        cand = re.split(r"[\s|&]", cand, maxsplit=1)[0]
        if _CO_ID_RE.fullmatch(cand):
            return cand.upper()
    m2 = _CO_ID_RE.search(text)
    if m2:
        return m2.group(1).upper()
    try:
        qs = parse_qs(urlparse(text).query or "", keep_blank_values=False)
    except Exception:
        qs = {}
    for key in ("id", "Id", "ID"):
        if key in qs and qs[key]:
            cand = (qs[key][0] or "").strip()
            if _CO_ID_RE.fullmatch(cand):
                return cand.upper()
    return None


def co_detail_url(offender_id: str) -> str:
    oid = extract_co_offender_id(offender_id) or (offender_id or "").strip().upper()
    if not oid or not _CO_ID_RE.fullmatch(oid):
        return CO_SOR_SEARCH_HOME
    # ext=t matches public "have you seen" deep links on the CBI portal
    return f"{CO_SOR_DETAIL_BASE}?id={oid}&ext=t"


def normalize_co_sor_url(url: str) -> str:
    """
    Canonical openable Colorado detail link.

    - ``www.colorado.gov/apps/cdps/sor`` → ``apps.colorado.gov/apps/dps/sor``
    - Force uppercase ``id=`` (case-sensitive on the live site)
    - Append ``ext=t`` for external deep-link form
    - Bare CO host / no id → search home
    """
    u = (url or "").strip()
    if not u:
        return u
    if not is_co_sor_url(u) and not extract_co_offender_id(u):
        return u
    oid = extract_co_offender_id(u)
    if oid:
        return co_detail_url(oid)
    if is_co_sor_url(u):
        return CO_SOR_SEARCH_HOME
    return u
