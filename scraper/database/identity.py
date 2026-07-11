"""Person-identity matching: middle names + multiple identifiers.

Used when merging CSV bulk rows into existing offenders so two different
people who share a common surname (e.g. NIRAJ V PATEL in FL vs
NIRAJ RASHMIBABU PATEL in CO) are not collapsed into one row.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _norm_token(s: Any) -> str:
    t = str(s or "").strip().casefold()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def _first_token(s: Any) -> str:
    parts = _norm_token(s).split()
    return parts[0] if parts else ""


def _digits(s: Any) -> str:
    return re.sub(r"[^0-9]", "", str(s or ""))


def normalize_dob(value: Any) -> str:
    """
    Normalize DOB to YYYYMMDD when possible, else digits-only.

    Accepts ISO, US MM/DD/YYYY, and bare digit strings.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    # ISO date prefix
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    # US MM/DD/YYYY
    m = re.match(r"^(\d{1,2})[/=-](\d{1,2})[/=-](\d{4})", raw)
    if m:
        mo, d, y = int(m.group(1)), int(m.group(2)), m.group(3)
        return f"{y}{mo:02d}{d:02d}"
    dig = _digits(raw)
    if len(dig) == 8:
        # ambiguous MMDDYYYY vs YYYYMMDD — prefer year-first if looks like year
        if dig.startswith(("19", "20")):
            return dig
        # MMDDYYYY
        return dig[4:] + dig[:4]
    return dig


def dobs_compatible(a: Any, b: Any) -> Optional[bool]:
    """
    True = same person DOB, False = hard conflict, None = insufficient data.
    """
    da, db = normalize_dob(a), normalize_dob(b)
    if not da or not db:
        return None
    if da == db:
        return True
    # Allow year-only vs full if years match and one side is only year
    if len(da) == 4 and len(db) >= 4 and da == db[:4]:
        return True
    if len(db) == 4 and len(da) >= 4 and db == da[:4]:
        return True
    return False


def middles_compatible(a: Any, b: Any) -> Optional[bool]:
    """
    True = compatible middle names, False = conflict, None = either empty.

    Rules:
      - either empty → unknown (None), not a conflict
      - single letter matches word starting with that letter
      - full tokens equal (casefold) or one is prefix of the other (token)
      - otherwise conflict (V vs RASHMIBABU)
    """
    ma, mb = _norm_token(a), _norm_token(b)
    if not ma or not mb:
        return None
    if ma == mb:
        return True
    # Strip periods already gone; single-letter initial
    ta, tb = ma.replace(" ", ""), mb.replace(" ", "")
    if len(ta) == 1 and tb.startswith(ta):
        return True
    if len(tb) == 1 and ta.startswith(tb):
        return True
    # Multi-token: all tokens of shorter appear in longer as initials or full
    pa, pb = ma.split(), mb.split()
    if len(pa) == 1 and len(pb) >= 1:
        if pb[0].startswith(pa[0]) or pa[0].startswith(pb[0]):
            # only allow if shorter is prefix of longer token (Rashmi vs Rashmibabu)
            shorter, longer = (pa[0], pb[0]) if len(pa[0]) <= len(pb[0]) else (pb[0], pa[0])
            if longer.startswith(shorter) and len(shorter) >= 2:
                return True
    if len(pb) == 1 and len(pa) >= 1:
        shorter, longer = (pb[0], pa[0]) if len(pb[0]) <= len(pa[0]) else (pa[0], pb[0])
        if longer.startswith(shorter) and len(shorter) >= 2:
            return True
    return False


def first_names_compatible(a: Any, b: Any) -> bool:
    """Require first-token match; slight prefix OK for OCR (NIRAJ/NITRAJ only if equal)."""
    fa, fb = _first_token(a), _first_token(b)
    if not fa or not fb:
        return False
    if fa == fb:
        return True
    # Allow 1-char OCR slip only when same length and edit distance 1 — keep strict
    # for now: exact first token only (NITRAJ ≠ NIRAJ)
    return False


