"""
Resolve stored source_url values into browser-openable public links.

Florida FDLE quirks:
  - flyer.jsf requires camelCase ``personId=`` (lowercase ``personid=`` shows an empty/invalid flyer)
  - merged multi-jurisdiction URLs like ``https://…fdle… | https://…other…`` 404 if opened whole
  - when no valid person id is present, fall back to the FDLE search home
"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# Public FDLE search entry (stable; no session-bound flyer required)
FL_FDLE_SEARCH_HOME = "https://offender.fdle.state.fl.us/offender/sops/search.jsf"
FL_FDLE_HOME = "https://offender.fdle.state.fl.us/offender/sops/home.jsf"
FL_FDLE_FLYER_BASE = "https://offender.fdle.state.fl.us/offender/sops/flyer.jsf"

_FDLE_HOST_MARKERS = (
    "offender.fdle.state.fl.us",
    "fdle.state.fl.us",
)

_MULTI_URL_SPLIT = re.compile(r"\s*\|\s*")
_PERSON_ID_RE = re.compile(r"(?i)(?:[?&])personid=([^&#\s]+)")


def split_source_urls(raw: Optional[str]) -> List[str]:
    """Split merged multi-jurisdiction source_url blobs into individual http(s) links."""
    text = (raw or "").strip()
    if not text:
        return []
    parts = _MULTI_URL_SPLIT.split(text)
    out: List[str] = []
    for p in parts:
        u = p.strip().strip("'\"")
        if not u:
            continue
        # tolerate missing scheme on rare rows
        if u.startswith("//"):
            u = "https:" + u
        if re.match(r"^https?://", u, re.I):
            out.append(u)
    return out


def _is_fdle_url(url: str) -> bool:
    low = (url or "").lower()
    return any(h in low for h in _FDLE_HOST_MARKERS)


def extract_fdle_person_id(url: str) -> Optional[str]:
    """Return personId digits from an FDLE flyer (or similar) URL, if present."""
    if not url:
        return None
    m = _PERSON_ID_RE.search(url)
    if not m:
        # path-style fallbacks
        m2 = re.search(r"(?i)/personid/(\d+)", url)
        if m2:
            return m2.group(1)
        return None
    pid = (m.group(1) or "").strip()
    # strip accidental trailing junk
    pid = re.sub(r"[^\w\-]", "", pid)
    return pid or None


def normalize_fdle_flyer_url(url: str) -> Optional[str]:
    """
    Rewrite FDLE flyer links to the canonical form that browsers can open.

    Returns None if the URL is FDLE but has no usable person id (caller should
    fall back to search home).
    """
    if not _is_fdle_url(url):
        return None
    pid = extract_fdle_person_id(url)
    if not pid:
        # Bare FDLE host / search / home — send to search home
        low = url.lower()
        if "flyer" in low or "personid" in low:
            return None
        if "search" in low or "home" in low or low.rstrip("/").endswith("fdle.state.fl.us"):
            return FL_FDLE_SEARCH_HOME
        return FL_FDLE_SEARCH_HOME
    # Always use https + camelCase personId (lowercase personid shows empty flyer)
    return f"{FL_FDLE_FLYER_BASE}?personId={pid}"


def resolve_public_source_url(
    raw_url: Optional[str],
    *,
    state: Optional[str] = None,
    prefer_hosts: Optional[Sequence[str]] = None,
) -> str:
    """
    Pick a single browser-safe URL from a stored source_url field.

    - Splits multi-URL merges
    - Fixes Florida FDLE personId casing / empty flyers
    - Falls back to FL search home for Florida when no valid link exists
    """
    urls = split_source_urls(raw_url)
    st = (state or "").strip().upper()
    # Prefer state-relevant hosts when known
    if prefer_hosts:
        hosts = [h.lower() for h in prefer_hosts if h]
    elif st == "FL":
        hosts = list(_FDLE_HOST_MARKERS)
    else:
        hosts = []

    ordered: List[str] = []
    if hosts:
        for u in urls:
            low = u.lower()
            if any(h in low for h in hosts):
                ordered.append(u)
        for u in urls:
            if u not in ordered:
                ordered.append(u)
    else:
        ordered = list(urls)

    for u in ordered:
        if _is_fdle_url(u):
            fixed = normalize_fdle_flyer_url(u)
            if fixed:
                return fixed
            # bad FDLE segment — try next
            continue
        # Non-FDLE: strip jsessionid noise from path for cleanliness
        cleaned = _strip_jsessionid(u)
        if cleaned:
            return cleaned

    # Florida with no usable deep link → search home
    if st == "FL" or any(_is_fdle_url(u) for u in urls) or (
        raw_url and _is_fdle_url(raw_url)
    ):
        return FL_FDLE_SEARCH_HOME

    # Last resort: first raw piece or empty
    if ordered:
        return ordered[0]
    return (raw_url or "").strip()


def _strip_jsessionid(url: str) -> str:
    try:
        p = urlparse(url)
        # remove ;jsessionid=… from path
        path = re.sub(r";jsessionid=[^/?#]*", "", p.path or "", flags=re.I)
        return urlunparse((p.scheme, p.netloc, path, "", p.query, p.fragment))
    except Exception:
        return url


def openable_url_for_record(record: Optional[dict]) -> str:
    """Convenience: resolve from an offender/misclass record dict."""
    rec = record or {}
    return resolve_public_source_url(
        rec.get("source_url"),
        state=rec.get("state") or rec.get("source_state"),
    )
