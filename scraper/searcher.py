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
# Keys are *canonical* race codes from _canonical_race_key().
_ETHNICITY_COMPATIBLE_RACES = {
    "hispanic": {"HISPANIC", "LATINO", "LATINA", "LATINX", "H", "WHITE HISPANIC"},
    "asian": {
        "ASIAN", "ASIAN / PACIFIC ISLANDER", "ASIAN/PACIFIC ISLANDER",
        "PACIFIC ISLANDER", "A", "API", "CHINESE", "KOREAN", "JAPANESE",
        "VIETNAMESE", "FILIPINO", "OTHER ASIAN",
    },
    # South Asian / Indian — registries often use Asian, Other, or Other Asian
    "indian": {
        "ASIAN", "ASIAN / PACIFIC ISLANDER", "ASIAN/PACIFIC ISLANDER",
        "ASIAN INDIAN", "EAST INDIAN", "INDIAN", "SOUTH ASIAN",
        "A", "API", "OTHER", "OTHER ASIAN", "UNKNOWN", "U",
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

# Collapse case/spelling variants so stats and comparisons share one bucket.
_RACE_ALIASES = {
    "W": "WHITE",
    "CAUCASIAN": "WHITE",
    "CAUCASION": "WHITE",  # common misspelling
    "WHITE": "WHITE",
    "B": "BLACK",
    "BLACK": "BLACK",
    "AFRICAN AMERICAN": "BLACK",
    "AFRICAN-AMERICAN": "BLACK",
    "BLACK OR AFRICAN AMERICAN": "BLACK",
    "H": "HISPANIC",
    "LATINO": "HISPANIC",
    "LATINA": "HISPANIC",
    "LATINX": "HISPANIC",
    "HISPANIC": "HISPANIC",
    "A": "ASIAN",
    "API": "ASIAN",
    "ASIAN": "ASIAN",
    "U": "UNKNOWN",
    "UNK": "UNKNOWN",
    "UNKNOWN": "UNKNOWN",
    "N/A": "UNKNOWN",
    "NA": "UNKNOWN",
    "NONE": "UNKNOWN",
    "NULL": "UNKNOWN",
    "": "UNKNOWN",
}


def _canonical_race_key(recorded_race: str) -> str:
    """
    Normalize recorded race for comparison and grouping.

    Merges case variants (White / WHITE → WHITE) and common aliases.
    """
    raw = (recorded_race or "").strip()
    if not raw or raw.upper() in ("N/A", "NA"):
        return "UNKNOWN"
    # collapse whitespace and punctuation noise for matching
    r = " ".join(raw.upper().replace("_", " ").replace("-", " ").split())
    r = r.replace(" / ", "/").replace("/ ", "/").replace(" /", "/")
    # "OTHER-ASIAN", "OTHER / ASIAN" → form used below
    r_spaced = r.replace("/", " ")
    r_spaced = " ".join(r_spaced.split())

    if r_spaced in _RACE_ALIASES:
        return _RACE_ALIASES[r_spaced]

    # Other Asian variants (Indian-friendly bucket)
    if r_spaced in ("OTHER ASIAN", "ASIAN OTHER", "OTHER ASIAN PACIFIC ISLANDER"):
        return "OTHER ASIAN"
    if "OTHER" in r_spaced and "ASIAN" in r_spaced:
        return "OTHER ASIAN"

    # White Hispanic kept distinct from White
    if "HISPANIC" in r_spaced and "WHITE" in r_spaced:
        return "WHITE HISPANIC"
    if r_spaced.startswith("WHITE") or r_spaced.endswith(" WHITE"):
        return "WHITE"

    if r_spaced in ("OTHER", "OTHER RACE", "OTHER RACES", "OT"):
        return "OTHER"

    # Asian Pacific Islander phrasing
    if "ASIAN" in r_spaced and "PACIFIC" in r_spaced:
        return "ASIAN / PACIFIC ISLANDER"
    if r_spaced in ("PACIFIC ISLANDER", "NATIVE HAWAIIAN", "NATIVE HAWAIIAN OR OTHER PACIFIC ISLANDER"):
        return "PACIFIC ISLANDER"

    return r_spaced


def format_race_label(recorded_race: str) -> str:
    """Human-readable race label (White not WHITE; Other Asian not OTHER ASIAN)."""
    key = _canonical_race_key(recorded_race)
    if key == "UNKNOWN":
        raw = (recorded_race or "").strip()
        return raw if raw else "—"
    # Title-case words; keep short codes upper
    if len(key) <= 2:
        return key
    return key.title().replace("Or", "or").replace("/ ", "/")


def _ethnicity_family(likely_ethnicity: str) -> str:
    """Normalize a classify_by_name label to a coarse family key."""
    eth = (likely_ethnicity or "").strip().lower()
    if eth == "indian" or eth.startswith("indian") or "high_confidence" in eth:
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


def _is_other_or_other_asian(race_key: str) -> bool:
    """True for generic Other / Other Asian codes (not mismatches for Indian names)."""
    r = (race_key or "").strip().upper()
    if r in ("OTHER", "OTHER ASIAN", "UNKNOWN"):
        return True
    if "OTHER" in r and "ASIAN" in r:
        return True
    return False


def _is_compatible(likely_ethnicity: str, recorded_race: str) -> bool:
    """Return True if recorded race is consistent with the name-based ethnicity."""
    if not recorded_race or not likely_ethnicity or likely_ethnicity == "Unknown":
        return True
    family = _ethnicity_family(likely_ethnicity)
    race = _canonical_race_key(recorded_race)

    # Indian surnames: Other / Other Asian are common registry codes — not mismatches
    if family == "indian" and _is_other_or_other_asian(race):
        return True

    compatible = _ETHNICITY_COMPATIBLE_RACES.get(family)
    if not compatible:
        # Unknown family: treat exact string equality (case-insensitive) as match
        return race == likely_ethnicity.strip().upper()
    if race in compatible:
        return True
    # Also accept un-canonicalized membership for odd registry strings already uppercased
    raw_u = " ".join((recorded_race or "").strip().upper().split())
    return raw_u in compatible


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


def _first_name_from_record(record: Dict[str, Any]) -> str:
    first = (record.get("first_name") or record.get("FirstName") or "").strip()
    if first:
        # Drop middle names / initials for first-name lists
        return first.split()[0]
    full = (record.get("full_name") or record.get("Name") or "").strip()
    if full:
        parts = full.replace(",", " ").split()
        if len(parts) >= 2:
            return parts[0]
    return ""


def _middle_name_from_record(record: Dict[str, Any]) -> str:
    """Middle name from column, multi-token first_name, or full_name."""
    mid = (record.get("middle_name") or record.get("MiddleName") or "").strip()
    if mid:
        return mid
    first = (record.get("first_name") or record.get("FirstName") or "").strip()
    if first:
        parts = first.split()
        if len(parts) >= 2:
            return " ".join(parts[1:])
    full = (record.get("full_name") or record.get("Name") or "").strip()
    if full:
        parts = full.replace(",", " ").split()
        if len(parts) >= 3:
            return " ".join(parts[1:-1])
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
        """Search offenders by race (INDIAN matches South Asian tags too)."""
        start = time.time()

        records = self.db.search_by_race(race, state=state, limit=limit)
        elapsed_ms = (time.time() - start) * 1000

        return SearchResults(
            records=records,
            total_count=len(records),
            query_time_ms=elapsed_ms,
            filters_applied={"race": race, "state": state or ""}
        )

    def search_by_surname_ethnicity(
        self,
        ethnicity: str,
        state: Optional[str] = None,
        limit: int = 1000,
    ) -> SearchResults:
        """Search by curated surname-ethnicity lists (e.g. indian, indian_high_confidence)."""
        start = time.time()
        eth = (ethnicity or "").strip().lower()
        surnames: List[str] = []
        if eth in ("indian_high_confidence", "high_confidence_indian", "indian_hc"):
            surnames = list(self.ethnic_db.indian_high_confidence_surnames or [])
        elif eth == "indian":
            surnames = list(self.ethnic_db.indian_surnames or [])
        elif eth == "hispanic":
            surnames = list(self.ethnic_db.hispanic_surnames or [])
        elif eth == "asian":
            for names in (self.ethnic_db.asian_surnames or {}).values():
                surnames.extend(names)
        elif eth == "african_american":
            surnames = list(self.ethnic_db.african_american_surnames or [])
        elif eth == "arabic":
            surnames = list(self.ethnic_db.arabic_surnames or [])
        elif eth == "jewish":
            surnames = list(self.ethnic_db.jewish_surnames or [])
        elif eth == "portuguese":
            surnames = list(self.ethnic_db.portuguese_surnames or [])
        elif eth == "native_american":
            surnames = list(self.ethnic_db.native_american_surnames or [])
        records = self.db.search_by_surname_list(surnames, state=state, limit=limit)
        elapsed_ms = (time.time() - start) * 1000
        return SearchResults(
            records=records,
            total_count=len(records),
            query_time_ms=elapsed_ms,
            filters_applied={"surname_ethnicity": eth, "state": state or ""},
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
        return_base_count: bool = False,
    ):
        """Find potential race/ethnicity misclassifications.

        ethnicity_filter: optional family key such as 'hispanic', 'asian',
        'indian', 'indian_high_confidence', 'african_american'.
        When set, only that family (or curated HC Indian list) is considered.

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
        hc_only = filter_key in (
            "indian_high_confidence",
            "high_confidence_indian",
            "high-confidence indian",
            "indian_hc",
        )
        if hc_only:
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

            if not last_name:
                continue

            if hc_only and not self.ethnic_db.is_indian_high_confidence_surname(last_name):
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

            if _is_compatible(likely_eth, recorded_race):
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