def last_names_compatible(a: Any, b: Any) -> bool:
    la, lb = _norm_token(a), _norm_token(b)
    return bool(la and lb and la == lb)


def _height_norm(h: Any) -> str:
    s = str(h or "").strip()
    if not s:
        return ""
    # 600, 6'00", 6 00 → 600
    dig = _digits(s)
    if len(dig) == 3:
        return dig
    if len(dig) == 2:  # 60 → 600?
        return dig + "0"
    m = re.search(r"(\d)\s*['′]\s*(\d{1,2})", s)
    if m:
        return f"{m.group(1)}{int(m.group(2)):02d}"
    return dig


def _weight_norm(w: Any) -> str:
    dig = _digits(w)
    return dig[:3] if dig else ""


def extract_identity(rec: Dict[str, Any]) -> Dict[str, str]:
    """Pull identity fields from a record dict."""
    mid = str(rec.get("middle_name") or "").strip()
    first = str(rec.get("first_name") or "").strip()
    # Multi-token first often holds middle
    if not mid and first and len(first.split()) >= 2:
        parts = first.split()
        first = parts[0]
        mid = " ".join(parts[1:])
    return {
        "first_name": first,
        "middle_name": mid,
        "last_name": str(rec.get("last_name") or "").strip(),
        "date_of_birth": str(rec.get("date_of_birth") or "").strip(),
        "height": str(rec.get("height") or "").strip(),
        "weight": str(rec.get("weight") or "").strip(),
        "external_id": str(rec.get("external_id") or "").strip(),
        "source_url": str(rec.get("source_url") or "").strip(),
        "state": str(rec.get("state") or rec.get("source_state") or "").strip().upper(),
        "source_state": str(rec.get("source_state") or "").strip().upper(),
        "zip_code": str(rec.get("zip_code") or "").strip(),
        "city": str(rec.get("city") or "").strip(),
        "address": str(rec.get("address") or "").strip(),
        "photo_url": str(rec.get("photo_url") or "").strip(),
    }


