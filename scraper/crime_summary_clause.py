"""Parse one semicolon-separated offense clause into a short label."""
from __future__ import annotations

import re
from typing import List, Optional

from scraper.crime_summary_junk import (
    is_junk_label,
    is_statute_or_docket,
    strip_statute_cites,
)
from scraper.crime_summary_maps import CODE_MAP, DROP_CLAUSE, OFFENSE_MAP

# Date tokens only — never Colorado CRS-style statutes like 18-3-402
# (old pattern \d{1,2}-\d{1,2}-\d{2,4} ate "18-3-402" → left "(1)(b) — SEX ASSAULT…").
_DATE = re.compile(
    r"(?ix)\b(?:"
    # Slash dates: 12/09/2003 or 12/09/03
    r"\d{1,2}/\d{1,2}/\d{2,4}"
    # Dot dates: 12.09.2003
    r"|\d{1,2}\.\d{1,2}\.\d{2,4}"
    # Hyphen dates require a real year (19xx/20xx), not statute middle segments
    r"|\d{1,2}-\d{1,2}-(?:19|20)\d{2}"
    r"|\d{4}-\d{1,2}-\d{1,2}"
    r"|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{2,4}"
    r")\b"
)

_COURT_JUNK = re.compile(
    r"(?ix)\b(?:"
    r"(?:county|judicial\s+district|superior\s+court|circuit\s+court|"
    r"district\s+court|dept\.?\s+of\s+corrections|ndoc|doc)\b.*"
    r")$"
)

# Whole clause is a court / institution / agency (drop entirely)
_COURT_OR_AGENCY_CLAUSE = re.compile(
    r"(?ix)^(?:"
    r".*\b(?:superior\s+court|circuit\s+court|district\s+court|"
    r"judicial\s+district|municipal\s+court|county\s+court)\b.*"
    r"|dept\.?\s+of\s+corrections.*"
    r"|department\s+of\s+corrections.*"
    r"|out\s+of\s+state.*"
    r"|california\s+department.*"
    r")$"
)

# Person-name-only clauses (NV dumps put conviction name next to the charge)
_PERSON_NAME_CLAUSE = re.compile(
    r"(?ix)^[A-Z][A-Z' \-.]{1,40}(?:\s+[A-Z][A-Z' \-.]{0,30}){0,4}$"
)

_LOCATION_TRAIL = re.compile(
    r"(?ix)"
    r"(?:"
    r",?\s*(?:in\s+)?(?:the\s+)?(?:city|county|state)\s+of\s+.+$"
    r"|,?\s*(?:resides?|residence|address|located)\s*(?:at|in|:)\s*.+$"
    r"|,?\s*\d{1,6}\s+[A-Za-z0-9.\- ]{2,40}\s+"
    r"(?:st|street|ave|avenue|blvd|rd|road|dr|drive|ln|lane|ct|court|way|hwy)\b.+$"
    r")$"
)
CITY_STATE = re.compile(
    r"(?ix)\b[A-Za-z][A-Za-z.\-']+(?:\s+[A-Za-z][A-Za-z.\-']+){0,3}"
    r",\s*[A-Z]{2}\b(?:\s+\d{5}(?:-\d{4})?)?"
)
COUNTY_LOC = re.compile(
    r"(?ix)\b[A-Za-z][A-Za-z.\-']+(?:\s+[A-Za-z][A-Za-z.\-']+)?\s+"
    r"County(?:,\s*[A-Z]{2})?\b"
)

_MONTHS = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)"
)


def norm(s: str) -> str:
    return " ".join((s or "").split()).strip(" ;,|")


def strip_dates(s: str) -> str:
    t = _DATE.sub(" ", s)
    t = re.sub(rf"(?i)\b{_MONTHS}\.?\s+\d{{1,2}},?\s+\d{{2,4}}\b", " ", t)
    t = re.sub(r"\(\s*(?:19|20)\d{2}\s*\)", " ", t)
    t = re.sub(r"\b(?:19|20)\d{2}\b", " ", t)
    return norm(t)


