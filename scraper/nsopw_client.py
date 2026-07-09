"""
NSOPW (Dru Sjodin National Sex Offender Public Website) search client.

Uses the same HTTPS JSON endpoint the official nsopw.gov SPA calls after
the user accepts Conditions of Use. The API accepts a same-day token header
(MM/DD/YYYY), first + last name, and a jurisdiction list.

The API sits behind Cloudflare. Plain Python TLS fingerprints and bursty
traffic often get 403/429 challenge pages ("Just a moment..."). This client:
  - prefers curl_cffi Chrome impersonation when installed
  - sends browser-like Origin/Referer headers
  - warms the session against nsopw.gov
  - retries Cloudflare blocks with backoff

Polite rate limiting is enforced. Respect NSOPW Conditions of Use.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from .config import DEFAULT_DELAY, REQUEST_TIMEOUT

NSOPW_SEARCH_URL = "https://nsopw-api.ojp.gov/nsopw/v1/v1.0/search"
NSOPW_OFFLINE_URL = "https://nsopw-api.ojp.gov/nsopw/v1/v1.0/jurisdictions/offline"
NSOPW_SEARCH_PAGE = "https://www.nsopw.gov/search-public-sex-offender-registries"
NSOPW_ORIGIN = "https://www.nsopw.gov"

# Match a current desktop Chrome — bot scoring is less harsh than custom UAs.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Cloudflare cool-down after a challenge (seconds), per attempt after the first.
_CF_BACKOFF_SECONDS = (0, 12, 28, 50)

# Core state/territory codes accepted by NSOPW (excludes "All")
DEFAULT_JURISDICTIONS = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY", "GU", "PR", "USVI", "AMERICANSAMOA", "CNMI",
]


@dataclass
class NSOPWOffender:
    """One hit from an NSOPW search."""

    first_name: str = ""
    middle_name: str = ""
    last_name: str = ""
    full_name: str = ""
    gender: str = ""
    date_of_birth: str = ""
    age: Optional[int] = None
    state: str = ""
    city: str = ""
    address: str = ""
    zip_code: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    jurisdiction_id: str = ""
    offender_uri: str = ""
    image_uri: str = ""
    absconder: bool = False
    aliases: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> Dict[str, Any]:
        """Map to database/offender dict."""
        import json

        return {
            "first_name": self.first_name or None,
            "last_name": self.last_name or None,
            "full_name": self.full_name or None,
            "gender": self.gender or None,
            "date_of_birth": self.date_of_birth or None,
            "age": self.age,
            "state": self.state or self.jurisdiction_id or None,
            "city": self.city or None,
            "address": self.address or None,
            "zip_code": self.zip_code or None,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "source_state": self.jurisdiction_id or "US",
            "source_url": self.offender_uri or None,
            "external_id": self.offender_uri or None,
            "raw_data_json": json.dumps(self.raw, ensure_ascii=False)[:50000],
            "flags": "nsopw",
        }


def _make_http_session() -> Tuple[Any, str]:
    """
    Prefer curl_cffi Chrome impersonation (Cloudflare-friendly TLS).
    Fall back to requests with a browser User-Agent.
    """
    try:
        from curl_cffi import requests as creq  # type: ignore

        session = creq.Session(impersonate="chrome")
        backend = "curl_cffi"
    except Exception:
        session = requests.Session()
        session.headers["User-Agent"] = BROWSER_UA
        backend = "requests"

    session.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": NSOPW_ORIGIN,
            "Referer": NSOPW_SEARCH_PAGE,
            "Content-Type": "application/json;charset=UTF-8",
        }
    )
    return session, backend


def _is_cloudflare_block(resp: Any) -> bool:
    """True when the response is a CF challenge / rate-limit page, not API JSON."""
    code = getattr(resp, "status_code", 0) or 0
    if code not in (403, 429, 503):
        return False
    ct = (getattr(resp, "headers", {}) or {}).get("Content-Type") or ""
    if "application/json" in ct.lower():
        return False
    body = (getattr(resp, "text", None) or "")[:1200].lower()
    markers = (
        "just a moment",
        "cloudflare",
        "attention required",
        "cf-ray",
        "enable javascript",
        "checking your browser",
    )
    if any(m in body for m in markers):
        return True
    # 429 from this host is almost always edge rate limiting
    return code == 429


class NSOPWClient:
    """Thin client for NSOPW name search."""

    def __init__(self, delay: float = DEFAULT_DELAY, timeout: float = REQUEST_TIMEOUT):
        self.delay = max(0.0, float(delay))
        self.timeout = timeout
        self.session, self.http_backend = _make_http_session()
        self._warmed = False

    def _token(self) -> str:
        """Same-day validation token (MM/DD/YYYY), as used by NSOPW clients."""
        return datetime.now().strftime("%m/%d/%Y")

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def _ensure_warm(self) -> None:
        """Hit the public search page once so Origin/Referer look organic."""
        if self._warmed:
            return
        try:
            self.session.get(
                NSOPW_SEARCH_PAGE,
                timeout=min(30.0, float(self.timeout)),
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
        except Exception:
            pass
        self._warmed = True

    def search_by_name(
        self,
        first_name: str,
        last_name: str,
        jurisdictions: Optional[Sequence[str]] = None,
    ) -> List[NSOPWOffender]:
        """
        Search NSOPW by first + last name.

        Both first and last name are required. The API accepts short *partial*
        prefixes; combined length must be at least 3 (e.g. first="M", last="AH").
        """
        first = (first_name or "").strip()
        last = (last_name or "").strip()
        if not first or not last:
            raise ValueError("NSOPW requires both first_name and last_name")
        if len(first) + len(last) < 3:
            raise ValueError("Combined first+last name must be at least 3 characters")

        jurs = list(jurisdictions) if jurisdictions else list(DEFAULT_JURISDICTIONS)
        # API rejects the literal "All" mixed into arrays in some cases — filter it
        jurs = [j for j in jurs if j and j.upper() != "ALL"]

        body = {
            "firstName": first,
            "lastName": last,
            "city": None,
            "county": None,
            "zips": None,
            "longitude": None,
            "latitude": None,
            "distance": None,
            "jurisdictions": jurs,
            "clientIp": "",
        }

        headers = {"token": self._token()}
        max_attempts = len(_CF_BACKOFF_SECONDS)
        resp: Any = None
        last_cf = False

        try:
            for attempt in range(max_attempts):
                self._ensure_warm()
                try:
                    resp = self.session.post(
                        NSOPW_SEARCH_URL,
                        json=body,
                        headers=headers,
                        timeout=self.timeout,
                    )
                except Exception as e:
                    if attempt + 1 >= max_attempts:
                        raise RuntimeError(f"NSOPW network error: {e}") from e
                    time.sleep(_CF_BACKOFF_SECONDS[min(attempt + 1, max_attempts - 1)])
                    continue

                if _is_cloudflare_block(resp):
                    last_cf = True
                    wait = _CF_BACKOFF_SECONDS[min(attempt + 1, max_attempts - 1)]
                    if attempt + 1 >= max_attempts:
                        break
                    # New TLS session sometimes clears a sticky challenge
                    if attempt >= 1:
                        try:
                            self.session.close()
                        except Exception:
                            pass
                        self.session, self.http_backend = _make_http_session()
                        self._warmed = False
                    time.sleep(wait)
                    continue

                last_cf = False
                break
        finally:
            # Polite spacing between completed search attempts (success or hard fail)
            if self.delay > 0:
                time.sleep(self.delay)

        if resp is None:
            raise RuntimeError("NSOPW search failed: no response")

        if last_cf or _is_cloudflare_block(resp):
            raise RuntimeError(
                f"NSOPW blocked by Cloudflare (HTTP {resp.status_code}). "
                "Wait a minute, increase Search delay to 3–5s, and retry. "
                f"HTTP backend={self.http_backend}. "
                "If this persists, install curl_cffi: pip install curl_cffi"
            )

        if resp.status_code == 422:
            # Structured validation errors
            try:
                err = resp.json()
                code = err.get("statusCode")
                raise RuntimeError(
                    f"NSOPW rejected query (statusCode={code}): {resp.text[:300]}"
                )
            except ValueError:
                resp.raise_for_status()

        if resp.status_code >= 400:
            preview = (resp.text or "")[:200].replace("\n", " ")
            raise RuntimeError(
                f"NSOPW search failed: HTTP {resp.status_code} for url: "
                f"{NSOPW_SEARCH_URL} — {preview}"
            )

        try:
            data = resp.json()
        except ValueError as e:
            raise RuntimeError(
                f"NSOPW returned non-JSON (HTTP {resp.status_code}): "
                f"{(resp.text or '')[:200]!r}"
            ) from e

        raw_offenders = data.get("offenders") or []
        return [self._parse_offender(o) for o in raw_offenders if isinstance(o, dict)]

    def _parse_offender(self, obj: Dict[str, Any]) -> NSOPWOffender:
        name = obj.get("name") or {}
        given = (name.get("givenName") or "").strip()
        middle = (name.get("middleName") or "").strip()
        sur = (name.get("surName") or "").strip()
        parts = [p for p in (given, middle, sur) if p]
        full = " ".join(parts)

        aliases: List[str] = []
        for a in obj.get("aliases") or []:
            if not isinstance(a, dict):
                continue
            ap = [p for p in (a.get("givenName"), a.get("middleName"), a.get("surName")) if p]
            if ap:
                aliases.append(" ".join(str(x) for x in ap))

        # Prefer residential location
        loc = {}
        for candidate in obj.get("locations") or []:
            if isinstance(candidate, dict):
                loc = candidate
                if (candidate.get("type") or "").upper() == "R":
                    break

        dob = obj.get("dob") or ""
        if isinstance(dob, str) and "T" in dob:
            dob = dob.split("T", 1)[0]

        age = obj.get("age")
        try:
            age = int(age) if age is not None else None
        except (TypeError, ValueError):
            age = None

        lat = loc.get("latitude")
        lon = loc.get("longitude")
        try:
            lat = float(lat) if lat is not None else None
            lon = float(lon) if lon is not None else None
            if lat == 0 and lon == 0:
                lat = lon = None
        except (TypeError, ValueError):
            lat = lon = None

        return NSOPWOffender(
            first_name=given,
            middle_name=middle,
            last_name=sur,
            full_name=full,
            gender=(obj.get("gender") or "").strip(),
            date_of_birth=dob,
            age=age,
            state=(loc.get("state") or obj.get("jurisdictionId") or "").strip(),
            city=(loc.get("city") or "").strip(),
            address=(loc.get("streetAddress") or "").strip(),
            zip_code=str(loc.get("zipCode") or "").strip(),
            latitude=lat,
            longitude=lon,
            jurisdiction_id=(obj.get("jurisdictionId") or "").strip(),
            offender_uri=unescape((obj.get("offenderUri") or "").strip()),
            image_uri=unescape((obj.get("imageUri") or "").strip()),
            absconder=bool(obj.get("absconder")),
            aliases=aliases,
            raw=obj,
        )
