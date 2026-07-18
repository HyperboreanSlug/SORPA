"""
Fetch jurisdiction offender report pages linked from NSOPW, extract demographics,
and optionally archive the raw HTML next to the database for offline validation.

When archiving HTML, remote <img> assets are downloaded beside the page and src
attributes rewritten so the offline HTML still shows offender photos.
"""

from __future__ import annotations

import base64
import html as html_lib
import mimetypes
import re
import time
from hashlib import sha1
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from scraper.config import DEFAULT_DELAY, REQUEST_TIMEOUT
from scraper.cookie_jar import CaptchaQueue, CookieJarStore

# Prefer a browser UA for state sites (many WAF on custom bots).
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_LABEL_MAP = {
    "race": "race",
    "racial": "race",
    "ethnicity": "ethnicity",
    "ethnic origin": "ethnicity",
    "sex": "gender",
    "gender": "gender",
    "height": "height",
    "weight": "weight",
    "eye color": "eye_color",
    "eyes": "eye_color",
    "hair color": "hair_color",
    "hair": "hair_color",
    "skin tone": "skin_tone",
    "complexion": "skin_tone",
    "build": "build",
    "age": "age",
    "date of birth": "date_of_birth",
    "dob": "date_of_birth",
    "birth date": "date_of_birth",
    "county": "county",
    "city": "city",
    "address": "address",
    "risk level": "risk_level",
    # Crime / offense (primary display field is "crime")
    "crime": "crime",
    "crimes": "crime",
    "offense": "crime",
    "offenses": "crime",
    "offense type": "offense_type",
    "offense description": "crime",
    "offense details": "crime",
    "offense title": "crime",
    "charge": "crime",
    "charges": "crime",
    "conviction offense": "crime",
    "convicting offense": "crime",
    # NV sexoffenders.nv.gov offense table column
    "conviction description": "crime",
    "qualifying offense": "crime",
    "registerable offense": "crime",
    "registrable offense": "crime",
    "registration offense": "crime",
    "sex offense": "crime",
    "sexual offense": "crime",
    "primary offense": "crime",
    "statute": "crime",
    "statute description": "crime",
    "violation": "crime",
    "violations": "crime",
    "crime description": "crime",
    "description of offense": "crime",
    "description of crime": "crime",
    # iCrimeWatch offense rows: "• Description:" → charge text in next cell
    "description": "crime",
    "conviction": "conviction_date",
    "conviction date": "conviction_date",
}

# Labels that may have long multi-line values (allow longer text)
_LONG_VALUE_KEYS = frozenset({"crime", "offense_type", "offense_description", "address"})
_MAX_CRIME_LEN = 800

_CAPTCHA_MARKERS = (
    "recaptcha",
    "hcaptcha",
    "cf-turnstile",
    "captcha",
    "just a moment",
    "datadome",
    "access denied",
    "bot detection",
)
_DISCLAIMER_MARKERS = (
    "conditions of use",
    "terms and conditions",
    "you must agree",
    "accept the terms",
    "disclaimer",
    "i agree",
    "by clicking accept",
)


def _normalize_label(raw: str) -> str:
    s = re.sub(r"\s+", " ", (raw or "").strip().lower())
    # iCrimeWatch / OffenderWatch: "• Race:", "&bull; Eyes:", etc.
    s = re.sub(r"^[\u2022\u00b7•·\-\*]+\s*", "", s)
    s = s.replace("&bull;", "").strip()
    return s.rstrip(":").strip()


def _clean_value(raw: str) -> str:
    """Collapse whitespace / newlines and decode HTML entities."""
    s = html_lib.unescape(raw or "")
    return re.sub(r"\s+", " ", s).strip()


def _normalize_url(url: str) -> str:
    """Fix scheme case, drop :80, and rewrite known gateway hosts."""
    url = html_lib.unescape((url or "").strip())
    m = re.match(r"^(https?)://(.*)$", url, flags=re.I)
    if m:
        url = m.group(1).lower() + "://" + m.group(2)
    # Drop default port 80 on http(s) hosts
    url = re.sub(r"^(https?://[^/:]+):80(?=/|$)", r"\1", url)
    # Colorado: cdps portal → live apps host; uppercase case-sensitive id
    try:
        from scraper.public_links_co import is_co_sor_url, normalize_co_sor_url

        if is_co_sor_url(url):
            return normalize_co_sor_url(url)
    except Exception:
        url = url.replace(
            "www.colorado.gov/apps/cdps/sor",
            "apps.colorado.gov/apps/dps/sor",
        )
        url = url.replace(
            "colorado.gov/apps/cdps/sor",
            "apps.colorado.gov/apps/dps/sor",
        )
    # MA SORB: restore case-sensitive action paths
    if "sorb.chs.state.ma.us" in url.lower():
        try:
            from scraper.public_links import normalize_ma_sorb_url

            url = normalize_ma_sorb_url(url)
        except Exception:
            url = re.sub(
                r"(?i)/viewnsoproffenderdetails\.action",
                "/viewNsoprOffenderDetails.action",
                url,
            )
            url = re.sub(
                r"(?i)/viewnsoproffenderimage\.action",
                "/viewNsoprOffenderImage.action",
                url,
            )
    return url