def strip_location_junk(s: str) -> str:
    t = s or ""
    t = _LOCATION_TRAIL.sub(" ", t)
    t = CITY_STATE.sub(" ", t)
    t = COUNTY_LOC.sub(" ", t)
    t = re.sub(r"(?i)\b(?:guilty/?convict(?:ed)?|adjudication\s+withheld)\b", " ", t)
    t = re.sub(r"(?i)\b(?:at\s+)?(?:last\s+)?known\s+(?:address|location)\b.*$", " ", t)
    t = re.sub(r"\s*[|;]\s*", " · ", t)
    t = re.sub(r"(?:\s*·\s*)+", " · ", t)
    return norm(t.strip(" ·;,"))


def to_regular_case(s: str) -> str:
    """Force readable regular case (never ALL CAPS registry dumps).

    - Mostly-uppercase text → Title Case
    - Already mixed → ensure first letter capital, leave the rest
    - Multi-part labels split on · / — keep each segment regular
    """
    raw = (s or "").strip()
    if not raw:
        return raw

    def _one(part: str) -> str:
        t = part.strip(" ·;,|-")
        if not t:
            return t
        letters = [c for c in t if c.isalpha()]
        if not letters:
            return t
        upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        # Registry ALL CAPS (or nearly) — never leave as SCREAMING
        if upper_ratio >= 0.75:
            t = t.title()
            # Soft small words after title() mid-phrase
            t = re.sub(
                r"\b(Of|Or|And|The|A|An|To|In|For|On|By|With)\b",
                lambda m: m.group(1).lower(),
                t,
            )
            # Re-capitalize first char / after em dash
            t = re.sub(
                r"(^|[—\-·]\s*)([a-z])",
                lambda m: m.group(1) + m.group(2).upper(),
                t,
            )
            return t
        # Mixed/lower: sentence-style first capital only
        if t[0].islower():
            return t[0].upper() + t[1:]
        return t

    # Keep multi-offense separators intact
    if " · " in raw:
        return " · ".join(_one(p) for p in raw.split(" · "))
    return _one(raw)


