from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

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
)



class SearcherAnalyzeMixin:
    def analyze_ethnicities(
        self,
        min_confidence: float = 0.5,
        limit: int = 10000,
        ethnicity_filter: Optional[str] = None,
        return_base_count: bool = False,
    ):
        """Find potential race/ethnicity misclassifications.

        ethnicity_filter: optional family key such as 'hispanic', 'asian',
        'indian'/'indian/mena' (includes high-confidence + MENA),
        'african_american'. When set, only that family is considered.

        If return_base_count is True, returns
        ``(misclassifications, base_count)`` where *base_count* is how many
        scanned offenders matched the selected ethnicity at min_confidence
        (compatible + mismatched). Rate of misclassification among the
        selected ethnicity is misclass / base_count.
        """
        # Stream rows instead of materializing the whole table as a list first
        # (faster, lower memory; same misclassification rules).
        # When a scan cap is set, walk newest ids first so recent scrapes/imports
        # are checked — ASC + LIMIT used to miss brand-new high-id rows.
        misclassifications: List[Misclassification] = []
        base_count = 0
        filter_key = (ethnicity_filter or "").strip().lower() or None
        # indian / indian/mena / mena / arabic / legacy HC → one merged family
        # (high-confidence surnames are included, not a separate filter)
        if filter_key in (
            "indian",
            "indian/mena",
            "indian_mena",
            "mena",
            "arabic",
            "middle_eastern",
            "middle eastern",
            "indian_high_confidence",
            "high_confidence_indian",
            "high-confidence indian",
            "indian_hc",
        ):
            family_filter = "indian"
        else:
            family_filter = filter_key
        scan_limit = None if limit is None or int(limit) <= 0 else int(limit)
        # Cap set → prefer newest; unlimited → full table ASC is fine
        newest_first = bool(scan_limit)

        for record in self.db.iter_offenders(
            limit=scan_limit, newest_first=newest_first
        ):
            last_name = _last_name_from_record(record)
            first_name = _first_name_from_record(record)
            middle_name = _middle_name_from_record(record)
            recorded_race = (record.get("race") or "").strip()
            recorded_ethnicity = (
                record.get("ethnicity")
                or record.get("Ethnicity")
                or ""
            )
            if isinstance(recorded_ethnicity, str):
                recorded_ethnicity = recorded_ethnicity.strip()
            else:
                recorded_ethnicity = str(recorded_ethnicity or "").strip()

            if not last_name:
                continue

            likely_eth, confidence, matching_names = self.ethnic_db.classify_by_name(
                last_name,
                first_name=first_name or None,
                middle_name=middle_name or None,
            )

            if confidence < min_confidence or likely_eth == "Unknown":
                continue

            family = _ethnicity_family(likely_eth)
            if family_filter and family != family_filter:
                continue

            # Matched selected ethnicity at threshold
            base_count += 1

            if _is_compatible(
                likely_eth, recorded_race, recorded_ethnicity=recorded_ethnicity or None
            ):
                continue

            misclassifications.append(Misclassification(
                record=record,
                # Canonical display (White/WHITE → White) for stats + table
                expected_race=format_race_label(recorded_race) if recorded_race else "—",
                likely_ethnicity=likely_eth,
                confidence=confidence,
                matching_names=matching_names,
            ))

        misclassifications.sort(key=lambda m: m.confidence, reverse=True)
        if return_base_count:
            return misclassifications, base_count
        return misclassifications


    def find_hispanic_misclassifications(
        self,
        min_confidence: float = 0.5,
        limit: int = 10000
    ) -> List[Misclassification]:
        """Find records with Hispanic names classified as non-Hispanic."""
        return self.analyze_ethnicities(
            min_confidence=min_confidence, limit=limit, ethnicity_filter="hispanic"
        )


    def find_asian_misclassifications(
        self,
        min_confidence: float = 0.5,
        limit: int = 10000
    ) -> List[Misclassification]:
        """Find records with Asian names classified as non-Asian."""
        return self.analyze_ethnicities(
            min_confidence=min_confidence, limit=limit, ethnicity_filter="asian"
        )


    def find_african_american_misclassifications(
        self,
        min_confidence: float = 0.5,
        limit: int = 10000
    ) -> List[Misclassification]:
        """Find records with African-American names classified as non-Black."""
        return self.analyze_ethnicities(
            min_confidence=min_confidence, limit=limit, ethnicity_filter="african_american"
        )


    def filter_by_hispanic_names(
        self,
        min_confidence: float = 0.5,
        limit: int = 10000
    ) -> List[Dict[str, Any]]:
        """Find records with Hispanic surnames."""
        return self._filter_by_ethnic_name("hispanic", min_confidence=min_confidence, limit=limit)


    def filter_by_asian_names(
        self,
        min_confidence: float = 0.5,
        limit: int = 10000
    ) -> List[Dict[str, Any]]:
        """Find records with Asian surnames."""
        return self._filter_by_ethnic_name("asian", min_confidence=min_confidence, limit=limit)


    def filter_by_african_american_names(
        self,
        min_confidence: float = 0.5,
        limit: int = 10000
    ) -> List[Dict[str, Any]]:
        """Find records with African-American surnames."""
        return self._filter_by_ethnic_name("african_american", min_confidence=min_confidence, limit=limit)


    def _filter_by_ethnic_name(
        self,
        ethnicity: str,
        min_confidence: float = 0.5,
        limit: int = 10000
    ) -> List[Dict[str, Any]]:
        """Generic filter by ethnicity from name."""
        records = self.search_all(limit=limit)
        filtered = []
        target = ethnicity.strip().lower()

        for record in records:
            last_name = _last_name_from_record(record)
            if not last_name:
                continue

            first_name = _first_name_from_record(record)
            middle_name = _middle_name_from_record(record)
            likely_eth, confidence, _ = self.ethnic_db.classify_by_name(
                last_name,
                first_name=first_name or None,
                middle_name=middle_name or None,
            )
            if confidence < min_confidence or likely_eth == "Unknown":
                continue

            if _ethnicity_family(likely_eth) == target:
                filtered.append(record)

        return filtered