# Host fragments → state code for photo storage folders
_PHOTO_HOST_STATE = (
    ("scor.sled.sc.gov", "SC"),
    ("sled.sc.gov", "SC"),
    ("sor.tbi.tn.gov", "TN"),
    ("tbi.tn.gov", "TN"),
    ("offender.fdle.state.fl.us", "FL"),
    ("fdle.state.fl.us", "FL"),
    ("state.sor.gbi.ga.gov", "GA"),
    ("gbi.ga.gov", "GA"),
)


def photo_state_from_url(photo_url: str) -> Optional[str]:
    """Infer registry state from a dedicated photo URL host (SC/TN/FL…)."""
    try:
        host = (urlparse(_normalize_url(photo_url)).netloc or "").lower()
    except Exception:
        return None
    if not host:
        return None
    for frag, st in _PHOTO_HOST_STATE:
        if frag in host:
            return st
    return None


def photo_url_variants(photo_url: str) -> List[str]:
    """
    Candidate URLs for a mugshot download.

    SC SLED DisplayImage.aspx often returns an empty GIF for Thumb=false but a
    real PNG/JPEG for Thumb=true (Content-Type may still say image/gif).

    AL / iCrimewatch: NSOPW may hand out wsdocs.watchsystems.com while the live
    page uses docs.watchsystems.com (and vice versa) — try both.
    """
    url = _normalize_url((photo_url or "").strip())
    if not url:
        return []
    out: List[str] = []
    seen: Set[str] = set()

    def _add(u: str) -> None:
        u = _normalize_url(u)
        if u and u not in seen:
            seen.add(u)
            out.append(u)

    _add(url)
    low = url.lower()
    if "displayimage.aspx" in low or "displayimage" in low:
        # Flip Thumb= true/false
        if re.search(r"thumb=false", url, flags=re.I):
            _add(re.sub(r"thumb=false", "Thumb=true", url, flags=re.I))
        elif re.search(r"thumb=true", url, flags=re.I):
            _add(re.sub(r"thumb=true", "Thumb=false", url, flags=re.I))
        else:
            sep = "&" if "?" in url else "?"
            _add(url + sep + "Thumb=true")
            _add(url + sep + "Thumb=false")
    # iCrimewatch / WatchSystems CDN host aliases (AL and many sheriff portals)
    if "watchsystems.com" in low:
        if "wsdocs.watchsystems.com" in low:
            _add(re.sub(r"wsdocs\.watchsystems\.com", "docs.watchsystems.com", url, flags=re.I))
        if "docs.watchsystems.com" in low and "wsdocs" not in low:
            _add(re.sub(r"(?<!ws)docs\.watchsystems\.com", "wsdocs.watchsystems.com", url, flags=re.I))
            # also plain docs → wsdocs
            _add(url.replace("docs.watchsystems.com", "wsdocs.watchsystems.com"))
            _add(url.replace("Docs.watchsystems.com", "wsdocs.watchsystems.com"))
        # http/https already normalized; try both schemes lightly
        if url.startswith("https://"):
            _add("http://" + url[len("https://") :])
        elif url.startswith("http://"):
            _add("https://" + url[len("http://") :])
    return out


def extract_dedicated_photo_urls(html: str, base_url: str = "") -> List[str]:
    """
    Pull dedicated mugshot URLs from report HTML (before/without asset rewrite).

    Prefers WatchSystems /pictures/ paths (AL iCrimewatch) over /offices/ banners.
    """
    text = html or ""
    found: List[str] = []
    seen: Set[str] = set()

    def _add(u: str) -> None:
        u = _normalize_url(urljoin(base_url or "", (u or "").strip()))
        if not u.lower().startswith(("http://", "https://")):
            return
        low = u.lower()
        # Skip clear chrome
        if any(b in low for b in ("/offices/", "button_", "spacer", "logo", "1x1")):
            return
        if u not in seen:
            seen.add(u)
            found.append(u)

    # Absolute CDN mugshots
    for m in re.findall(
        r"https?://[^\s\"'<>]+watchsystems\.com/[^\s\"'<>]+", text, flags=re.I
    ):
        if "/pictures/" in m.lower() or "/picture/" in m.lower():
            _add(m.rstrip(".,);'\"\\"))
    # img src= (may be protocol-relative)
    for src in re.findall(
        r"<img[^>]+(?:src|data-src)\s*=\s*[\"']([^\"']+)[\"']", text, flags=re.I
    ):
        low = src.lower()
        if "watchsystems.com" in low and "/pictures/" in low:
            _add(src)
        elif "callimage" in low or "displayimage" in low or "/sorimage/" in low:
            _add(src)
    # Prefer /pictures/ first
    found.sort(
        key=lambda u: (
            0 if "/pictures/" in u.lower() else 1,
            0 if "watchsystems" in u.lower() else 1,
            u,
        )
    )
    return found

