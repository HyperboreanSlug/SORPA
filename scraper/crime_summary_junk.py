"""Strip case numbers, statutes, and registry chrome from offense clauses."""
from __future__ import annotations

import re
from typing import Optional

# Pure statute / docket clauses — never English offense phrases
_STATUTE_ONLY = re.compile(
    r"(?ix)^(?:"
    r"(?:f\.?s\.?|s\.?c\.?\s*code|rcw|u\.?s\.?c\.?|c\.?r\.?s\.?|"
    r"texas\s+penal\s+code|penal\s+code)\s*[\d\s.()\-a-z/]+"
    r"|s\.\s*\d{2,4}\.\d+.*"
    r"|chapter\s+\d+.*"
    r"|\d{1,2}:\d+:\d+-\d+-cr-[a-z0-9\-]+"  # federal: 1:0:11-60222-CR-WILLIAMS-01
    r"|(?:cr|case|docket)[- ]?\d{3,}"
    r"|\d{2,}[-/]\d{2,}(?:[-/]\d+)*"  # 21-5510, 9709-272
    r"|[\d.\-()/:]+$"  # pure numeric / cite residue
    r"|[a-z]{0,4}\d{4,}[a-z0-9\-]*$"  # booking / code tokens
    r")$"
)

# Federal / state docket tokens embedded in free text
_DOCKET_TOKEN = re.compile(
    r"(?ix)\b(?:"
    r"\d{1,2}:\d+:\d+-\d+-cr-[a-z0-9\-]+"
    r"|(?:cr|case|docket)[- ]?\d{3,}"
    r")\b"
)

# Leftover mangled docket after digit stripping (e.g. "1:0:11- -Cr-Williams-01")
_DOCKET_RESIDUE = re.compile(
    r"(?ix)^(?:"
    r"[\d:\-./]+\s*-?\s*cr[a-z0-9\-\s]*"
    r"|[\d\s.\-:/()]+"
    r")$"
)

# FL 800.04 / CO 18-3-402 / subsection crumbs (1)(b)
_INLINE_STATUTE = re.compile(
    r"(?ix)\b(?:"
    r"\d{3}\.\d{2,4}(?:\(\d+\))*"  # 800.04(5)
    r"|\d{2,4}\.\d{2,}(?:\(\d+\))*"  # longer dotted cites
    r"|\d{1,2}-\d{1,3}-\d{2,4}(?:\s*\([a-z0-9]+\))*"  # CRS 18-3-402(1)(b)
    r")\b"
)


def is_statute_or_docket(clause: str) -> bool:
    c = " ".join((clause or "").split()).strip(" ;,|")
    if not c:
        return True
    if _STATUTE_ONLY.match(c):
        return True
    if _DOCKET_RESIDUE.match(c):
        return True
    if _DOCKET_TOKEN.fullmatch(c):
        return True
    return False


def strip_statute_cites(s: str) -> str:
    t = s or ""
    # iCrimeWatch / OffenderWatch field prefixes left in stored crime text
    t = re.sub(
        r"(?i)^[\u2022\u00b7•·\-\*]+\s*"
        r"(?:description|details|offense|charge|charges)\s*:?\s*",
        "",
        t,
    )
    t = re.sub(
        r"(?i)^(?:description|details)\s*:\s*",
        "",
        t,
    )
    t = re.sub(r"(?i)\*?\s*excluding\s+subsections?\s+[\d.(),\s]+", " ", t)
    t = re.sub(r"(?i)\bF\.?S\.?\s*[\d.()/a-z]+\s*(?:\(PRINCIPAL\))?", " ", t)
    t = re.sub(r"(?i)\bs\.\s*\d{2,4}\.\d+(?:\([a-z0-9]+\))*\d*", " ", t)
    t = re.sub(r"(?i)\bChapter\s+\d+\b", " ", t)
    t = re.sub(r"(?i)\bRCW\s+[\d\s.A-Z]+", " ", t)
    t = re.sub(r"(?i)\bTEXAS\s+PENAL\s+CODE\s*[\d.()/a-z]*", " ", t)
    t = re.sub(r"(?i)\bC\.?R\.?S\.?\s*[\d.\-()a-z]+\b", " ", t)
    t = re.sub(r"(?i)\b(?:PRINCIPAL|CHARGE CORRELATION PENDING)\b", " ", t)
    t = _DOCKET_TOKEN.sub(" ", t)
    t = _INLINE_STATUTE.sub(" ", t)
    # Orphan subsection crumbs left after cite strip: "(1)(b)" / "1 — b —"
    t = re.sub(r"(?:\s*\([a-z0-9]+\)\s*)+", " ", t, flags=re.I)
    t = re.sub(r"(?i)^\s*\d{1,2}\s*[—\-]\s*[a-z]\s*[—\-]\s*", "", t)
    t = re.sub(r"\b\d{5,}\b", " ", t)  # long booking / case numbers
    # Collapse empty paren residue from stripped statutes: "( (10 COUNTS))" → "(10 COUNTS)"
    t = re.sub(r"\(\s*\(", "(", t)
    t = re.sub(r"\)\s*\)", ")", t)
    t = re.sub(r"\(\s*\)", " ", t)
    t = re.sub(r"\s{2,}", " ", t)
    return " ".join(t.split()).strip(" ;,|")


def is_junk_label(label: str) -> bool:
    """True if a finished summary fragment is case-number / chrome noise."""
    s = " ".join((label or "").split()).strip(" ·;,|")
    if not s or len(s) < 3:
        return True
    # Mis-scraped iCrimeWatch field labels (e.g. FRANCISCO AGUILAR → "description")
    if re.fullmatch(
        r"(?i)[\u2022\u00b7•·\-\*]?\s*description\s*:?",
        s,
    ):
        return True
    # Statute-subsection crumbs: "1 — b — SEX ASSAULT…" (stripped CRS cite)
    if re.match(r"(?i)^\d{1,2}\s*[—\-]\s*[a-z]\b", s) and len(s) < 80:
        return True
    if is_statute_or_docket(s):
        return True
    if _DOCKET_RESIDUE.match(s):
        return True
    if re.search(r"(?i)\d:\d+", s):  # any federal docket remnant
        return True
    letters = sum(1 for c in s if c.isalpha())
    digits = sum(1 for c in s if c.isdigit())
    if digits >= 3 and letters <= max(digits, 4):
        return True
    # Judge/docket scraps like "-Cr-Williams-01"
    if re.search(r"(?i)\bcr-[a-z]+\-\d+", s) and letters < 20:
        return True
    return False


def strip_parentheses(s: str) -> str:
    """No parentheses in report crime text — keep inner words with an em dash."""
    t = s or ""
    # "Sexual battery (weapon/force)" → "Sexual battery — weapon/force"
    t = re.sub(r"\s*\(([^)]*)\)", r" — \1", t)
    t = t.replace("(", " ").replace(")", " ")
    t = re.sub(r"(?:\s*—\s*)+", " — ", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" ·;,|—- ")


def clean_label(label: str) -> Optional[str]:
    """Final polish: drop dockets/statutes and all parentheses."""
    s = strip_statute_cites(label)
    s = strip_parentheses(s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ·;,|—-")
    if is_junk_label(s):
        return None
    return s
