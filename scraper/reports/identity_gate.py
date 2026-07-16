"""Gate: never attach report-page data to a different person.

Wrong-person joins (e.g. FL PERSON_NBR used as flyer personId) are worse than
missing data. Every HTML scrape must pass a name identity check before
demographics, photos, or html_verified flags are applied.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from scraper.database.identity import (
    first_names_compatible,
    last_names_compatible,
    _norm_token,
)

# UI chrome often bolded on FDLE flyers — not person names
_HTML_NAME_NOISE = frozenset(
    {
        "sexual offender",
        "sexual predator",
        "sexually violent predator",
        "confinement",
        "released - subject to registration",
        "click here to track this offender",
        "link to fdle",
        "fdle mobile app",
        "subject's flyer",
        "transient offender",
        "transient predator",
        "how to use this map",
        "print map",
        "view larger map",
        "show map",
        "more resources",
        "tier level",
        "delaware information subscription service",
        "black",
        "white",
        "male",
        "female",
        "hispanic",
        "brown",
        "grey",
        "gray",
        "blue",
        "green",
        "hazel",
        "blond or strawberry",
        "black or african american",
        "not available",
        "date unavailable",
        "civil commitment",
        "delaware state police",
        "kansas city",
        "st louis",
        "saint louis",
        "st charles",
        "st francois",
        "blue springs",
        "poplar bluff",
        "new madrid",
        "el dorado springs",
        "o fallon",
        "red or auburn",
        "tier level",
        "state bureau of identification",
        "st louis city",
    }
)

_HTML_NAME_NOISE_SUBSTR = (
    "this map plots",
    "selected offender",
    "subscription service",
    "click here",
    "view larger",
    "how to use",
    "tier level",
    "more resources",
    "or strawberry",
    "african american",
    "not available",
    "unavailable",
    "state police",
    "civil commitment",
    "or auburn",
    "bureau of identification",
    "louis city",
)

_GEN_SUFFIX = frozenset(
    {"jr", "sr", "ii", "iii", "iv", "v", "vi", "2nd", "3rd", "4th", "junior", "senior"}
)

# Common place/org tokens that appear as 2-word "names" in HTML
_PLACE_OR_ORG = frozenset(
    {
        "kansas",
        "city",
        "louis",
        "charles",
        "police",
        "delaware",
        "state",
        "springs",
        "bluff",
        "madrid",
        "fallon",
        "francois",
        "dorado",
        "available",
        "commitment",
        "civil",
        "auburn",
        "strawberry",
    }
)


def _strip_gen_suffix(tokens: List[str]) -> List[str]:
    out = list(tokens)
    while out and out[-1].casefold().strip(".") in _GEN_SUFFIX:
        out.pop()
    return out


def _looks_like_person_name(n: str) -> bool:
    raw = (n or "").strip()
    low = raw.casefold()
    if not low or low in _HTML_NAME_NOISE:
        return False
    for frag in _HTML_NAME_NOISE_SUBSTR:
        if frag in low:
            return False
    if low.endswith(":") or low.startswith("http"):
        return False
    if any(ch.isdigit() for ch in low):
        return False
    # Person names: 2–5 tokens, mostly letters, not a sentence
    tokens = [t for t in re.split(r"\s+", raw) if t]
    tokens = _strip_gen_suffix(tokens)
    if len(tokens) < 2 or len(tokens) > 5:
        return False
    if len(raw) > 48:
        return False
    # Reject sentence-like (period, comma mid-phrase beyond LAST, FIRST)
    if raw.count(".") > 0 and not any(
        t.casefold().rstrip(".") in _GEN_SUFFIX for t in raw.split()
    ):
        # allow Jr. only
        if not re.search(r"\b(Jr|Sr)\.?\s*$", raw, re.I):
            return False
    if raw.count(",") > 1:
        return False
    alpha = sum(1 for c in raw if c.isalpha())
    if alpha < 4 or alpha / max(1, len(raw.replace(" ", ""))) < 0.85:
        return False
    # Each token should look like a name part (letters / hyphen / apostrophe)
    for t in tokens:
        core = t.rstrip(".")
        if not re.fullmatch(r"[A-Za-z][A-Za-z\-']{0,30}", core):
            return False
    # Reject pure place/org two-word labels
    cores = [t.casefold().rstrip(".") for t in tokens]
    if all(c in _PLACE_OR_ORG for c in cores):
        return False
    if len(cores) == 2 and cores[0] in _PLACE_OR_ORG and cores[1] in _PLACE_OR_ORG:
        return False
    return True


def extract_person_name_from_html(html: str) -> Optional[str]:
    """Best-effort display name from a registry report page."""
    if not html:
        return None
    # Prefer early page region (headers) over map/footer chrome
    head = html[:120_000] if len(html) > 120_000 else html
    candidates: List[str] = []
    for m in re.finditer(
        r'font-weight:\s*bold[^>]*>\s*([A-Za-z][A-Za-z0-9 \-\'.]{3,48})\s*<',
        head,
        flags=re.I,
    ):
        n = re.sub(r"\s+", " ", m.group(1)).strip()
        if _looks_like_person_name(n):
            candidates.append(n)
    if candidates:
        # Prefer 2–3 token ALL CAPS / Title names over long phrases
        def _score(s: str) -> tuple:
            toks = s.split()
            caps = sum(1 for t in toks if t.isupper())
            return (caps, -abs(len(toks) - 2), -len(s))

        candidates.sort(key=_score, reverse=True)
        return candidates[0]
    m = re.search(
        r"(?<![A-Za-z])(?:Offender\s+)?Name\s*:?\s*</(?:div|span|label|th|td)>\s*"
        r"<(?:div|span|td)[^>]*>\s*([^<]{3,48})\s*<",
        head,
        flags=re.I,
    )
    if m:
        n = re.sub(r"\s+", " ", m.group(1)).strip()
        if _looks_like_person_name(n):
            return n
    return None


def extract_person_name_from_html_path(path: Any) -> Optional[str]:
    p = Path(str(path or ""))
    if not p.is_file():
        return None
    try:
        return extract_person_name_from_html(
            p.read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        return None


def split_html_display_name(name: str) -> Tuple[str, str, str]:
    """Return (first, middle, last) tokens from a display name."""
    raw = re.sub(r"\s+", " ", (name or "").strip())
    if not raw:
        return "", "", ""
    if "," in raw:
        left, right = raw.split(",", 1)
        parts = (right + " " + left).split()
    else:
        parts = raw.split()
    # Strip Jr/Sr/II/IV *before* taking last token
    parts = _strip_gen_suffix(parts)
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def _last_names_overlap(a: str, b: str) -> bool:
    if last_names_compatible(a, b):
        return True
    ra, rb = _norm_token(a), _norm_token(b)
    pa, pb = ra.split(), rb.split()
    if not pa or not pb:
        return False
    if len(pa) == 1 and pa[0] in pb:
        return True
    if len(pb) == 1 and pb[0] in pa:
        return True
    return bool(set(pa) & set(pb))


def _normalize_name_parts(first: str, middle: str, last: str) -> Tuple[str, str, str]:
    """Drop Jr/Sr/II suffixes from last/middle for comparison."""
    def _clean(s: str) -> str:
        toks = _strip_gen_suffix([t for t in re.split(r"\s+", (s or "").strip()) if t])
        return " ".join(toks)

    last_c = _clean(last)
    # Sometimes suffix is on first/middle blob
    mid_c = _clean(middle)
    first_c = _clean(first)
    return first_c, mid_c, last_c


def record_name_matches_html(
    record: Dict[str, Any],
    html_name: Optional[str],
) -> bool:
    """True when the page name is the same person as *record*."""
    if not html_name or not _looks_like_person_name(html_name):
        return False
    hf, hm, hl = split_html_display_name(html_name)
    hf, hm, hl = _normalize_name_parts(hf, hm, hl)
    rf = str(record.get("first_name") or "").strip()
    rm = str(record.get("middle_name") or "").strip()
    rl = str(record.get("last_name") or "").strip()
    if not rl:
        full = str(record.get("full_name") or "").strip()
        if full:
            rf, rm, rl = split_html_display_name(full)
    rf, rm, rl = _normalize_name_parts(rf, rm, rl)
    if not rl or not hl:
        return False
    if not _last_names_overlap(rl, hl):
        return False
    if rf and hf:
        return first_names_compatible(rf, hf)
    if not rf and not hf:
        if rm and hm and first_names_compatible(rm, hm):
            return True
        return len(_norm_token(rl).split()) >= 2
    # One side missing first — too weak for attachment
    return False


def demo_identity_ok(
    record: Dict[str, Any],
    demo: Dict[str, Any],
) -> Tuple[bool, str]:
    """Return (ok, reason) for whether *demo* is the same person as *record*.

    Name must match. When both sides have DOB, DOB must be compatible
    (NUCLEAR reject on conflict).
    """
    from scraper.database.identity import dobs_compatible

    html_name = str(demo.get("full_name") or demo.get("name") or "").strip() or None
    if not html_name:
        html_name = extract_person_name_from_html_path(demo.get("report_html_path") or "")
    if not html_name and demo.get("report_html"):
        html_name = extract_person_name_from_html(str(demo.get("report_html")))
    if not html_name:
        if demo.get("report_fetch_ok"):
            return False, "html_name_missing"
        return False, "report_not_ok"
    if not record_name_matches_html(record, html_name):
        return False, f"name_mismatch:{html_name}"
    rec_dob = record.get("date_of_birth")
    demo_dob = demo.get("date_of_birth") or demo.get("dob")
    if rec_dob and demo_dob:
        dc = dobs_compatible(rec_dob, demo_dob)
        if dc is False:
            return False, f"dob_mismatch:{demo_dob}"
    return True, f"name_match:{html_name}"


def _flags_list(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, dict):
        return [str(x) for x in (raw.get("tags") or [])]
    if not raw:
        return []
    try:
        p = json.loads(str(raw))
        if isinstance(p, list):
            return [str(x) for x in p]
        if isinstance(p, dict):
            return [str(x) for x in (p.get("tags") or [])]
    except (TypeError, json.JSONDecodeError):
        pass
    return [str(raw)]


def strip_wrong_person_html(record: Dict[str, Any], *, reason: str = "") -> bool:
    """
    Remove report_html_path / photo / html sources that fail identity.

    Keeps bulk CSV fields. Returns True if the record was modified.
    """
    from scraper.database.sources import apply_sources_to_record, dumps_sources, parse_sources

    changed = False
    html_path = str(record.get("report_html_path") or "").strip()
    if html_path:
        hn = extract_person_name_from_html_path(html_path)
        if hn and not record_name_matches_html(record, hn):
            record["report_html_path"] = None
            photo = str(record.get("photo_path") or "")
            hp = html_path.replace("\\", "/")
            if hp and hp in photo.replace("\\", "/"):
                record["photo_path"] = None
            # Also drop sibling _assets photo dirs
            stem = Path(html_path).stem
            if stem and stem in photo.replace("\\", "/"):
                record["photo_path"] = None
            changed = True

    sources = parse_sources(record.get("sources_json"))
    if sources:
        kept: List[Dict[str, Any]] = []
        for s in sources:
            if not isinstance(s, dict):
                continue
            st = str(s.get("type") or "").lower()
            if st not in ("report_html", "nsopw_report"):
                kept.append(s)
                continue
            spath = str(s.get("html_path") or "")
            name = extract_person_name_from_html_path(spath) if spath else None
            if name and record_name_matches_html(record, name):
                kept.append(s)
                continue
            if name and not record_name_matches_html(record, name):
                changed = True
                continue
            if s.get("html_verified"):
                s = dict(s)
                s["html_verified"] = False
                s["html_status"] = f"identity_unverified:{reason or 'no_name'}"
                changed = True
            kept.append(s)
        if changed:
            record["sources_json"] = dumps_sources(kept)
            apply_sources_to_record(record)

    if changed:
        flags = _flags_list(record.get("flags"))
        if "identity_html_mismatch" not in flags:
            flags.append("identity_html_mismatch")
        while "race_html_verified" in flags:
            flags.remove("race_html_verified")
        # If race was only from wrong HTML multi-display, recompute already done
        record["flags"] = json.dumps(flags)
    return changed
