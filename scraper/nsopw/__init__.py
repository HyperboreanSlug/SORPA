"""NSOPW client and ethnic database builder package."""

# Import client first (no dependency on builder) to keep shims cycle-free.
from scraper.nsopw.client import (
    NSOPWClient,
    NSOPWOffender,
    normalize_jurisdiction_code,
    offender_matches_name_prefixes,
)
from scraper.nsopw.search_plan import (
    compact_search_plan,
    estimate_compact_query_count,
    first_initials_for_mode,
    describe_first_mode,
    is_abbreviated_first_mode,
)
from scraper.nsopw.builder import (
    NSOPWEthnicDatabaseBuilder,
    RateLimiter,
    BuildStats,
    StateReportStats,
)

__all__ = [
    "NSOPWClient",
    "NSOPWOffender",
    "NSOPWEthnicDatabaseBuilder",
    "normalize_jurisdiction_code",
    "compact_search_plan",
    "RateLimiter",
    "BuildStats",
    "StateReportStats",
    "offender_matches_name_prefixes",
    "estimate_compact_query_count",
    "first_initials_for_mode",
    "describe_first_mode",
    "is_abbreviated_first_mode",
]