def title_offense(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    low = s.lower()
    if low.startswith("sexual battery"):
        rest = s[len("sexual battery") :].strip(" -:")
        base = ("Sexual battery " + rest).strip() if rest else "Sexual battery"
        return to_regular_case(base)
    return to_regular_case(s)


def summarize_lewd_clause(clause: str) -> Optional[str]:
    """Compress FL/CA lewd/lascivious boilerplate to short card labels.

    Never includes the words lewd/lascivious — only victim age and acts.

    Examples::

        … victim is under 12 or … force or coercion
          → Victim under 12/force
        … molestation involving unclothed genitals
          → Unclothed genitals
        288 (A) LEWD OR LASCIVIOUS ACTS W/ CHILD UNDER 14
          → Victim under 14
    """
    low = (clause or "").lower()
    under12 = bool(
        re.search(
            r"under\s*12|victim\s+is\s+under\s*12|u/?12|< ?12|vctm\s*under\s*12",
            low,
        )
    )
    # CA PC 288(a) and similar: child under 14 / under fourteen
    under14 = bool(
        re.search(
            r"under\s*14|less\s+than\s*14|u/?14|< ?14|vctm\s*under\s*14|"
            r"child\s+under\s*14|w/?\s*child\s+under\s*14",
            low,
        )
    )
    under16 = bool(
        re.search(
            r"less\s+than\s*16|under\s*16|12-15|u/?16|< ?16|vctm\s*<?\s*16",
            low,
        )
    )
    force = bool(re.search(r"force|coercion", low))
    unclothed = bool(re.search(r"unclothed\s+genitals?", low))
    molest = bool(re.search(r"molest", low))
    exhibition = bool(re.search(r"exhibition", low))
    with_child = bool(re.search(r"\b(?:child|minor|w/?\s*child)\b", low))

    if under12 and force:
        return "Victim under 12/force"
    if under12:
        return "Victim under 12"
    if under14:
        return "Victim under 14"
    if unclothed:
        return "Unclothed genitals"
    if molest and under16:
        return "Molestation — victim under 16"
    if molest:
        return "Molestation"
    if under16:
        return "Victim under 16"
    if exhibition:
        return "Indecent exhibition"
    if force:
        return "Force or coercion"
    # Bare CA-style "lewd … with child" without a parsed age
    if with_child:
        return "Acts with a child"
    # No useful qualifier — omit rather than print lewd/lascivious
    return None


def extract_from_clause(clause: str) -> Optional[str]:
    c = norm(clause)
    if not c or len(c) < 3 or DROP_CLAUSE.match(c) or is_statute_or_docket(c):
        return None
    # NV multi-column dumps: court / agency / person-name only
    if _COURT_OR_AGENCY_CLAUSE.match(c):
        return None
    if _PERSON_NAME_CLAUSE.match(c) and not re.search(
        r"(?i)\b(?:rape|assault|battery|molest|abuse|sodomy|indecent|"
        r"porn|sex|lewd|kidnap|fail(?:ure)?\s+to\s+regist|offense|"
        r"child|minor|conduct)\b",
        c,
    ):
        return None

    c = strip_dates(c)
    c = strip_statute_cites(c)
    if not c or DROP_CLAUSE.match(c) or is_statute_or_docket(c) or is_junk_label(c):
        return None
    if _COURT_OR_AGENCY_CLAUSE.match(c):
        return None

    src = clause + " " + c
    for rx, label in CODE_MAP:
        if rx.search(src):
            return label
    m = re.search(r"CHILD\s+MOLESTATION[- ]?(\d)", src, re.I)
    if m:
        return f"Child molestation {m.group(1)}"

    m = re.search(r"—\s*(.+)$", c)
    if m and len(m.group(1)) > 8:
        c = norm(m.group(1))
    m = re.search(r"§\s*[\d\-.]+\s*[—-]\s*(.+)$", c)
    if m and len(m.group(1)) > 8:
        c = norm(m.group(1))

    if is_statute_or_docket(c) or is_junk_label(c):
        return None

    if re.search(r"sexual\s+battery", c, re.I):
        extra = []
        if re.search(r"weapon|force|wpn", c, re.I):
            extra.append("weapon/force")
        if re.search(r"under\s*12", c, re.I):
            extra.append("victim under 12")
        if re.search(r"injury\s+not\s+likely", c, re.I):
            extra.append("injury not likely")
        base = "Sexual battery"
        return f"{base} — {', '.join(extra)}" if extra else base

    # Long-form lewd/lascivious (keep victim age / molestation qualifiers)
    if re.search(r"\blewd\b|\blascivious\b", src, re.I) or re.search(
        r"\blewd\b|\blascivious\b", c, re.I
    ):
        return summarize_lewd_clause(src)

    for pat, lab in OFFENSE_MAP:
        if re.search(pat, c, re.I) or re.search(pat, src, re.I):
            if "porn" in lab.lower():
                m_c = re.search(r"\(\s*(\d+)\s*counts?\s*\)", src, re.I)
                if m_c:
                    return f"{lab} — {m_c.group(1)} counts"
            # Promote to "Attempted …" when source has ATTEMPT and label doesn't
            if re.search(r"(?i)\battempt", src) and not re.search(
                r"(?i)\battempt", lab
            ):
                if lab.lower().startswith("sexual assault"):
                    lab = "Attempted " + lab[0].lower() + lab[1:]
                elif not lab.lower().startswith("attempt"):
                    lab = "Attempted " + lab[0].lower() + lab[1:]
            return lab

    c2 = _COURT_JUNK.sub("", c).strip(" ;,")
    c2 = strip_statute_cites(strip_location_junk(c2))
    if not c2 or len(c2) < 4:
        return None
    if DROP_CLAUSE.match(c2) or is_statute_or_docket(c2) or is_junk_label(c2):
        return None
    if _COURT_OR_AGENCY_CLAUSE.match(c2) or _PERSON_NAME_CLAUSE.match(c2):
        return None
    if len(c2) > 90:
        c2 = re.split(r"\s+where\s+|\s+by\s+offender\s+", c2, maxsplit=1)[0]
        c2 = strip_location_junk(norm(c2))
        if len(c2) > 80:
            return None
    # Title Case or ALL CAPS multi-token names without offense verbs
    if re.match(
        r"^(?:[A-Z][a-z]+|[A-Z]+)(?:\s+(?:[A-Z][a-z]+|[A-Z]+|[A-Z]\.?)){1,4}$",
        c2,
    ) and not re.search(
        r"(?i)sexual|lewd|rape|child|assault|battery|molest|offense|conduct",
        c2,
    ):
        return None
    if CITY_STATE.fullmatch(c2) or COUNTY_LOC.fullmatch(c2) or is_junk_label(c2):
        return None
    return title_offense(c2)
