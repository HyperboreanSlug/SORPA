"""Reject scars/marks/tattoos (SMT) text mis-filed as crime; statute card parse."""
from __future__ import annotations

import re
from typing import List, Set

from bs4 import BeautifulSoup

from scraper.reports.util import _MAX_CRIME_LEN, _clean_value

_SMT_TYPE_RE = re.compile(
    r"(?i)^(?:tattoos?|scars?|marks?|brand(?:ing)?|piercings?|mole|birthmark|"
    r"deformit(?:y|ies)|missing|amputat)\b"
)
_OFFENSE_WORD_RE = re.compile(
    r"(?i)\b(?:rape|assault|battery|molest(?:ation|ing)?|abuse|sodomy|"
    r"indecent|indecency|porn(?:ograph(?:y|ic))?|sex(?:ual)?|"
    r"lewd|kidnap(?:ping)?|fail(?:ure)?\s+to\s+regist|csc|"
    r"criminal\s+sexual|exploitation|enticing|voyeur(?:ism)?|"
    r"exposure|incest|homicide|murder|solicitation|solicit)\b"
)
# MI SMT body catalog: "ARM, LEFT, LOWER" / "SHOULDER, RIGHT" / "HEAD, NONSPECIFIC"
_BODY_CATALOG_RE = re.compile(
    r"(?ix)\b(?:"
    r"arm|leg|neck|chest|back|face|abdomen|wrist|hand|foot|ankle|"
    r"shoulder|forearm|thigh|hip|head|ear|eye|finger|thumb|shin|"
    r"knee|elbow|palm|scalp|cheek|chin|forehead|bicep|tricep|calf|"
    r"torso|groin|rib(?:s)?"
    r")\s*,\s*"
    r"(?:left|right|upper|lower|center|nonspecific|middle|inner|outer|"
    r"side|top|bottom)"
    r"(?:\s*,\s*(?:left|right|upper|lower|center|nonspecific|middle|"
    r"inner|outer|side)){0,2}"
)
_INK_MARK_RE = re.compile(
    r"(?i)\b(?:tattoos?|tattooed|ink|scars?|brand(?:ing|ed)?|piercings?|"
    r"birthmarks?|moles?)\b"
)
_PURE_BODY_LOC_RE = re.compile(
    r"(?ix)^(?:"
    r"(?:arm|leg|neck|chest|back|face|abdomen|wrist|hand|foot|ankle|"
    r"shoulder|forearm|thigh|hip|head|ear|shin|knee|elbow|palm|"
    r"scalp|torso)"
    r"(?:\s*,\s*(?:left|right|upper|lower|center|nonspecific|middle|"
    r"inner|outer|side)){1,3}"
    r"(?:\s*;\s*)?"
    r")+$"
)


def is_smt_description_junk(text: str) -> bool:
    """True for tattoo/scar mark descriptions mis-filed as crime."""
    s = " ".join((text or "").split()).strip()
    if not s:
        return True
    # Strip section chrome: "Scars, Marks and Tattoos — Forcible Rape" → keep offense
    s_core = re.sub(
        r"(?i)^scars?,?\s*marks?\s*(?:and|&)?\s*tattoos?\s*[-–—:|/]*\s*",
        "",
        s,
    ).strip()
    # Real charges always win (even if SMT section label was glued on)
    if _OFFENSE_WORD_RE.search(s) or (s_core and _OFFENSE_WORD_RE.search(s_core)):
        return False
    # Bare type token only (TATTOOS / SCARS) — not a charge
    if _SMT_TYPE_RE.match(s) and len(s) < 48:
        return True
    seps = s.count("|") + s.count(";")
    if seps >= 2 and s.count(":") >= 2:
        return True
    if re.search(
        r"(?i)\b(?:left|right|upper|lower|center|chest|neck|arm|back|face|"
        r"abdomen|wrist|hand|leg|ankle|shoulder)\b.?:",
        s,
    ) and not re.search(r"\d{2,3}\.\d+", s):
        return True
    # Tattoo / scar / ink wording with no offense verbs
    if _INK_MARK_RE.search(s):
        return True
    # Body-location catalog (MI SMT Description column dumps)
    body_hits = _BODY_CATALOG_RE.findall(s)
    if body_hits:
        # "OUTLINED CROSS … BLACK INK; ARM, LEFT, LOWER" or multi-location lists
        if len(body_hits) >= 2:
            return True
        if re.search(
            r"(?i);\s*(?:arm|leg|neck|chest|back|face|abdomen|wrist|hand|"
            r"shoulder|forearm|thigh|head|ear|knee|elbow|palm|shin)\b",
            s,
        ):
            return True
        if re.search(
            r"(?i)\b(?:cross|skull|rose|dragon|tribal|eagle|heart|star|web|"
            r"clover|phoenix|demon|wings|banner|flowers?|praying\s+hands)\b",
            s,
        ):
            return True
    # Entire value is body location(s): "ARM, LEFT" / "ARM, LEFT; ARM, RIGHT"
    if _PURE_BODY_LOC_RE.match(s):
        return True
    # Short inch-scar fragments with no charge text
    if re.search(r"(?i)\b\d+(?:-\d+)?\s*(?:inch|in\.?|\"|'')\s+scar\b", s):
        return True
    return False


def is_smt_table(headers: List[str], rows) -> bool:
    """Scars/Marks/Tattoos grids use Type | Location | Description."""
    hset = {h for h in headers if h}
    if "type" in hset and "location" in hset and "description" in hset:
        return True
    type_i = next((i for i, h in enumerate(headers) if h == "type"), None)
    if type_i is None:
        return False
    smt_hits = 0
    checked = 0
    for data_row in rows[1:6]:
        tds = data_row.find_all("td")
        if type_i >= len(tds):
            continue
        val = _clean_value(tds[type_i].get_text(" ", strip=True)) or ""
        if not val:
            continue
        checked += 1
        if _SMT_TYPE_RE.match(val):
            smt_hits += 1
    return checked > 0 and smt_hits >= max(1, checked // 2)


def extract_statute_card_offenses(
    soup: BeautifulSoup,
    *,
    is_label_chrome_value,
    is_demographic_crime_junk,
    is_crime_cell,
) -> str:
    """MI/VA-style card headers: ``750.520C1A - CRIMINAL SEXUAL CONDUCT…``."""
    collected: List[str] = []
    seen: Set[str] = set()
    for node in soup.select(
        "#convictions .card-header, .card-header.gold, div.card-header, "
        ".card-header span, #offenses .card-header"
    ):
        raw = _clean_value(node.get_text(" ", strip=True))
        if not raw or len(raw) < 8:
            continue
        raw = re.sub(r"\s*[-–—]+\s*$", "", raw).strip()
        if is_label_chrome_value(raw) or is_demographic_crime_junk(raw):
            continue
        if not (
            re.search(r"\d{2,3}\.\d+", raw)
            or re.search(r"(?i)\b(?:mcl|f\.?s\.?|u\.?s\.?c\.?)\b", raw)
            or _OFFENSE_WORD_RE.search(raw)
        ):
            continue
        if not is_crime_cell(raw):
            continue
        key = raw.casefold()
        if key in seen:
            continue
        seen.add(key)
        collected.append(raw)
        if len(collected) >= 8:
            break
    return "; ".join(collected)[:_MAX_CRIME_LEN]
