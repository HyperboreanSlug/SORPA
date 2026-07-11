"""Summarize long SOR offense / crime strings for report cards.

Registry pages often dump multi-statute boilerplate (FL “Commission of OR
Attempt…”, chapter cites, F.S. sections, case numbers, dates). Reports show a
short human summary; Misclassify / detail drawers keep the full text.
"""
from __future__ import annotations

import re
from typing import List, Optional

# Drop entire clauses that are pure legal chrome
_DROP_CLAUSE = re.compile(
    r"(?ix)^(?:"
    r"commission\s+of\s+or\s+attempt.*"
    r"|attempt,?\s*solicit,?\s*or\s*conspire.*"
    r"|chapter\s+\d+.*"
    r"|f\.?s\.?\s*[\d.]+.*"
    r"|s\.?\s*\d{3}\.\d+.*"
    r"|rcw\s+[\d\s.a-z]+$"
    r"|guilty/?convict.*"
    r"|adjudication\s+withheld.*"
    r"|principal\s*$"
    r"|charge\s+correlation\s+pending.*"
    r"|no\s+picture\s+available.*"
    r"|registration\s+of\s+criminal\s+offenders.*"
    r"|scars,?\s*marks\s+and\s+tattoos.*"
    r"|alias(?:es)?\s*(?:information)?:?$"
    r"|photos?:?$"
    r"|more\s+information.*"
    r"|compliant\s+tier\s+level.*"
    r"|offender\s+age\s+at\s+time.*"
    r"|physical\s+description.*"
    r"|name:?$"
    r"|level:?.*"
    r"|status:?.*"
    r"|this\s+link\s+reflects.*"
    r")$"
)

_DATE = re.compile(
    r"(?ix)\b(?:"
    r"\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}"
    r"|\d{4}-\d{1,2}-\d{1,2}"
    r"|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{2,4}"
    r")\b"
)

_STATUTE_ONLY = re.compile(
    r"(?ix)^(?:"
    r"(?:f\.?s\.?|s\.?c\.?\s*code|rcw|u\.?s\.?c\.?|c\.?r\.?s\.?)\s*[\d\s.()\-a-z/]+"
    r"|s\.\s*\d{2,4}\.\d+.*"
    r"|chapter\s+\d+.*"
    r"|\d{2,}-\d{3,4}.*"  # case numbers like 9709272 / 21-5510
    r"|[\d\s.\-()a-z]{3,40}$"  # bare numbers / cites
    r")$"
)

# Court / place junk often after FL short charges
_COURT_JUNK = re.compile(
    r"(?ix)\b(?:"
    r"(?:county|judicial\s+district|superior\s+court|circuit\s+court|"
    r"district\s+court|dept\.?\s+of\s+corrections|ndoc|doc)\b.*"
    r")$"
)

# Known short registry codes → readable labels (first match wins)
_CODE_MAP = [
    (re.compile(r"(?i)SEX\s*BAT\s*/?\s*WPN\.?\s*OR\s*FORCE"), "Sexual battery (weapon/force)"),
    (re.compile(r"(?i)SEX\s*BAT\s*BY\s*ADULT\s*/?\s*VCTM\s*UNDER\s*12"), "Sexual battery (adult/victim under 12)"),
    (re.compile(r"(?i)SEX\s*BAT\s*BY\s*JUVEN\s*/?\s*VCTM\s*UNDER\s*12"), "Sexual battery (juvenile/victim under 12)"),
    (re.compile(r"(?i)SEX\s*BAT\s*/?\s*INJ\s*NOT\s*LIKELY"), "Sexual battery (injury not likely)"),
    (re.compile(r"(?i)LEWD\s*ASLT\s*/?\s*SEX\s*BAT\s*VCTM\s*<?\s*16"), "Lewd assault / sex bat (victim <16)"),
    (re.compile(r"(?i)LEWD,?\s*LASCIVIOUS\s*(?:CHILD\s*)?U/?16"), "Lewd/lascivious (child under 16)"),
    (re.compile(r"(?i)SEXUAL\s*BATTERY\s*BY\s*ADULT\s*ON\s*ADULT"), "Sexual battery (adult on adult)"),
    (re.compile(r"(?i)FAIL(?:URE)?\s*TO\s*REGIST|FAIL\s*COMPLY\s*REG|RE-?REGISTR"), "Fail to register"),
    (re.compile(r"(?i)TRAVELING\s+TO\s+MEET\s+MINOR"), "Traveling to meet minor"),
    (re.compile(r"(?i)STATUTORY\s+SEXUAL\s+SEDUCTION"), "Statutory sexual seduction"),
    (re.compile(r"(?i)COMMUNICATE\s+WITH\s+MINOR\s+FOR\s+IMMORAL"), "Communicate with minor (immoral purposes)"),
]

_MONTHS = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)"
)


def _norm(s: str) -> str:
    return " ".join((s or "").split()).strip(" ;,|")


