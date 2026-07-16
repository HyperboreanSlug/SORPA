"""Offense/crime extraction from report HTML tables."""
from __future__ import annotations

import re
from typing import List, Set

from bs4 import BeautifulSoup

from scraper.reports.util import (
    _LABEL_MAP,
    _MAX_CRIME_LEN,
    _clean_value,
    _normalize_label,
)

# Columns that hold offense / charge / statute text
_CRIME_HEADER_KEYS: Set[str] = {
    "offense",
    "offenses",
    "offense description",
    "offense type",
    "charge",
    "charges",
    "crime",
    "crimes",
    "statute",
    "qualifying offense",
    "registerable offense",
    "registrable offense",
    "description",
    "violation",
    "chapter/section",  # MA SORB statute column
}

# MA SORB puts the offense title under "Jurisdiction" when chapter/section is present
_MA_OFFENSE_NAME_KEYS: Set[str] = {"jurisdiction"}

# Demographic labels that must never become crime text
_DEMO_LABEL_RE = re.compile(
    r"(?ix)^(?:"
    r"photo\s*date|name|level|year\s*of\s*birth|age|sex|gender|race|"
    r"height|weight|eye\s*color|hair\s*color|aliases?|address|type|row|"
    r"please\s+click|photos\s+are\s+labeled"
    r")\s*:?\s*$"
)

_DEMO_JUNK_RE = re.compile(
    r"(?i)\b(?:photo\s*date|year\s*of\s*birth)\b"
)


def is_demographic_crime_junk(text: str) -> bool:
    """True when *text* is mis-parsed demographics, not an offense."""
    s = " ".join((text or "").split()).strip()
    if not s:
        return True
    if _DEMO_JUNK_RE.search(s):
        return True
    # Many MA misparses are "Label:; value; Label:; value"
    if s.count(":;") >= 2 or (s.count(";") >= 3 and "level" in s.lower()):
        return True
    # "LAST, FIRST; 1981; MALE; 5'8\"; Brown; ALIAS…" — demographics dump
    if re.search(r"(?i)\b(?:male|female)\b", s) and re.search(
        r"\b(?:19|20)\d{2}\b", s
    ):
        if re.search(r"(?i)\b(?:brown|black|blue|hazel|green|blond)\b", s) or re.search(
            r"\d'\d", s
        ):
            return True
    # Surname, Given with no offense verbs and multiple semicolons
    if re.match(r"^[A-Z][A-Z' \-]+,\s*[A-Z]", s) and s.count(";") >= 2:
        if not re.search(
            r"(?i)\b(?:rape|assault|battery|molest|abuse|sodomy|indecent|"
            r"porn|sex|lewd|kidnap|fail(?:ure)?\s+to\s+regist)\b",
            s,
        ):
            return True
    return False


def _is_crime_cell(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) < 3:
        return False
    lab = _normalize_label(t)
    if lab in _LABEL_MAP or lab in _CRIME_HEADER_KEYS:
        return False
    if _DEMO_LABEL_RE.match(t) or _DEMO_LABEL_RE.match(lab):
        return False
    if lab in ("live", "work", "school", "temporary", "registered"):
        return False
    # Pure row numbers / dates alone are not offenses
    if re.fullmatch(r"\d{1,3}", t):
        return False
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", t):
        return False
    return True


def _table_caption(table) -> str:
    cap = table.find("caption")
    if not cap:
        return ""
    return _normalize_label(cap.get_text(" ", strip=True))


def _header_cells(row) -> List[str]:
    cells = row.find_all("th")
    if len(cells) < 2:
        cells = row.find_all(["th", "td"])
    return [_normalize_label(c.get_text(" ", strip=True)) for c in cells]


def extract_crime_from_tables(soup: BeautifulSoup) -> str:
    """Pull offense/charge text from multi-row offense tables."""
    collected: List[str] = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        headers = _header_cells(rows[0])
        # Nested outer wrappers produce huge first rows — never scrape those
        if len(headers) > 10:
            continue
        if any(len(h) > 80 for h in headers):
            continue

        cap = _table_caption(table)
        is_offense_caption = any(
            k in cap for k in ("offense", "charge", "crime", "conviction")
        )

        # MA SORB: Offense(s) table uses Jurisdiction | Chapter/Section | Date | Count
        ma_style = (
            "jurisdiction" in headers
            and any(
                h in headers
                for h in ("chapter/section", "conviction/adjudication date", "no. of convictions")
            )
        ) or (
            is_offense_caption
            and "jurisdiction" in headers
            and "address" not in headers
        )

        idxs = [i for i, h in enumerate(headers) if h in _CRIME_HEADER_KEYS]
        if ma_style:
            for i, h in enumerate(headers):
                if h in _MA_OFFENSE_NAME_KEYS and i not in idxs:
                    idxs.insert(0, i)

        if not idxs:
            head_blob = " ".join(headers)
            if not is_offense_caption and not any(
                k in head_blob for k in ("offense", "charge", "crime", "statute")
            ):
                continue
            # Narrow fallback only: short header rows, known offense tables
            if len(headers) > 6:
                continue
            for data_row in rows[1:]:
                tds = data_row.find_all("td")
                parts = [
                    _clean_value(td.get_text(" ", strip=True))
                    for td in tds
                    if _is_crime_cell(_clean_value(td.get_text(" ", strip=True)))
                ]
                # Prefer longer phrase cells (offense titles over codes alone)
                parts = [p for p in parts if len(p) >= 5]
                if parts:
                    collected.append(" — ".join(parts[:3]))
            continue

        for data_row in rows[1:]:
            # Skip pure header rows
            if data_row.find_all("th") and not data_row.find_all("td"):
                continue
            tds = data_row.find_all(["td", "th"])
            parts: List[str] = []
            for i in idxs:
                if i >= len(tds):
                    continue
                t = _clean_value(tds[i].get_text(" ", strip=True))
                if _is_crime_cell(t):
                    parts.append(t)
            if parts:
                collected.append(" — ".join(parts))

    # Prefer real offense phrases; drop demographic junk fragments
    seen = set()
    uniq: List[str] = []
    for c in collected:
        if is_demographic_crime_junk(c):
            continue
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
        if len(uniq) >= 8:
            break
    return "; ".join(uniq)[:_MAX_CRIME_LEN]
