from __future__ import annotations

from scraper.database import Database
from scraper.ethnic_names import EthnicNameDatabase, get_ethnic_database

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
    recorded_ethnicity_from_record,
)



class SearcherInitMixin:
    def __init__(self, db_path: Optional[str] = None):
        self.db = Database(db_path)
        self.ethnic_db = EthnicNameDatabase()


    def close(self):
        """Close the database connection."""
        self.db.close()


