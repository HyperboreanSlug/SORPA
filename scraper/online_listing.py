"""Detect when a registry listing is not available online (dead / 404 URL)."""
from __future__ import annotations

import json
import re
from typing import Any, List, Mapping, Optional

# Shown on Reports cards when the live listing is gone.
UNAVAILABLE_ONLINE_LABEL = "NOT AVAILABLE ONLINE"

_ERROR404_RE = re.compile(r"(?i)error404|/error/error|error\.jsf")


def _flags_list(record: Optional[Mapping[str, Any]]) -> List[str]:
    if not record:
        return []
    raw = record.get("flags")
    if isinstance(raw, list):
        return [str(t) for t in raw]
    if isinstance(raw, dict):
        tags = raw.get("tags")
        if isinstance(tags, list):
            return [str(t) for t in tags]
        return []
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            return [raw] if "blocked:" in raw.lower() else []
        if isinstance(parsed, list):
            return [str(t) for t in parsed]
        if isinstance(parsed, dict):
            tags = parsed.get("tags")
            if isinstance(tags, list):
                return [str(t) for t in tags]
            # Nested flags blob sometimes stored as a single JSON string element
            return []
    return []


def _sources_list(record: Optional[Mapping[str, Any]]) -> List[dict]:
    if not record:
        return []
    raw = record.get("sources_json")
    if not raw:
        return []
    try:
        srcs = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []
    if not isinstance(srcs, list):
        return []
    return [s for s in srcs if isinstance(s, dict)]


def _url_is_error_page(url: str) -> bool:
    return bool(url and _ERROR404_RE.search(url))


def listing_unavailable_online(record: Optional[Mapping[str, Any]]) -> bool:
    """True when prior scrape proved the live listing is gone or is an error page.

    Uses stored flags / sources_json (e.g. ``blocked:http_404``) — no live HTTP.
    Example: Jacinto Calderon FDLE flyer ``personId=17757`` → 404.
    """
    if not record:
        return False
    for t in _flags_list(record):
        tl = t.lower()
        if "blocked:http_404" in tl or tl in ("http_404", "blocked:404"):
            return True
        if "blocked:http_410" in tl or "gone" == tl:
            return True
    raw_url = str(record.get("source_url") or "")
    if _url_is_error_page(raw_url):
        return True
    for s in _sources_list(record):
        st = str(s.get("html_status") or "").lower()
        if "http_404" in st or st.endswith(":404") or "http_410" in st:
            return True
        if _url_is_error_page(str(s.get("source_url") or "")):
            return True
    return False


def online_status_label(record: Optional[Mapping[str, Any]]) -> str:
    """Banner text for Reports, or empty when listing may still be online."""
    if listing_unavailable_online(record):
        return UNAVAILABLE_ONLINE_LABEL
    return ""