def _strip_dates(s: str) -> str:
    t = _DATE.sub(" ", s)
    t = re.sub(rf"(?i)\b{_MONTHS}\.?\s+\d{{1,2}},?\s+\d{{2,4}}\b", " ", t)
    t = re.sub(r"\(\s*(?:19|20)\d{2}\s*\)", " ", t)
    t = re.sub(r"\b(?:19|20)\d{2}\b", " ", t)
    return _norm(t)


def _strip_statute_cites(s: str) -> str:
    t = s
    t = re.sub(r"(?i)\*?\s*excluding\s+subsections?\s+[\d.(),\s]+", " ", t)
    t = re.sub(r"(?i)\bF\.?S\.?\s*[\d.()/a-z]+\s*(?:\(PRINCIPAL\))?", " ", t)
    t = re.sub(r"(?i)\bs\.\s*\d{2,4}\.\d+(?:\([a-z0-9]+\))*\d*", " ", t)
    t = re.sub(r"(?i)\bChapter\s+\d+\b", " ", t)
    t = re.sub(r"(?i)\bRCW\s+[\d\s.A-Z]+", " ", t)
    t = re.sub(r"(?i)\b(?:PRINCIPAL|CHARGE CORRELATION PENDING)\b", " ", t)
    t = re.sub(r"\b\d{5,}\b", " ", t)  # case / booking numbers
    t = re.sub(r"\s{2,}", " ", t)
    return _norm(t)


def _lewd_qualifier(clause: str) -> str:
    """Compress long lewd/lascivious descriptions to a short qualifier."""
    low = clause.lower()
    parts: List[str] = []
    if re.search(r"under\s*12|victim\s+is\s+under\s*12|u/?12|< ?12", low):
        parts.append("under 12")
    elif re.search(r"less\s+than\s*16|under\s*16|12-15|u/?16|< ?16", low):
        parts.append("under 16")
    if re.search(r"force|coercion", low):
        parts.append("force")
    if re.search(r"unclothed\s+genitals?", low):
        parts.append("unclothed genitals")
    if re.search(r"molest", low) and "unclothed" not in " ".join(parts):
        parts.append("molestation")
    if re.search(r"conduct", low) and not parts:
        parts.append("conduct")
    if re.search(r"battery|batt\b", low) and not parts:
        parts.append("battery")
    if not parts:
        return "Lewd/lascivious"
    # Prefer compact pairs used in the preferred example
    if "under 12" in parts and "force" in parts:
        return "Lewd/lascivious (under 12/force)"
    if "unclothed genitals" in parts:
        return "Lewd/lascivious (unclothed genitals)"
    return "Lewd/lascivious (" + "/".join(parts) + ")"


def _title_offense(s: str) -> str:
    s = s.strip()
    if not s:
        return s
    # Already mostly title/upper codes handled elsewhere
    if s.isupper() and len(s) < 40:
        return s.title()
    # Sentence case for known phrases
    low = s.lower()
    if low.startswith("sexual battery"):
        rest = s[len("sexual battery") :].strip(" -:")
        return ("Sexual battery " + rest).strip() if rest else "Sexual battery"
    return s[0].upper() + s[1:] if len(s) > 1 else s.upper()


