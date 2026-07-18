from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from scraper.searcher_race import (
    Misclassification,
    format_race_label,
    _ethnicity_family,
    ethnicity_filter_matches,
    _is_compatible,
    _last_name_from_record,
    _first_name_from_record,
    _middle_name_from_record,
    recorded_ethnicity_from_record,
)
from scraper.searcher_surnames import surnames_for_ethnicity_filter


class SearcherAnalyzeMixin:
    def _iter_analyze_records(
        self,
        filter_key: Optional[str],
        scan_limit: Optional[int],
    ) -> Iterable[Dict[str, Any]]:
        """Yield candidate rows for misclass analysis.

        Specific ethnicity (indian, asian, …) → curated surname list pull from
        the full DB. Avoids "newest N of 300k" missing sparse ethnicities.
        """
        surnames = (
            surnames_for_ethnicity_filter(self.ethnic_db, filter_key)
            if filter_key
            else None
        )
        if surnames is not None:
            lim = int(scan_limit or 0)
            if hasattr(self.db, "search_by_surname_list"):
                return self.db.search_by_surname_list(surnames, limit=lim)
            res = self.search_by_surname_ethnicity(
                filter_key or "", limit=lim if lim > 0 else 10_000_000
            )
            return res.records

        return self.db.iter_offenders(
            limit=scan_limit, newest_first=bool(scan_limit)
        )

    def analyze_ethnicities(
        self,
        min_confidence: float = 0.5,
        limit: int = 0,
        ethnicity_filter: Optional[str] = None,
        return_base_count: bool = False,
    ):
        """Find potential race/ethnicity misclassifications.

        ethnicity_filter: 'hispanic', 'asian', 'indian', 'mena', etc.

        When a specific ethnicity is selected, candidates are all DB rows
        matching the curated surname list — not a random newest-*limit* slice
        of the whole table (which left Indian×White with only a few hits).

        *limit* ``0`` = unlimited. Positive caps candidates/scan rows.
        """
        misclassifications: List[Misclassification] = []
        base_count = 0
        filter_key = (ethnicity_filter or "").strip().lower() or None
        if filter_key == "all":
            filter_key = None
        scan_limit = None if limit is None or int(limit) <= 0 else int(limit)

        for record in self._iter_analyze_records(filter_key, scan_limit):
            last_name = _last_name_from_record(record)
            first_name = _first_name_from_record(record)
            middle_name = _middle_name_from_record(record)
            recorded_race = (record.get("race") or "").strip()
            # Top-level column first; fall back to sources_json (TX HISPANIC, etc.)
            recorded_ethnicity = recorded_ethnicity_from_record(record)

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
            if not ethnicity_filter_matches(family, filter_key):
                continue

            base_count += 1
            if _is_compatible(
                likely_eth,
                recorded_race,
                recorded_ethnicity=recorded_ethnicity or None,
                last_name=last_name,
            ):
                continue

            from scraper.searcher_appearance import apply_appearance_signals

            confidence, matching_names, _meta = apply_appearance_signals(
                record,
                likely_eth,
                confidence,
                matching_names,
                family=family,
            )
            if confidence < min_confidence:
                continue

            misclassifications.append(
                Misclassification(
                    record=record,
                    expected_race=format_race_label(recorded_race)
                    if recorded_race
                    else "—",
                    likely_ethnicity=likely_eth,
                    confidence=confidence,
                    matching_names=matching_names,
                )
            )

        misclassifications.sort(key=lambda m: m.confidence, reverse=True)
        if return_base_count:
            return misclassifications, base_count
        return misclassifications

    def find_hispanic_misclassifications(
        self, min_confidence: float = 0.5, limit: int = 0
    ) -> List[Misclassification]:
        return self.analyze_ethnicities(
            min_confidence=min_confidence, limit=limit, ethnicity_filter="hispanic"
        )

    def find_asian_misclassifications(
        self, min_confidence: float = 0.5, limit: int = 0
    ) -> List[Misclassification]:
        return self.analyze_ethnicities(
            min_confidence=min_confidence, limit=limit, ethnicity_filter="asian"
        )

    def find_african_american_misclassifications(
        self, min_confidence: float = 0.5, limit: int = 0
    ) -> List[Misclassification]:
        return self.analyze_ethnicities(
            min_confidence=min_confidence,
            limit=limit,
            ethnicity_filter="african_american",
        )

    def filter_by_hispanic_names(
        self, min_confidence: float = 0.5, limit: int = 10000
    ) -> List[Dict[str, Any]]:
        return self._filter_by_ethnic_name(
            "hispanic", min_confidence=min_confidence, limit=limit
        )

    def filter_by_asian_names(
        self, min_confidence: float = 0.5, limit: int = 10000
    ) -> List[Dict[str, Any]]:
        return self._filter_by_ethnic_name(
            "asian", min_confidence=min_confidence, limit=limit
        )

    def filter_by_african_american_names(
        self, min_confidence: float = 0.5, limit: int = 10000
    ) -> List[Dict[str, Any]]:
        return self._filter_by_ethnic_name(
            "african_american", min_confidence=min_confidence, limit=limit
        )

    def _filter_by_ethnic_name(
        self,
        ethnicity: str,
        min_confidence: float = 0.5,
        limit: int = 10000,
    ) -> List[Dict[str, Any]]:
        surnames = surnames_for_ethnicity_filter(self.ethnic_db, ethnicity)
        if surnames is not None and hasattr(self.db, "search_by_surname_list"):
            records = self.db.search_by_surname_list(
                surnames, limit=0 if not limit else int(limit)
            )
        else:
            records = self.search_all(limit=limit)

        filtered: List[Dict[str, Any]] = []
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
            family = _ethnicity_family(likely_eth)
            if not ethnicity_filter_matches(family, target):
                continue
            filtered.append(record)
            if limit and len(filtered) >= int(limit):
                break
        return filtered
