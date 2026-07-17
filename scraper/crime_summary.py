"""Summarize long SOR offense / crime strings for report cards.

Registry pages often dump multi-statute boilerplate (FL “Commission of OR
Attempt…”, chapter cites, F.S. sections, case numbers, dates). Reports show a
short human summary; Misclassify / detail drawers keep the full text.
"""
from __future__ import annotations

import re
from typing import List, Optional

from scraper.crime_summary_clause import (
    CITY_STATE,
    COUNTY_LOC,
    extract_from_clause,
    norm,
    strip_dates,
    strip_location_junk,
    to_regular_case,
)
from scraper.crime_summary_junk import (
    clean_label,
    is_junk_label,
    strip_parentheses,
    strip_statute_cites,
)


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


def summarize_crime(text: Optional[str], *, max_len: int = 200) -> str:
    """
    Return a multi-offense summary for report/export cards.

    Example (CHRISTOPHER SINGH-style FL dump)::

        Commission of OR Attempt...; Chapter 794; Sexual Battery *Excluding...;
        s. 800.04(4)(b); Lewd/lascivious ... under 12 ... force...;
        s. 800.04(5)(c)1; Lewd/lascivious ... unclothed genitals...;

        → Sexual battery · Victim under 12/force · Unclothed genitals
    """
    try:
        return _summarize_crime_impl(text, max_len=max_len)
    except Exception:
        # Never let a bad registry string take down the GUI
        try:
            fallback = norm(text or "")
            if not fallback:
                return ""
            from scraper.crime_summary_clause import to_regular_case

            cut = fallback[: max_len]
            return to_regular_case(cut)
        except Exception:
            return ""


def _summarize_crime_impl(text: Optional[str], *, max_len: int = 200) -> str:
    raw = norm(text or "")
    if not raw:
        return ""

    raw = re.sub(
        r"(?i)^scars,?\s*marks\s+and\s+tattoos\s*[—\-:]+\s*",
        "",
        raw,
    )
    raw = re.sub(r"(?i)no\s+photograph\s+available[^.]*\.", " ", raw)

    labels: List[str] = []
    for p in re.split(r"\s*;\s*", raw):
        lab = extract_from_clause(p)
        if lab:
            labels.append(lab)

    if not labels:
        cleaned = strip_location_junk(strip_statute_cites(strip_dates(raw)))
        cleaned = re.sub(
            r"(?i)commission of or attempt, solicit, or conspire to commit",
            "",
            cleaned,
        )
        cleaned = clean_label(strip_location_junk(norm(cleaned))) or ""
        if cleaned and is_junk_label(cleaned):
            cleaned = ""
        cleaned = strip_parentheses(cleaned).replace("(", "").replace(")", "")
        # Never print lewd/lascivious on cards
        cleaned = re.sub(r"(?i)\blewd/?\s*lascivious\b", " ", cleaned)
        cleaned = re.sub(r"(?i)\blewd\b|\blascivious\b", " ", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ·;,")
        cleaned = to_regular_case(cleaned)
        if cleaned and len(cleaned) <= max_len:
            return cleaned
        if cleaned:
            cut = cleaned[: max_len - 1]
            if " " in cut:
                cut = cut.rsplit(" ", 1)[0]
            return to_regular_case(cut.rstrip(" ,;:") + "…")
        return ""

    labels = _dedupe_preserve(labels)

    cleaned_labels: List[str] = []
    for x in labels:
        if CITY_STATE.fullmatch(x) or COUNTY_LOC.fullmatch(x):
            continue
        polished = clean_label(strip_location_junk(x))
        if not polished or len(polished) < 3 or is_junk_label(polished):
            continue
        # Hard ban on lewd/lascivious wording in card output
        if re.search(r"(?i)\blewd\b|\blascivious\b", polished):
            polished = re.sub(r"(?i)\blewd/?\s*lascivious\b\s*[—\-]?\s*", "", polished)
            polished = re.sub(r"(?i)\blewd\b|\blascivious\b", "", polished)
            polished = re.sub(r"\s{2,}", " ", polished).strip(" ·;,|—-")
            if not polished or len(polished) < 3:
                continue
        cleaned_labels.append(polished)
    labels = _dedupe_preserve(cleaned_labels)

    has_sb_qual = any(
        x.casefold().startswith("sexual battery —")
        or x.casefold().startswith("sexual battery -")
        for x in labels
    )
    if has_sb_qual:
        labels = [x for x in labels if x.casefold() != "sexual battery"]

    summary = " · ".join(labels)
    # Final guard: never ship parentheses; always regular case (never ALL CAPS)
    summary = summary.replace("(", "").replace(")", "")
    summary = re.sub(r"\s{2,}", " ", summary).strip(" ·;,")
    summary = to_regular_case(summary)
    if len(summary) <= max_len:
        return summary

    while len(labels) > 1 and len(" · ".join(labels)) > max_len:
        labels.pop()
    summary = " · ".join(labels)
    summary = summary.replace("(", "").replace(")", "")
    summary = re.sub(r"\s{2,}", " ", summary).strip(" ·;,")
    summary = to_regular_case(summary)
    if len(summary) <= max_len:
        return summary
    cut = summary[: max_len - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return to_regular_case(cut.rstrip(" ·,;:") + "…")
