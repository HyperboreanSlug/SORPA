"""Search and filter engine for sex offender records."""

import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from .database import Database
from .ethnic_names import EthnicNameDatabase


@dataclass
class SearchResults:
    """Container for search results with metadata."""
    records: List[Dict[str, Any]]
    total_count: int
    query_time_ms: float
    filters_applied: Dict[str, str] = field(default_factory=dict)


@dataclass
class Misclassification:
    """A record that may have been misclassified by race/ethnicity."""
    record: Dict[str, Any]
    expected_race: str
    likely_ethnicity: str
    confidence: float
    matching_names: List[str] = field(default_factory=list)


# Map likely-ethnicity labels to race strings that are considered a match
# (i.e. not a misclassification when the recorded race is one of these).
_ETHNICITY_COMPATIBLE_RACES = {
    "hispanic": {"HISPANIC", "LATINO", "LATINA", "LATINX", "H", "WHITE HISPANIC"},
    "asian": {
        "ASIAN", "ASIAN / PACIFIC ISLANDER", "ASIAN/PACIFIC ISLANDER",
        "PACIFIC ISLANDER", "A", "API", "CHINESE", "KOREAN", "JAPANESE",
        "VIETNAMESE", "FILIPINO",
    },
    # South Asian / Indian subcontinent — often coded Asian or Other on registries
    "indian": {
        "ASIAN", "ASIAN / PACIFIC ISLANDER", "ASIAN/PACIFIC ISLANDER",
        "ASIAN INDIAN", "EAST INDIAN", "INDIAN", "SOUTH ASIAN",
        "A", "API", "OTHER", "UNKNOWN",
    },
    "african_american": {
        "BLACK", "AFRICAN AMERICAN", "AFRICAN-AMERICAN", "B", "BLACK OR AFRICAN AMERICAN",
    },
    "native_american": {
        "NATIVE AMERICAN", "AMERICAN INDIAN", "AMERICAN INDIAN OR ALASKA NATIVE",
        "ALASKA NATIVE", "I", "NATIVE",
    },
    "arabic": {"WHITE", "OTHER", "MIDDLE EASTERN", "ARAB"},
    "jewish": {"WHITE", "OTHER"},
    "portuguese": {"WHITE", "HISPANIC", "OTHER"},
    "european": {"WHITE", "CAUCASIAN", "W"},
    "african": {
        "BLACK", "AFRICAN AMERICAN", "AFRICAN-AMERICAN", "B", "BLACK OR AFRICAN AMERICAN",
    },
}


def _ethnicity_family(likely_ethnicity: str) -> str:
    """Normalize a classify_by_name label to a coarse family key."""
    eth = (likely_ethnicity or "").strip().lower()
    if eth == "indian" or eth.startswith("indian"):
        return "indian"
    if eth.startswith("asian"):
        return "asian"
    if eth.startswith("european"):
        return "european"
    if eth.startswith("african (") or eth == "african":
        return "african"
    if eth in ("african american", "african-american"):
        return "african_american"
    if eth in ("native american", "native-american"):
        return "native_american"
    if eth == "hispanic":
        return "hispanic"
    if eth == "jewish":
        return "jewish"
    if eth == "portuguese":
        return "portuguese"
    if eth == "arabic":
        return "arabic"
    return eth.replace(" ", "_")


def _is_compatible(likely_ethnicity: str, recorded_race: str) -> bool:
    """Return True if recorded race is consistent with the name-based ethnicity."""
    if not recorded_race or not likely_ethnicity or likely_ethnicity == "Unknown":
        return True
    family = _ethnicity_family(likely_ethnicity)
    race = recorded_race.strip().upper()
    compatible = _ETHNICITY_COMPATIBLE_RACES.get(family)
    if not compatible:
        # Unknown family: treat exact string equality (case-insensitive) as match
        return race == likely_ethnicity.strip().upper()
    return race in compatible


def _last_name_from_record(record: Dict[str, Any]) -> str:
    last = (record.get("last_name") or record.get("LastName") or "").strip()
    if last:
        return last
    full = (record.get("full_name") or record.get("Name") or "").strip()
    if full:
        parts = full.split()
        if parts:
            return parts[-1]
    return ""


