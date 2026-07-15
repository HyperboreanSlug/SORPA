from __future__ import annotations

import time
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



class SearcherCoreMixin:
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
        limit: int = 0,
    ) -> SearchResults:
        """Search by curated surname-ethnicity lists (indian, mena, asian, …).

        *limit* ``0`` returns all matching rows (no artificial cap).
        """
        start = time.time()
        eth = (ethnicity or "").strip().lower()
        from scraper.searcher_surnames import surnames_for_ethnicity_filter

        surnames = surnames_for_ethnicity_filter(self.ethnic_db, eth) or []
        records = self.db.search_by_surname_list(
            surnames, state=state, limit=limit
        )
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


    def get_race_distribution(self) -> List[Dict[str, Any]]:
        """Get count of offenders by race."""
        return self.db.get_race_distribution()


    def get_state_distribution(self) -> List[Dict[str, Any]]:
        """Get count of offenders by state."""
        return self.db.get_state_distribution()


    def get_total_count(self) -> int:
        """Get total number of records."""
        return self.db.get_total_count()


