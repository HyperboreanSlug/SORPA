"""Curated surname lists for ethnicity filters and misclass candidate pulls."""
from __future__ import annotations

from typing import List, Optional


def unique_surnames(names) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for n in names or []:
        k = (n or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append((n or "").strip())
    return out


def surnames_for_ethnicity_filter(ethnic_db, eth: str) -> Optional[List[str]]:
    """Curated surname list for a filter key, or None for full-table scan."""
    key = (eth or "").strip().lower()
    if not key or key == "all":
        return None

    from scraper.searcher_race import (
        ASIAN_FILTERS,
        BLACK_FILTERS,
        INDIAN_MENA_MERGED_FILTERS,
        INDIAN_ONLY_FILTERS,
        MENA_ONLY_FILTERS,
        NON_WHITE_FILTERS,
        WHITE_FILTERS,
    )

    db = ethnic_db
    if key in NON_WHITE_FILTERS:
        pool: list = []
        pool.extend(db.hispanic_surnames or [])
        for names in (db.asian_surnames or {}).values():
            pool.extend(names)
        pool.extend(db.indian_surnames or [])
        pool.extend(db.indian_high_confidence_surnames or [])
        pool.extend(db.arabic_surnames or [])
        pool.extend(db.african_american_surnames or [])
        for names in (db.african_surnames or {}).values():
            pool.extend(names)
        pool.extend(db.native_american_surnames or [])
        return unique_surnames(pool)
    if key in WHITE_FILTERS:
        pool = list(db.jewish_surnames or [])
        pool.extend(db.portuguese_surnames or [])
        for names in (db.european_surnames or {}).values():
            pool.extend(names)
        return unique_surnames(pool)
    if key in BLACK_FILTERS:
        pool = list(db.african_american_surnames or [])
        for names in (db.african_surnames or {}).values():
            pool.extend(names)
        return unique_surnames(pool)
    if key in INDIAN_MENA_MERGED_FILTERS:
        return unique_surnames(
            list(db.indian_surnames or [])
            + list(db.indian_high_confidence_surnames or [])
            + list(db.arabic_surnames or [])
        )
    if key in INDIAN_ONLY_FILTERS:
        return unique_surnames(
            list(db.indian_surnames or [])
            + list(db.indian_high_confidence_surnames or [])
        )
    if key in MENA_ONLY_FILTERS:
        return unique_surnames(list(db.arabic_surnames or []))
    if key in ASIAN_FILTERS or key == "asian":
        pool = []
        for names in (db.asian_surnames or {}).values():
            pool.extend(names)
        return unique_surnames(pool)
    if key == "hispanic":
        return unique_surnames(list(db.hispanic_surnames or []))
    if key == "african_american":
        return unique_surnames(list(db.african_american_surnames or []))
    if key == "jewish":
        return unique_surnames(list(db.jewish_surnames or []))
    if key == "portuguese":
        return unique_surnames(list(db.portuguese_surnames or []))
    if key == "native_american":
        return unique_surnames(list(db.native_american_surnames or []))
    if key == "african":
        pool = []
        for names in (db.african_surnames or {}).values():
            pool.extend(names)
        return unique_surnames(pool)
    if key == "european":
        pool = []
        for names in (db.european_surnames or {}).values():
            pool.extend(names)
        return unique_surnames(pool)
    return None