def score_identity_match(
    a: Dict[str, Any],
    b: Dict[str, Any],
) -> Tuple[int, List[str], bool]:
    """
    Score whether *a* and *b* are the same person.

    Returns ``(score, reasons, hard_reject)``.

    Hard reject when DOBs or middle names clearly conflict — even if height
    matches. Callers must not merge when ``hard_reject`` is True.

    Positive evidence (need multiple when relying on name):
      +5 external_id exact (strong registry id)
      +5 DOB match
      +4 middle name full match / +2 initial match
      +3 first+last name
      +2 height+weight both match
      +2 same zip
      +1 same state
      +1 same city
    """
    ia, ib = extract_identity(a), extract_identity(b)
    reasons: List[str] = []
    score = 0
    hard = False

    # --- Hard conflicts first ---
    dob_c = dobs_compatible(ia["date_of_birth"], ib["date_of_birth"])
    if dob_c is False:
        return 0, ["dob_conflict"], True

    mid_c = middles_compatible(ia["middle_name"], ib["middle_name"])
    if mid_c is False:
        return 0, ["middle_conflict"], True

    if not last_names_compatible(ia["last_name"], ib["last_name"]):
        # Allow if external_id already matches strongly below
        pass
    if ia["last_name"] and ib["last_name"] and not last_names_compatible(
        ia["last_name"], ib["last_name"]
    ):
        return 0, ["last_name_mismatch"], True
    if ia["first_name"] and ib["first_name"] and not first_names_compatible(
        ia["first_name"], ib["first_name"]
    ):
        return 0, ["first_name_mismatch"], True

    # --- Positive signals ---
    ext_a = ia["external_id"].casefold()
    ext_b = ib["external_id"].casefold()
    if ext_a and ext_b and ext_a == ext_b:
        # Same registry id — strong, but still respect hard rejects above
        score += 5
        reasons.append("external_id")
    elif ext_a and ext_b and ext_a != ext_b:
        # Different ids: only a soft penalty if both look like registry ids
        # and jurisdictions differ — not hard reject (multi-state can differ)
        reasons.append("external_id_diff")

    if last_names_compatible(ia["last_name"], ib["last_name"]) and first_names_compatible(
        ia["first_name"], ib["first_name"]
    ):
        score += 3
        reasons.append("first_last")

    if dob_c is True:
        score += 5
        reasons.append("dob")

    if mid_c is True:
        ma, mb = _norm_token(ia["middle_name"]), _norm_token(ib["middle_name"])
        if len(ma) == 1 or len(mb) == 1:
            score += 2
            reasons.append("middle_initial")
        else:
            score += 4
            reasons.append("middle_full")

    ha, hb = _height_norm(ia["height"]), _height_norm(ib["height"])
    wa, wb = _weight_norm(ia["weight"]), _weight_norm(ib["weight"])
    if ha and hb and ha == hb and wa and wb and wa == wb:
        score += 2
        reasons.append("height_weight")
    elif ha and hb and ha == hb:
        score += 1
        reasons.append("height")

    # Location corroboration
    za = re.sub(r"\D", "", ia["zip_code"])[:5]
    zb = re.sub(r"\D", "", ib["zip_code"])[:5]
    if za and zb and za == zb:
        score += 2
        reasons.append("zip")
    sa = (ia["state"] or ia["source_state"] or "").split("|")[0].strip().upper()
    sb = (ib["state"] or ib["source_state"] or "").split("|")[0].strip().upper()
    if sa and sb and sa == sb:
        score += 1
        reasons.append("state")
    if (
        ia["city"]
        and ib["city"]
        and _norm_token(ia["city"]) == _norm_token(ib["city"])
    ):
        score += 1
        reasons.append("city")

    return score, reasons, hard


def should_merge_records(
    incoming: Dict[str, Any],
    existing: Dict[str, Any],
    *,
    min_score: int = 6,
    unique_name_candidate: bool = False,
) -> Tuple[bool, int, List[str]]:
    """
    Decide whether to merge *incoming* into *existing*.

    Requires multi-identifier agreement:
      - never on first+last alone
      - hard reject on DOB / middle conflicts
      - min_score default 6 ≈ first+last(3)+DOB(5) or first+last+middle_full+hw
      - unique_name_candidate only helps if no middle/DOB conflict and score>=5
        with height_weight or same state — still not name alone
    """
    score, reasons, hard = score_identity_match(incoming, existing)
    if hard:
        return False, score, reasons + ["hard_reject"]
    if score >= min_score:
        return True, score, reasons
    # Unique first+last is still weak for common surnames (Patel, Garcia).
    # Only boost when we also have DOB, middle, or registry id — never
    # height/weight alone (FL NIRAJ V vs CO NIRAJ RASHMIBABU both 6'00" 202).
    if unique_name_candidate and score >= 5 and (
        "dob" in reasons
        or "middle_full" in reasons
        or "middle_initial" in reasons
        or "external_id" in reasons
    ):
        return True, score, reasons + ["unique_name_boost"]
    return False, score, reasons


def person_identity_key(rec: Dict[str, Any]) -> str:
    """
    Stable key for collapsing same-person listings (reports / UI).

    Prefers registry id, else first|middle|last|dob.
    """
    ident = extract_identity(rec)
    ext = ident["external_id"]
    if ext:
        return f"ext:{ext.casefold()}"
    url = ident["source_url"]
    if url:
        # leave URL normalization to caller when possible
        return f"url:{url.casefold()[:120]}"
    return "|".join(
        [
            _first_token(ident["first_name"]),
            _norm_token(ident["middle_name"]),
            _norm_token(ident["last_name"]),
            normalize_dob(ident["date_of_birth"]),
        ]
    )
