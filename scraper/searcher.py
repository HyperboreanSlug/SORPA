"""Sex offender search + misclassification (composed)."""
from __future__ import annotations

from typing import Optional

from scraper.ethnicity_review import ethnicity_review_verdict  # noqa: F401
from scraper.searcher_race import (  # noqa: F401
    SearchResults,
    Misclassification,
    _ETHNICITY_COMPATIBLE_RACES,
    _RACE_ALIASES,
    _canonical_race_key,
    format_race_label,
    _ethnicity_family,
    _is_other_or_other_asian,
    _has_hispanic_ethnicity,
    _is_compatible,
    _last_name_from_record,
    _first_name_from_record,
    _middle_name_from_record,
    recorded_ethnicity_from_record,
)

from scraper.searcher_init import SearcherInitMixin
from scraper.searcher_core import SearcherCoreMixin
from scraper.searcher_analyze import SearcherAnalyzeMixin
from scraper.searcher_export import SearcherExportMixin


class SexOffenderSearcher(
    SearcherInitMixin,
    SearcherCoreMixin,
    SearcherAnalyzeMixin,
    SearcherExportMixin,
):
    """Search offenders and flag surname/race mismatches."""


def get_searcher(db_path: Optional[str] = None) -> SexOffenderSearcher:
    """Get a searcher instance."""
    return SexOffenderSearcher(db_path=db_path)


__all__ = [
    "SexOffenderSearcher",
    "get_searcher",
    "ethnicity_review_verdict",
    "format_race_label",
    "Misclassification",
    "SearchResults",
]

