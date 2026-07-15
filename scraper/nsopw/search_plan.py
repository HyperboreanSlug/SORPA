"""
Build a local offender database by searching NSOPW for common ethnic surnames,
saving report links + archived HTML, and enriching demographics from report pages.

NSOPW name search accepts partial first *and* last names. Combined first+last
must be at least 3 characters (e.g. first="M", last="AH" matches Mohamed Ahmed).
Default mode uses A–Z first initials + shortest valid last-name prefixes, then
collapses surnames that share a prefix so one query covers many list names.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from hashlib import sha1
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# Full first names (optional mode) — NSOPW requires first + last.
DEFAULT_FIRST_NAMES = [
    "John", "James", "Robert", "Michael", "David", "William", "Joseph", "Thomas",
    "Carlos", "Juan", "Jose", "Luis", "Miguel", "Maria", "Ana", "Rosa",
    "Wei", "Li", "Min", "Yong", "Jin",
    "Ahmed", "Mohamed", "Ali", "Omar",
]

# Single-letter prefixes: one search per letter covers partial first-name matches
FIRST_INITIALS = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

# Common *Indian / South Asian* given-name starting letters (not US SSA).
# Frequency-style order: A/S/R/P/M/K/V/N/B/D dominate Indian first names
# (Amit, Suresh, Raj, Priya, Mohan, Krishna, Vijay, Neha, Bharat, Deepak, …).
# Optional abbreviated mode — default remains full A–Z for max coverage.
FIRST_INITIALS_INDIAN = list("ASRPMKVNBD")
# Slightly wider Indian set (~top 14)
FIRST_INITIALS_INDIAN_WIDE = list("ASRPMKVNBDGJHT")

# Back-compat aliases (old "common" = US letters; now maps to Indian)
FIRST_INITIALS_COMMON = FIRST_INITIALS_INDIAN
FIRST_INITIALS_COMMON_WIDE = FIRST_INITIALS_INDIAN_WIDE


def first_initials_for_mode(mode: str) -> List[str]:
    """Resolve first-name letter set from first_mode string.

    Default is full A–Z. Abbreviated Indian-letter modes are optional.
    """
    m = (mode or "initials").lower().strip()
    if m in (
        "indian",
        "common",
        "common_letters",
        "most_common",
        "top10",
        "abbreviated",
        "abbrev",
    ):
        return list(FIRST_INITIALS_INDIAN)
    if m in (
        "indian_wide",
        "common_wide",
        "common14",
        "top14",
        "wide",
    ):
        return list(FIRST_INITIALS_INDIAN_WIDE)
    if m in ("full", "full_names"):
        return list(DEFAULT_FIRST_NAMES)
    # "initials" / "all" / A–Z (default — not abbreviated)
    return list(FIRST_INITIALS)


def is_abbreviated_first_mode(mode: str) -> bool:
    """True when first-letter set is the optional Indian abbreviated set."""
    m = (mode or "initials").lower().strip()
    return m in (
        "indian",
        "indian_wide",
        "common",
        "common_letters",
        "most_common",
        "top10",
        "common_wide",
        "common14",
        "top14",
        "wide",
        "abbreviated",
        "abbrev",
    )


# Most common *Indian* surname digraphs (frequency order from HC list).
# Abbreviated surname search uses only these letter combos — not every digraph
# from the full list and never brute-force AA–ZZ or US surname patterns.
INDIAN_LAST_DIGRAPHS_ABBREV = [
    "RA", "CH", "KA", "SA", "BA", "PA", "SH", "NA", "BH", "GA",
    "SU", "GO", "VE", "JA", "MA", "SE", "TA", "KU", "DE", "KO",
    "MU", "SI", "DA", "TH", "VA", "MO", "SR", "SO", "GH", "KR",
]  # top ~30
INDIAN_LAST_DIGRAPHS_WIDE = INDIAN_LAST_DIGRAPHS_ABBREV + [
    "WA", "MI", "AG", "RE", "AH", "VI", "BE", "PO", "PR", "LA",
    "HA", "KH", "ME", "GU", "AN", "AR", "AS", "AT", "DI", "DU",
]  # top ~50


def indian_surname_digraphs(
    surnames: Sequence[str] | None = None,
) -> Set[str]:
    """
    2-letter last-name prefixes that appear in Indian surnames.

    Used so abbreviated / compact last tokens are only combos that actually
    occur in Indian name data — never US-frequency or brute-force AA–ZZ.
    """
    if surnames is None:
        try:
            edb = get_ethnic_database()
            pool: Set[str] = set()
            pool.update(edb.indian_surnames or set())
            pool.update(edb.indian_high_confidence_surnames or set())
            for names in (edb.indian_surnames_by_group or {}).values():
                pool.update(names or set())
            surnames = sorted(pool)
        except Exception:
            surnames = [
                "Patel", "Shah", "Singh", "Kumar", "Gupta", "Sharma",
                "Reddy", "Nair", "Iyer", "Rao", "Das", "Banerjee",
                "Chatterjee", "Mukherjee", "Krishnan", "Menon", "Pillai",
            ]
    digs: Set[str] = set()
    for sn in surnames:
        al = _surname_alnum(sn)
        if len(al) >= 2:
            digs.add(al[:2].upper())
        elif len(al) == 1:
            digs.add(al.upper())
    return digs


def top_surname_digraphs(
    surnames: Sequence[str],
    limit: int = 30,
) -> List[str]:
    """Most frequent 2-letter last prefixes among *surnames* (frequency order)."""
    from collections import Counter

    counts: Counter = Counter()
    for sn in surnames:
        al = _surname_alnum(sn)
        if len(al) >= 2:
            counts[al[:2].upper()] += 1
        elif len(al) == 1:
            counts[al.upper()] += 1
    if limit <= 0:
        return [d for d, _ in counts.most_common()]
    return [d for d, _ in counts.most_common(int(limit))]


def abbreviated_last_digraphs_for_mode(mode: str) -> List[str]:
    """Static Indian-likely surname digraph set for abbreviated first modes."""
    m = (mode or "initials").lower().strip()
    if m in (
        "indian_wide",
        "common_wide",
        "common14",
        "top14",
        "wide",
    ):
        return list(INDIAN_LAST_DIGRAPHS_WIDE)
    # indian / common / abbreviated / top10
    return list(INDIAN_LAST_DIGRAPHS_ABBREV)

# NSOPW API: len(firstName) + len(lastName) >= 3 (verified live).
# Keep the floor at 3 so compact short partials maximize coverage per query
# (e.g. M+AH covers Ahmed/Ahmad). API alias/fuzzy extras are kept as scrape
# yield; list surnames are bucketed matched vs other in the builder.
MIN_COMBINED_NAME_LEN = 3

# Default rate limits (seconds / caps)
# Search hits Cloudflare on nsopw-api — keep higher.
# Report pages are per-jurisdiction and can be faster (HTML save is the same request).
DEFAULT_SEARCH_DELAY = 3.0
DEFAULT_REPORT_DELAY = 0.75
DEFAULT_MIN_SEARCH_INTERVAL = 2.0
DEFAULT_MIN_REPORT_INTERVAL = 0.25


def last_name_search_prefix(
    surname: str,
    first: str,
    min_combined: int = MIN_COMBINED_NAME_LEN,
) -> str:
    """
    Shortest last-name token valid with this first name under NSOPW's min length.

    Goal: fewest API queries for most coverage. With first="M" (1 char), last
    needs 2 chars → "AH" for Ahmed/Ahmad (one search covers both). With
    first="MO" (2), last needs 1 char → "A".
    """
    first_s = (first or "").strip()
    last_s = (surname or "").strip()
    if not last_s:
        return last_s
    need = max(1, int(min_combined) - len(first_s))
    if len(last_s) <= need:
        return last_s
    return last_s[:need]


def _surname_alnum(s: str) -> str:
    """Lowercase letters-only form for surname comparison (drops spaces/hyphens)."""
    return re.sub(r"[^a-z]", "", (s or "").strip().lower())


# Prefix-expand list surnames only at this length+ (Garcia→Garciaz).
# Shorter tokens (De, Das, John, del, Ali) are exact-match only so compact
# NSOPW queries do not bucket De-Vries/Delosantos/Johnson as ethnicity matches.
_MIN_PREFIX_EXPAND_LEN = 5


def last_matches_target_surnames(last_name: str, targets: Sequence[str]) -> bool:
    """
    True if hit last name is covered by any full list surname.

    Match rules (case-insensitive, hyphens/spaces ignored for comparison):
      - exact equality on the full last name, or
      - prefix expand only when the *list* surname is long enough
        (default ≥5 letters), e.g. Garciaz ≈ Garcia

    Short list names (De, John, Das, Ali, del, …) match **exactly only**.
    Otherwise De→Delosantos/De-Vries and John→Johnson false-positive as
    Indian (or other) ethnicity-list matches.
    """
    last = (last_name or "").strip().lower()
    if not last:
        return False
    last_compact = _surname_alnum(last)
    if not last_compact:
        return False

    for t in targets:
        tl = (t or "").strip().lower()
        if not tl:
            continue
        tl_compact = _surname_alnum(tl)
        if not tl_compact:
            continue
        # Exact full last name (raw or alphanumeric-compact)
        if last == tl or last_compact == tl_compact:
            return True
        # Prefix expand only for longer list surnames (never De/John/del/…)
        if len(tl_compact) >= _MIN_PREFIX_EXPAND_LEN and last_compact.startswith(tl_compact):
            return True
    return False


def compact_search_plan(
    surname_pairs: Sequence[Tuple[str, str]],
    firsts: Sequence[str],
    min_combined: int = MIN_COMBINED_NAME_LEN,
    allowed_last_prefixes: Optional[Set[str]] = None,
) -> List[Tuple[str, str, str, List[str]]]:
    """
    Collapse (first, full_surname) into unique (first, last_prefix) queries.

    Returns list of (first, last_prefix, eth_label, covered_full_surnames).
    When several list surnames share a short last prefix (Ahmed/Ahmad → AH),
    one NSOPW query covers them all.

    Last prefixes are always derived from the selected surnames (not brute-force
    AA–ZZ). If ``allowed_last_prefixes`` is set (e.g. Indian digraph whitelist),
    only prefixes in that set are kept — so abbreviated surname search only uses
    letter combos that appear in Indian names.
    """
    allow: Optional[Set[str]] = None
    if allowed_last_prefixes is not None:
        allow = {p.upper() for p in allowed_last_prefixes if p}

    # key: (first_norm, last_prefix_norm) -> first, last_prefix, eth, surnames
    first_disp: Dict[Tuple[str, str], str] = {}
    prefix_disp: Dict[Tuple[str, str], str] = {}
    eth_disp: Dict[Tuple[str, str], str] = {}
    covered: Dict[Tuple[str, str], Set[str]] = {}

    for surname, eth_label in surname_pairs:
        sn = (surname or "").strip()
        if not sn:
            continue
        eth = eth_label or ""
        for first in firsts:
            fn = (first or "").strip()
            if not fn:
                continue
            prefix = last_name_search_prefix(sn, fn, min_combined=min_combined)
            if len(fn) + len(prefix) < min_combined:
                prefix = sn
                if len(fn) + len(prefix) < min_combined:
                    continue
            pref_key = prefix.upper()
            # Whitelist: only Indian-likely (or other allowed) last letter combos
            if allow is not None and pref_key not in allow:
                # 1-letter last prefixes: allow if that letter starts any digraph
                if len(pref_key) == 1:
                    if not any(d.startswith(pref_key) for d in allow):
                        continue
                else:
                    continue
            key = (fn.upper(), pref_key)
            first_disp[key] = fn
            prefix_disp[key] = prefix
            if eth and not eth_disp.get(key):
                eth_disp[key] = eth
            elif key not in eth_disp:
                eth_disp[key] = eth
            covered.setdefault(key, set()).add(sn)

    plan: List[Tuple[str, str, str, List[str]]] = []
    for key in covered:
        plan.append(
            (
                first_disp[key],
                prefix_disp[key],
                eth_disp.get(key, ""),
                sorted(covered[key], key=str.lower),
            )
        )
    plan.sort(key=lambda t: (t[1].upper(), t[0].upper()))
    return plan


def estimate_compact_query_count(
    surname_pairs: Sequence[Tuple[str, str]],
    firsts: Sequence[str] | None = None,
    min_combined: int = MIN_COMBINED_NAME_LEN,
    allowed_last_prefixes: Optional[Set[str]] = None,
) -> int:
    """Estimate unique NSOPW queries after short-prefix collapse."""
    firsts = list(firsts) if firsts is not None else list(FIRST_INITIALS)
    return len(
        compact_search_plan(
            surname_pairs,
            firsts,
            min_combined=min_combined,
            allowed_last_prefixes=allowed_last_prefixes,
        )
    )


def describe_first_mode(mode: str) -> str:
    """Short human label for first-name + surname abbreviated strategy."""
    m = (mode or "initials").lower().strip()
    if m in (
        "indian",
        "common",
        "common_letters",
        "most_common",
        "top10",
        "abbreviated",
        "abbrev",
    ):
        return (
            f"abbreviated: Indian firsts {''.join(FIRST_INITIALS_INDIAN)} "
            f"({len(FIRST_INITIALS_INDIAN)}) + top {len(INDIAN_LAST_DIGRAPHS_ABBREV)} "
            f"surname digraphs"
        )
    if m in (
        "indian_wide",
        "common_wide",
        "common14",
        "top14",
        "wide",
    ):
        return (
            f"abbreviated wide: Indian firsts {''.join(FIRST_INITIALS_INDIAN_WIDE)} "
            f"({len(FIRST_INITIALS_INDIAN_WIDE)}) + top {len(INDIAN_LAST_DIGRAPHS_WIDE)} "
            f"surname digraphs"
        )
    if m in ("full", "full_names"):
        return f"full first names ({len(DEFAULT_FIRST_NAMES)})"
    return f"A–Z firsts ({len(FIRST_INITIALS)} letters) + all list surname digraphs"


def last_prefix_whitelist_for(
    ethnicity: str,
    surname_pairs: Sequence[Tuple[str, str]],
    *,
    abbreviated: bool,
    mode: str = "indian",
) -> Optional[Set[str]]:
    """
    When abbreviated mode is on, restrict last prefixes to the most common
    Indian-likely surname digraphs (first *and* last letters abbreviated).

    - Indian ethnicity lists: static top digraphs (INDIAN_LAST_DIGRAPHS_*)
      intersected with digraphs that appear in the selected surnames (so we
      only query combos present in the list).
    - Other ethnicities: top digraphs by frequency within the selected list
      (same abbreviated last-letter idea, ethnicity-local).

    Default (non-abbreviated): None → every digraph from selected surnames
    (still list-derived; never brute-force AA–ZZ).
    """
    if not abbreviated:
        return None

    selected = [s for s, _ in surname_pairs]
    selected_digs = indian_surname_digraphs(selected)
    if not selected_digs:
        return set()

    eth = (ethnicity or "").lower().strip()
    indianish = eth in (
        "indian",
        "indian/mena",
        "indian_mena",
        "mena",
        "arabic",
        "indian_high_confidence",
        "high_confidence_indian",
        "high-confidence indian",
        "indian_hc",
        "south_asian",
        "southasian",
    ) or eth.startswith("indian")

    if indianish:
        # Abbreviated: only the common Indian digraph set that also appear
        # in the selected surname list (covers both first- and last-letter cuts).
        seed = {d.upper() for d in abbreviated_last_digraphs_for_mode(mode)}
        # Always keep 1-letter starters of allowed digraphs for longer firsts
        allowed = selected_digs & seed
        # If intersection is empty (tiny custom list), fall back to top-N of list
        if not allowed:
            n = len(INDIAN_LAST_DIGRAPHS_WIDE) if "wide" in (mode or "") else len(
                INDIAN_LAST_DIGRAPHS_ABBREV
            )
            allowed = set(top_surname_digraphs(selected, limit=n))
        return allowed

    # Non-Indian ethnicity + abbreviated first mode: still abbreviate last
    # prefixes by taking top digraphs from the selected list only.
    n = (
        len(INDIAN_LAST_DIGRAPHS_WIDE)
        if (mode or "").lower().strip()
        in ("indian_wide", "common_wide", "common14", "top14", "wide")
        else len(INDIAN_LAST_DIGRAPHS_ABBREV)
    )
    return set(top_surname_digraphs(selected, limit=n))
