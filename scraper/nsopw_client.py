"""
NSOPW (Dru Sjodin National Sex Offender Public Website) search client.

Uses the same HTTPS JSON endpoint the official nsopw.gov SPA calls after
the user accepts Conditions of Use. The site requires a same-day validation
token header (MM/DD/YYYY), first + last name, and jurisdiction list.

Polite rate limiting is enforced. Respect NSOPW Conditions of Use.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from typing import Any, Dict, List, Optional, Sequence

import requests

from .config import USER_AGENT, DEFAULT_DELAY, REQUEST_TIMEOUT

NSOPW_SEARCH_URL = "https://nsopw-api.ojp.gov/nsopw/v1/v1.0/search"
NSOPW_OFFLINE_URL = "https://nsopw-api.ojp.gov/nsopw/v1/v1.0/jurisdictions/offline"
NSOPW_SEARCH_PAGE = "https://www.nsopw.gov/search-public-sex-offender-registries"

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


class NSOPWClient:
    """Thin client for NSOPW name search."""

    def __init__(self, delay: float = DEFAULT_DELAY, timeout: float = REQUEST_TIMEOUT):
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.nsopw.gov",
                "Referer": NSOPW_SEARCH_PAGE,
                "Content-Type": "application/json;charset=UTF-8",
            }
        )

    def _token(self) -> str:
        """Same-day validation token expected by the API (Conditions of Use)."""
        return datetime.now().strftime("%m/%d/%Y")

    def close(self) -> None:
        self.session.close()

    def search_by_name(
        self,
        first_name: str,
        last_name: str,
        jurisdictions: Optional[Sequence[str]] = None,
    ) -> List[NSOPWOffender]:
        """
        Search NSOPW by first + last name.

        Both first and last name are required by the API (min combined length 3).
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
        try:
            resp = self.session.post(
                NSOPW_SEARCH_URL,
                json=body,
                headers=headers,
                timeout=self.timeout,
            )
        finally:
            time.sleep(self.delay)

        if resp.status_code == 422:
            # Structured validation errors
            try:
                err = resp.json()
                code = err.get("statusCode")
                raise RuntimeError(f"NSOPW rejected query (statusCode={code}): {resp.text[:300]}")
            except ValueError:
                resp.raise_for_status()

        resp.raise_for_status()
        data = resp.json()
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
