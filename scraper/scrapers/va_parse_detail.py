"""Parse vspsor.com Offender/Details HTML and merge into list records."""
from __future__ import annotations

import re
from typing import Any, Dict
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scraper.reports.fetcher_crime_va import extract_va_card_offenses
from scraper.reports.util import _clean_value

BASE = "https://www.vspsor.com"
_WS = re.compile(r"\s+")

_LABEL_MAP = {
    "registration number": "registration_number",
    "reg number": "registration_number",
    "status": "status",
    "age": "age",
    "tier": "risk_level",
    "sex": "gender",
    "gender": "gender",
    "race": "race",
    "hair": "hair_color",
    "hair color": "hair_color",
    "eyes": "eye_color",
    "eye color": "eye_color",
    "height": "height",
    "weight": "weight",
    "initial registration start date": "registration_date",
    "initial registration": "registration_date",
    "reg. renewed": "last_verified",
    "reg renewed": "last_verified",
    "date of birth": "date_of_birth",
    "dob": "date_of_birth",
    "birth date": "date_of_birth",
}


def _norm_name(s: str) -> str:
    return _WS.sub(" ", (s or "").casefold().strip())


def names_compatible(list_full: str, detail_full: str) -> bool:
    """Identity gate: refuse detail demos when names clearly disagree.

    Deterministic surname + first-name check. A shared common surname alone
    (JOHN DOE vs JANE DOE) must NOT pass — that merges another person's
    race/DOB/photo.
    """
    a = _norm_name(list_full)
    b = _norm_name(detail_full)
    if not a or not b or a == b:
        return True
    from scraper.database.identity import first_names_compatible, last_names_compatible
    from scraper.reports.identity_gate import split_html_display_name

    af, _am, al = split_html_display_name(list_full)
    bf, _bm, bl = split_html_display_name(detail_full)
    if not al or not bl:
        return True
    if not last_names_compatible(al, bl):
        return False
    if af and bf:
        return first_names_compatible(af, bf)
    return True


def parse_detail_html(html: str, *, base_url: str = BASE) -> Dict[str, Any]:
    """Extract demographics + crime from an Offender/Details page."""
    if not html:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    found: Dict[str, Any] = {}

    for sel in ("h1", "#offender-details h1", ".offender-name", "title"):
        node = soup.select_one(sel)
        if not node:
            continue
        text = _clean_value(node.get_text(" ", strip=True)) or ""
        text = re.sub(r"\s*[-|].*$", "", text).strip()
        text = re.sub(r"(?i)^offender details\s*", "", text).strip()
        if text and len(text) > 2 and "virginia" not in text.casefold():
            found["full_name"] = text
            break

    for el in soup.find_all(string=True):
        raw = (el if isinstance(el, str) else str(el) or "").strip()
        if not raw or len(raw) > 80 or ":" not in raw:
            continue
        m = re.match(r"^([^:]{2,40}):\s*(.+)$", raw)
        if not m:
            continue
        key = _LABEL_MAP.get(m.group(1).strip().casefold())
        val = _clean_value(m.group(2))
        if key and val and key not in found:
            found[key] = val

    for lab_el in soup.find_all(["div", "span", "dt", "th", "label", "strong"]):
        lab_raw = lab_el.get_text(" ", strip=True)
        if not lab_raw or len(lab_raw) > 48:
            continue
        lab_key = lab_raw.rstrip(":").strip().casefold()
        field = _LABEL_MAP.get(lab_key)
        if not field or field in found:
            continue
        val = ""
        sib = lab_el.find_next_sibling()
        if sib is not None:
            val = _clean_value(sib.get_text(" ", strip=True)) or ""
        if not val and lab_el.parent is not None:
            for k in lab_el.parent.find_all(["div", "span"], recursive=False):
                if k is lab_el:
                    continue
                t = _clean_value(k.get_text(" ", strip=True)) or ""
                if t and t.casefold() != lab_key and not t.endswith(":"):
                    val = t
                    break
        if val and len(val) < 120 and val.casefold() != lab_key:
            found[field] = val

    crime = extract_va_card_offenses(soup)
    if crime:
        found["crime"] = crime

    for img in soup.find_all("img"):
        alt = (img.get("alt") or "").casefold()
        src = (img.get("src") or "").strip()
        if not src:
            continue
        if "offender" in alt or "photo" in alt or "/api/file/image/" in src:
            if not src.startswith("http"):
                src = urljoin((base_url or BASE).rstrip("/") + "/", src)
            if "/api/file/image/" in src or src.startswith("http"):
                found.setdefault("photo_url", src)
                break

    if found.get("risk_level"):
        found["risk_level"] = _clean_value(str(found["risk_level"]))
    return {k: v for k, v in found.items() if v not in (None, "")}


def merge_detail_into_record(
    record: Dict[str, Any],
    detail: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge detail fields when identity is compatible; never invent IDs."""
    out = dict(record)
    if not detail:
        return out
    if not names_compatible(
        str(out.get("full_name") or ""), str(detail.get("full_name") or "")
    ):
        flags = str(out.get("flags") or "")
        if "identity_html_mismatch" not in flags:
            out["flags"] = (flags + " identity_html_mismatch").strip()
        return out

    reg_no = detail.get("registration_number")
    if reg_no:
        raw = out.get("raw_data_json")
        raw = dict(raw) if isinstance(raw, dict) else {}
        raw["registration_number"] = reg_no
        out["raw_data_json"] = raw

    for key in (
        "race",
        "gender",
        "height",
        "weight",
        "eye_color",
        "hair_color",
        "risk_level",
        "crime",
        "registration_date",
        "last_verified",
        "date_of_birth",
        "age",
        "photo_url",
    ):
        val = detail.get(key)
        if val not in (None, "") and out.get(key) in (None, ""):
            out[key] = val
    return out
