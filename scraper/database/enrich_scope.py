"""Scope filters for report enrichment and requeue operations."""

from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

from scraper.database.sources import infer_source_type, parse_sources

if TYPE_CHECKING:
    from scraper.ethnic_names import EthnicNameDatabase

# enrich_scope / source_scope values
SCOPE_ALL = "all"
SCOPE_EXTERNAL = "external_imports"
SCOPE_NSOPW = "nsopw"
SCOPE_ETHNICITY_MATCH = "ethnicity_match"

BULK_SOURCE_TYPES = frozenset(
    {"csv_bulk", "bulk", "direct", "direct_import", "scrape_direct"}
)
NSOPW_SOURCE_TYPES = frozenset({"nsopw", "nsopw_search", "nsopw_report"})
BULK_FLAG_MARKERS = ("tx_bulk", "csv_bulk", "bulk_import", "direct_import")


def _ethnicity_family(likely_ethnicity: str) -> str:
    """Normalize a classify_by_name label (delegates to searcher_race)."""
    from scraper.searcher_race import _ethnicity_family as _fam

    return _fam(likely_ethnicity)


def record_is_external_import(record: Dict[str, Any]) -> bool:
    """
    True when the record originated from a bulk/direct CSV import.

    Covers state dump imports (TX BCP, FL/GA bulk CSV, etc.), not rows that
    were first discovered via NSOPW name search.
    """
    if not record:
        return False
    flags = str(record.get("flags") or "").lower()
    if any(m in flags for m in BULK_FLAG_MARKERS):
        return True

    sources = parse_sources(record.get("sources_json"))
    if sources:
        types = {(s.get("type") or "").strip().lower() for s in sources if s}
        if types & BULK_SOURCE_TYPES:
            # Bulk contribution present — external unless *only* NSOPW-tagged
            if not types or types <= NSOPW_SOURCE_TYPES:
                return False
            return True

    if "nsopw" in flags:
        return False

    st, _, _ = infer_source_type(record)
    return st == "csv_bulk"


def record_is_nsopw_scraped(record: Dict[str, Any]) -> bool:
    """True when the record was discovered via NSOPW search (not bulk import)."""
    if not record:
        return False
    flags = str(record.get("flags") or "").lower()
    if "nsopw" in flags:
        return True
    if record.get("nsopw_ethnicity_match") is not None:
        return True
    if (record.get("nsopw_result_bucket") or "").strip():
        return True

    sources = parse_sources(record.get("sources_json"))
    if sources:
        types = {(s.get("type") or "").strip().lower() for s in sources if s}
        if types & NSOPW_SOURCE_TYPES:
            return True

    st, origin, _ = infer_source_type(record)
    return st in NSOPW_SOURCE_TYPES or origin == "nsopw"


def passes_source_scope(record: Dict[str, Any], scope: Optional[str]) -> bool:
    """Apply a source_scope filter (all / external_imports / nsopw)."""
    key = (scope or SCOPE_ALL).strip().lower()
    if key in (SCOPE_ALL, "", "any"):
        return True
    if key in (SCOPE_EXTERNAL, "external", "direct_import", "bulk", "csv", "import"):
        return record_is_external_import(record)
    if key in (SCOPE_NSOPW, "scraped", "nsopw_scraped"):
        return record_is_nsopw_scraped(record)
    return True


def record_matches_ethnicity_classifier(
    record: Dict[str, Any],
    ethnicity_filter: Optional[str],
    *,
    min_confidence: float = 0.5,
    ethnic_db: Optional["EthnicNameDatabase"] = None,
) -> bool:
    """
    True when the offender's name classifies into *ethnicity_filter*.

    Uses the same keys as Misclassify → Analyze: hispanic, asian, indian,
    mena, indian/mena (merged), african_american, …. ``all`` / empty matches
    every record.
    """
    filt = (ethnicity_filter or "").strip().lower()
    if not filt or filt == "all":
        return True
    if not ethnic_db:
        # Fail closed: never silently ignore a requested ethnicity filter
        return False

    last = (record.get("last_name") or "").strip()
    if not last:
        full = (record.get("full_name") or "").strip()
        parts = full.replace(",", " ").split()
        if len(parts) >= 2:
            last = parts[-1]
    if not last:
        return False

    first = (record.get("first_name") or "").strip() or None
    middle = (record.get("middle_name") or "").strip() or None
    likely_eth, confidence, _names = ethnic_db.classify_by_name(
        last, first_name=first, middle_name=middle
    )
    if confidence < float(min_confidence) or likely_eth == "Unknown":
        return False

    from scraper.searcher_race import ethnicity_filter_matches

    family = _ethnicity_family(likely_eth)
    return ethnicity_filter_matches(family, filt)


def filter_records_for_enrich(
    records: list,
    *,
    source_scope: Optional[str] = SCOPE_ALL,
    ethnicity_filter: Optional[str] = None,
    min_confidence: float = 0.5,
    ethnic_db: Optional["EthnicNameDatabase"] = None,
) -> tuple[list, int]:
    """
    Return (matching_records, skipped_count) after scope filters.

    Used by requeue / enrich_misclassified before processing.
    """
    out = []
    skipped = 0
    for rec in records or []:
        if not isinstance(rec, dict):
            skipped += 1
            continue
        if not passes_source_scope(rec, source_scope):
            skipped += 1
            continue
        if not record_matches_ethnicity_classifier(
            rec,
            ethnicity_filter,
            min_confidence=min_confidence,
            ethnic_db=ethnic_db,
        ):
            skipped += 1
            continue
        out.append(rec)
    return out, skipped
