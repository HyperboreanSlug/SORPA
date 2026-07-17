"""
Resolve stored source_url values into browser-openable public links.

Florida FDLE quirks:
  - flyer.jsf requires camelCase ``personId=`` (lowercase ``personid=`` shows an empty/invalid flyer)
  - merged multi-jurisdiction URLs like ``https://…fdle… | https://…other…`` 404 if opened whole
  - when no valid person id is present, fall back to the FDLE search home

Michigan mspsor.com:
  - live site uses path-style ``/Home/OffenderDetails/{uuid}`` (search grid + maps)
  - older rows stored ``/Home/OffenderDetails?id={uuid}`` — still rewrite to path form
  - prefer mspsor.com when jurisdiction is MI (avoid WI captcha / foreign hosts)
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

# MA SORB: Tomcat action paths are case-sensitive (lowercase → 404)
_MA_SORB_HOST = "sorb.chs.state.ma.us"
_MA_PATH_FIXES = (
    (re.compile(r"(?i)/viewnsoproffenderdetails\.action"), "/viewNsoprOffenderDetails.action"),
    (re.compile(r"(?i)/viewnsoproffenderimage\.action"), "/viewNsoprOffenderImage.action"),
)

from scraper.public_links_mi import (  # noqa: E402
    MI_MSPSOR_DETAILS_BASE,
    MI_MSPSOR_HOST,
    MI_MSPSOR_SEARCH_HOME,
    extract_mspsor_offender_id,
    is_mspsor_url as _is_mspsor_url,
    normalize_mspsor_url,
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


def _is_ma_sorb_url(url: str) -> bool:
    return _MA_SORB_HOST in (url or "").lower()


def normalize_ma_sorb_url(url: str) -> str:
    """Restore case-sensitive MA SORB action paths (lowercase → 404)."""
    u = (url or "").strip()
    if not u or not _is_ma_sorb_url(u):
        return u
    try:
        p = urlparse(u)
    except Exception:
        return u
    path = p.path or ""
    for pat, repl in _MA_PATH_FIXES:
        path = pat.sub(repl, path)
    scheme = (p.scheme or "https").lower()
    host = (p.netloc or "").lower()
    query = p.query or ""
    return urlunparse((scheme, host, path, "", query, ""))


def _is_fdle_error_page(url: str) -> bool:
    """True for FDLE error404 / error landings (not a usable flyer)."""
    low = (url or "").lower()
    if not low:
        return False
    if "error404" in low or "/error/error" in low:
        return True
    if "error.jsf" in low and _is_fdle_url(low):
        return True
    return False


def _record_flags_list(record: Optional[dict]) -> List[str]:
    """Normalize offenders.flags (list JSON or dict.tags) to string tags."""
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
            import json

            parsed = json.loads(raw)
        except Exception:
            return [raw] if raw.startswith("blocked:") else []
        if isinstance(parsed, list):
            return [str(t) for t in parsed]
        if isinstance(parsed, dict):
            tags = parsed.get("tags")
            if isinstance(tags, list):
                return [str(t) for t in tags]
    return []


def _record_has_http_404_block(record: Optional[dict]) -> bool:
    """Prior report fetch marked this listing as HTTP 404."""
    for t in _record_flags_list(record):
        tl = t.lower()
        if "blocked:http_404" in tl or tl in ("http_404", "blocked:404"):
            return True
    # sources_json html_status
    raw = (record or {}).get("sources_json")
    if not raw:
        return False
    try:
        import json

        srcs = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return False
    if not isinstance(srcs, list):
        return False
    for s in srcs:
        if not isinstance(s, dict):
            continue
        st = str(s.get("html_status") or "").lower()
        if "http_404" in st or st.endswith(":404"):
            return True
        su = str(s.get("source_url") or "").lower()
        if _is_fdle_error_page(su):
            return True
    return False


def resolve_public_source_url(
    raw_url: Optional[str],
    *,
    state: Optional[str] = None,
    prefer_hosts: Optional[Sequence[str]] = None,
    skip_fdle_flyers: bool = False,
) -> str:
    """
    Pick a single browser-safe URL from a stored source_url field.

    - Splits multi-URL merges
    - Fixes Florida FDLE personId casing / empty flyers
    - Fixes Massachusetts SORB action-path casing
    - Skips FDLE error404 landings
    - Falls back to FL search home for Florida when no valid link exists
    """
    urls = split_source_urls(raw_url)
    st = (state or "").strip().upper()
    if " | " in st:
        # multi-jurisdiction: prefer host of the first URL, else first listed code
        # (do NOT force FL just because FL appears — out-of-state GA+FL address)
        from scraper.database.sources import jurisdiction_from_url

        url_jur = ""
        for u in urls:
            url_jur = jurisdiction_from_url(u)
            if url_jur:
                break
        parts = [p.strip().upper() for p in st.split("|") if p.strip()]
        if url_jur and url_jur in parts:
            st = url_jur
        elif parts:
            st = parts[0]
        else:
            st = st.split("|", 1)[0].strip()
    # Prefer state-relevant hosts when known
    if prefer_hosts:
        hosts = [h.lower() for h in prefer_hosts if h]
    elif st == "FL":
        hosts = list(_FDLE_HOST_MARKERS)
    elif st == "MA":
        hosts = [_MA_SORB_HOST]
    elif st == "MI":
        hosts = [MI_MSPSOR_HOST]
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
        if _is_fdle_error_page(u):
            continue
        if _is_fdle_url(u):
            if skip_fdle_flyers and "flyer" in u.lower():
                continue
            fixed = normalize_fdle_flyer_url(u)
            if fixed and not _is_fdle_error_page(fixed):
                return fixed
            # bad FDLE segment — try next
            continue
        if _is_ma_sorb_url(u):
            cleaned = normalize_ma_sorb_url(_strip_jsessionid(u))
            if cleaned:
                return cleaned
            continue
        if _is_mspsor_url(u):
            cleaned = normalize_mspsor_url(_strip_jsessionid(u))
            if cleaned:
                return cleaned
            continue
        # MI rows sometimes hold foreign hosts (WI captcha, etc.) — skip those
        if st == "MI":
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

    # Michigan with no usable deep link → public search
    if st == "MI" or any(_is_mspsor_url(u) for u in urls) or (
        raw_url and _is_mspsor_url(raw_url)
    ):
        return MI_MSPSOR_SEARCH_HOME

    # Last resort: first raw piece or empty (never error404)
    if ordered:
        u0 = ordered[0]
        if _is_fdle_error_page(u0):
            return FL_FDLE_SEARCH_HOME if st == "FL" else ""
        if _is_ma_sorb_url(u0):
            return normalize_ma_sorb_url(u0)
        if _is_mspsor_url(u0):
            return normalize_mspsor_url(u0)
        return u0
    raw = (raw_url or "").strip()
    if _is_fdle_error_page(raw):
        return FL_FDLE_SEARCH_HOME if st == "FL" else ""
    if _is_ma_sorb_url(raw):
        return normalize_ma_sorb_url(raw)
    if _is_mspsor_url(raw):
        return normalize_mspsor_url(raw)
    return raw


def _strip_jsessionid(url: str) -> str:
    try:
        p = urlparse(url)
        # remove ;jsessionid=… from path
        path = re.sub(r";jsessionid=[^/?#]*", "", p.path or "", flags=re.I)
        return urlunparse((p.scheme, p.netloc, path, "", p.query, p.fragment))
    except Exception:
        return url


def openable_url_for_record(record: Optional[dict]) -> str:
    """Convenience: resolve from an offender/misclass record dict.

    Known-dead FDLE flyers (prior ``blocked:http_404`` / error404 URL) open the
    FDLE search home instead of the error page — PERSON_NBR is not always a
    valid flyer ``personId`` (e.g. Carlos Gabriel Ramirez / 19184).
    """
    rec = record or {}
    # Prefer source_state (registry) then residential state
    state = rec.get("source_state") or rec.get("state")
    skip_flyers = _record_has_http_404_block(rec)
    url = resolve_public_source_url(
        rec.get("source_url"),
        state=state,
        skip_fdle_flyers=skip_flyers,
    )
    if url and _is_fdle_error_page(url):
        return FL_FDLE_SEARCH_HOME
    return url
