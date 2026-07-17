"""Michigan mspsor.com URL canonicalization."""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urlparse, urlunparse

MI_MSPSOR_HOST = "mspsor.com"
MI_MSPSOR_SEARCH_HOME = "https://mspsor.com/Home/Search"
MI_MSPSOR_DETAILS_BASE = "https://mspsor.com/Home/OffenderDetails"

_UUID_RE = re.compile(
    r"(?i)\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b"
)
_MI_DETAILS_PATH_RE = re.compile(
    r"(?i)/Home/OffenderDetails(?:/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}))?"
)


def is_mspsor_url(url: str) -> bool:
    return "mspsor.com" in (url or "").lower()


def extract_mspsor_offender_id(url: str) -> Optional[str]:
    """Return the offender UUID from a mspsor detail URL, if present."""
    u = (url or "").strip()
    if not u or not is_mspsor_url(u):
        return None
    try:
        p = urlparse(u)
    except Exception:
        return None
    path = p.path or ""
    m = _MI_DETAILS_PATH_RE.search(path)
    if m and m.group(1):
        return m.group(1).lower()
    qs = parse_qs(p.query or "", keep_blank_values=False)
    for key in ("id", "Id", "ID", "offenderId", "OffenderId"):
        if key in qs and qs[key]:
            cand = (qs[key][0] or "").strip()
            if _UUID_RE.fullmatch(cand):
                return cand.lower()
    for k, vals in qs.items():
        if k.lower() == "id" and vals:
            cand = (vals[0] or "").strip()
            if _UUID_RE.fullmatch(cand):
                return cand.lower()
    if "offenderdetails" in path.replace("\\", "/").lower():
        m2 = _UUID_RE.search(u)
        if m2:
            return m2.group(1).lower()
    return None


def normalize_mspsor_url(url: str) -> str:
    """
    Canonical Michigan detail link:
    ``https://mspsor.com/Home/OffenderDetails/{uuid}``.
    """
    u = (url or "").strip()
    if not u or not is_mspsor_url(u):
        return u
    oid = extract_mspsor_offender_id(u)
    if oid:
        return f"{MI_MSPSOR_DETAILS_BASE}/{oid}"
    try:
        p = urlparse(u)
        path = (p.path or "").rstrip("/").lower()
    except Exception:
        return MI_MSPSOR_SEARCH_HOME
    if not path or path in ("", "/home", "/home/search"):
        return MI_MSPSOR_SEARCH_HOME
    if "offenderdetails" in path and not oid:
        return MI_MSPSOR_SEARCH_HOME
    clean_path = p.path or "/"
    if re.search(r"(?i)^/home/search", clean_path):
        clean_path = "/Home/Search"
    return urlunparse(("https", MI_MSPSOR_HOST, clean_path, "", p.query or "", ""))
