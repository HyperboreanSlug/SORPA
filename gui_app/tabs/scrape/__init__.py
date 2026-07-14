"""Scrape tab package."""
from __future__ import annotations

from .build import ScrapeBuildMixin
from .dedupe import ScrapeDedupeMixin
from .dedupe_remove import ScrapeDedupeRemoveMixin
from .import_csv import ScrapeImportMixin
from .run import ScrapeRunMixin
from .select import ScrapeSelectMixin


class ScrapeTabMixin(
    ScrapeBuildMixin,
    ScrapeSelectMixin,
    ScrapeRunMixin,
    ScrapeImportMixin,
    ScrapeDedupeMixin,
    ScrapeDedupeRemoveMixin,
):
    """State bulk scrape + CSV import + dedupe."""


__all__ = ["ScrapeTabMixin"]