class SexOffenderSearcher:
    """Search and filter sex offender records with misclassification detection."""

    def __init__(self, db_path: Optional[str] = None):
        self.db = Database(db_path)
        self.ethnic_db = EthnicNameDatabase()

    # ---- Search operations ----

    def search_by_name(
        self,
        name: str,
        state: Optional[str] = None,
        race: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> SearchResults:
        """Search offenders by name."""
        start = time.time()

        records = self.db.search_by_name(name, state=state, race=race, limit=limit, offset=offset)
        total = len(records) if offset == 0 else self.db.get_total_count()

        elapsed_ms = (time.time() - start) * 1000

        return SearchResults(
            records=records,
            total_count=total,
            query_time_ms=elapsed_ms,
            filters_applied={"name": name, "state": state or "", "race": race or ""}
        )

    def search_by_race(
        self,
        race: str,
        state: Optional[str] = None,
        limit: int = 1000
    ) -> SearchResults:
        """Search offenders by race."""
        start = time.time()

        records = self.db.search_by_race(race, state=state, limit=limit)
        elapsed_ms = (time.time() - start) * 1000

        return SearchResults(
            records=records,
            total_count=len(records),
            query_time_ms=elapsed_ms,
            filters_applied={"race": race, "state": state or ""}
        )

    def search_by_state(
        self,
        state: str,
        limit: int = 1000
    ) -> SearchResults:
        """Search offenders by state."""
        start = time.time()

        records = self.db.search_by_state(state, limit=limit)
        elapsed_ms = (time.time() - start) * 1000

        return SearchResults(
            records=records,
            total_count=len(records),
            query_time_ms=elapsed_ms,
            filters_applied={"state": state}
        )

    def search_all(self, limit: int = 10000) -> List[Dict[str, Any]]:
        """Get records across all states (paginated)."""
        return self.db.search_by_state("ALL", limit=limit)

    # ---- Ethnicity analysis ----

    def analyze_ethnicities(
        self,
        min_confidence: float = 0.5,
        limit: int = 10000,
        ethnicity_filter: Optional[str] = None,
    ) -> List[Misclassification]:
        """Find potential race/ethnicity misclassifications.

        ethnicity_filter: optional family key such as 'hispanic', 'asian',
        'african_american'. When set, only that family is considered.
        """
        records = self.search_all(limit=limit)
        misclassifications: List[Misclassification] = []
        filter_key = (ethnicity_filter or "").strip().lower() or None

        for record in records:
            last_name = _last_name_from_record(record)
            recorded_race = (record.get("race") or "").strip()

            if not last_name:
                continue

            likely_eth, confidence, matching_names = self.ethnic_db.classify_by_name(last_name)

            if confidence < min_confidence or likely_eth == "Unknown":
                continue

            family = _ethnicity_family(likely_eth)
            if filter_key and family != filter_key:
                continue

            if _is_compatible(likely_eth, recorded_race):
                continue

            misclassifications.append(Misclassification(
                record=record,
                expected_race=recorded_race or "N/A",
                likely_ethnicity=likely_eth,
                confidence=confidence,
                matching_names=matching_names,
            ))

        misclassifications.sort(key=lambda m: m.confidence, reverse=True)
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

    # ---- Ethnic name filtering ----

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

            likely_eth, confidence, _ = self.ethnic_db.classify_by_name(last_name)
            if confidence < min_confidence or likely_eth == "Unknown":
                continue

            if _ethnicity_family(likely_eth) == target:
                filtered.append(record)

        return filtered

    # ---- Statistics ----

    def get_race_distribution(self) -> List[Dict[str, Any]]:
        """Get count of offenders by race."""
        return self.db.get_race_distribution()

    def get_state_distribution(self) -> List[Dict[str, Any]]:
        """Get count of offenders by state."""
        return self.db.get_state_distribution()

    def get_total_count(self) -> int:
        """Get total number of records."""
        return self.db.get_total_count()

    # ---- Export ----

    def export_misclassifications(
        self,
        output_path: str,
        min_confidence: float = 0.5,
        limit: int = 10000,
        ethnicity_filter: Optional[str] = None,
    ) -> int:
        """Export misclassified records to CSV."""
        import csv

        misclassifications = self.analyze_ethnicities(
            min_confidence=min_confidence,
            limit=limit,
            ethnicity_filter=ethnicity_filter,
        )

        if not misclassifications:
            return 0

        headers = [
            "first_name", "last_name", "full_name", "race", "likely_ethnicity",
            "confidence", "matching_names", "state", "county", "address",
            "age", "gender", "offense_type"
        ]

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()

            for mc in misclassifications:
                row = {
                    "first_name": mc.record.get("first_name"),
                    "last_name": mc.record.get("last_name"),
                    "full_name": mc.record.get("full_name"),
                    "race": mc.expected_race,
                    "likely_ethnicity": mc.likely_ethnicity,
                    "confidence": round(mc.confidence, 3),
                    "matching_names": "; ".join(mc.matching_names),
                    "state": mc.record.get("state"),
                    "county": mc.record.get("county"),
                    "address": mc.record.get("address"),
                    "age": mc.record.get("age"),
                    "gender": mc.record.get("gender"),
                    "offense_type": mc.record.get("offense_type"),
                }
                writer.writerow(row)

        return len(misclassifications)

    def export_filtered(
        self,
        output_path: str,
        filters: Dict[str, Any]
    ) -> int:
        """Export filtered records to CSV."""
        return self.db.export_to_csv(output_path, filters=filters)

    # ---- Cleanup ----

    def close(self):
        """Close the database connection."""
        self.db.close()


def get_searcher(db_path: Optional[str] = None) -> SexOffenderSearcher:
    """Get a searcher instance."""
    return SexOffenderSearcher(db_path=db_path)