def _extract_from_clause(clause: str) -> Optional[str]:
    c = _norm(clause)
    if not c or len(c) < 3:
        return None
    if _DROP_CLAUSE.match(c):
        return None
    # Bare statute / case number
    if _STATUTE_ONLY.match(c) and not re.search(
        r"(?i)sexual|lewd|rape|molest|battery|assault|porn|child|indecent|sodomy",
        c,
    ):
        return None

    c = _strip_dates(c)
    c = _strip_statute_cites(c)
    if not c or _DROP_CLAUSE.match(c):
        return None

    # Map short codes first (before general cleanup)
    src = clause + " " + c
    for rx, label in _CODE_MAP:
        if rx.search(src):
            return label
    m = re.search(r"(?i)CHILD\s+MOLESTATION[- ]?(\d)", src)
    if m:
        return f"Child molestation {m.group(1)}"

    # "21 - 5510 (a3) — Sexual exploitation of a child..."
    m = re.search(r"—\s*(.+)$", c)
    if m and len(m.group(1)) > 8:
        c = _norm(m.group(1))
    m = re.search(r"§\s*[\d\-.]+\s*[—-]\s*(.+)$", c)
    if m and len(m.group(1)) > 8:
        c = _norm(m.group(1))

    # Sexual battery (with optional exclusion noise already stripped)
    if re.search(r"(?i)sexual\s+battery", c):
        extra = []
        if re.search(r"(?i)weapon|force|wpn", c):
            extra.append("weapon/force")
        if re.search(r"(?i)under\s*12", c):
            extra.append("victim under 12")
        if re.search(r"(?i)injury\s+not\s+likely", c):
            extra.append("injury not likely")
        base = "Sexual battery"
        return f"{base} ({', '.join(extra)})" if extra else base

    # Lewd / lascivious long form
    if re.search(r"(?i)lewd|lascivious", c):
        return _lewd_qualifier(c)

    # Rape / sodomy / molestation / exploitation
    for pat, lab in (
        (r"(?i)\brape\b.*\b1st\b|\b1st\s+degree\s+rape\b", "Rape 1st degree"),
        (r"(?i)\brape\b.*\b3rd\b|\b3rd\s+degree\s+rape\b", "Rape 3rd degree"),
        (r"(?i)\brape\b", "Rape"),
        (r"(?i)sodomy", "Sodomy"),
        (r"(?i)child\s+molestation", "Child molestation"),
        (r"(?i)sexual\s+exploitation\s+of\s+a\s+child", "Sexual exploitation of a child"),
        (r"(?i)aggravated\s+indecent\s+liberties", "Aggravated indecent liberties"),
        (r"(?i)indecent\s+liberties", "Indecent liberties"),
        (r"(?i)criminal\s+sexual\s+conduct", "Criminal sexual conduct"),
        (r"(?i)sexual\s+assault", "Sexual assault"),
        (r"(?i)unlawful\s+sexual\s+activity", "Unlawful sexual activity with minor"),
        (r"(?i)possession\s+of\s+child\s+porn", "Possession of child pornography"),
        (r"(?i)false\s+imprison", "False imprisonment"),
    ):
        if re.search(pat, c):
            return lab

    # Drop court/location trailing junk for remaining short phrases
    c2 = _COURT_JUNK.sub("", c).strip(" ;,")
    c2 = re.sub(r"(?i)\b[A-Z][a-z]+,\s*[A-Z]{2}\b", " ", c2)  # City, ST
    c2 = _norm(c2)
    if not c2 or len(c2) < 4:
        return None
    if _DROP_CLAUSE.match(c2) or _STATUTE_ONLY.match(c2):
        return None
    # Avoid dumping remaining long legal paragraphs
    if len(c2) > 90:
        # Try first clause-like fragment
        c2 = re.split(r"\s+where\s+|\s+by\s+offender\s+", c2, maxsplit=1)[0]
        c2 = _norm(c2)
        if len(c2) > 80:
            return None
    # Skip pure person names / address-like leftovers
    if re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}$", c2) and not re.search(
        r"(?i)sexual|lewd|rape|child|assault|battery", c2
    ):
        return None
    return _title_offense(c2)


def _dedupe_preserve(items: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for it in items:
        key = it.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def summarize_crime(text: Optional[str], *, max_len: int = 140) -> str:
    """
    Return a short multi-offense summary for report cards.

    Example::

        Commission of OR Attempt...; Chapter 794; Sexual Battery *Excluding...;
        s. 800.04(4)(b); Lewd/lascivious ... under 12 ... force...;
        s. 800.04(5)(c)1; Lewd/lascivious ... unclothed genitals...; s. 800.04(5)(d)

        → Sexual battery · Lewd/lascivious (under 12/force) · Lewd/lascivious (unclothed genitals)
    """
    raw = _norm(text or "")
    if not raw:
        return ""

    # Pre-strip global junk headers
    raw = re.sub(
        r"(?i)^scars,?\s*marks\s+and\s+tattoos\s*[—\-:]+\s*",
        "",
        raw,
    )
    raw = re.sub(r"(?i)no\s+photograph\s+available[^.]*\.", " ", raw)

    parts = re.split(r"\s*;\s*", raw)
    labels: List[str] = []
    for p in parts:
        lab = _extract_from_clause(p)
        if lab:
            labels.append(lab)

    # If nothing parsed, fall back to first non-junk fragment stripped
    if not labels:
        cleaned = _strip_statute_cites(_strip_dates(raw))
        cleaned = re.sub(
            r"(?i)commission of or attempt, solicit, or conspire to commit",
            "",
            cleaned,
        )
        cleaned = _norm(cleaned)
        if cleaned and len(cleaned) <= max_len:
            return cleaned
        if cleaned:
            cut = cleaned[: max_len - 1]
            if " " in cut:
                cut = cut.rsplit(" ", 1)[0]
            return cut.rstrip(" ,;:") + "…"
        return ""

    labels = _dedupe_preserve(labels)

    # Merge identical "Lewd/lascivious" with no qualifier if we already have qualified ones
    lewd_qual = [x for x in labels if x.lower().startswith("lewd/lascivious (")]
    if lewd_qual:
        labels = [x for x in labels if x.casefold() != "lewd/lascivious"]

    # Prefer "Sexual battery (…)" over bare "Sexual battery" when both appear
    has_sb_qual = any(
        x.casefold().startswith("sexual battery (") for x in labels
    )
    if has_sb_qual:
        labels = [x for x in labels if x.casefold() != "sexual battery"]

    summary = " · ".join(labels)
    if len(summary) <= max_len:
        return summary

    # Drop trailing items until it fits; keep at least one
    while len(labels) > 1 and len(" · ".join(labels)) > max_len:
        labels.pop()
    summary = " · ".join(labels)
    if len(summary) <= max_len:
        return summary
    cut = summary[: max_len - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ·,;:") + "…"
