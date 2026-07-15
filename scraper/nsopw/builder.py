"""NSOPW ethnic database builder (composed mixins)."""
from __future__ import annotations

from scraper.nsopw.builder_types import (  # noqa: F401
    BuildStats,
    RateLimiter,
    StateReportStats,
)
from scraper.nsopw.builder_init import BuilderInitMixin
from scraper.nsopw.builder_querylog import BuilderQueryLogMixin
from scraper.nsopw.builder_surnames import BuilderSurnamesMixin
from scraper.nsopw.builder_build import BuilderBuildMixin
from scraper.nsopw.builder_requeue_inc import BuilderRequeueIncMixin
from scraper.nsopw.builder_requeue_work import BuilderRequeueWorkMixin
from scraper.nsopw.builder_verify import BuilderVerifyMixin
from scraper.nsopw.builder_enrich_need import BuilderEnrichNeedMixin
from scraper.nsopw.builder_enrich_run import BuilderEnrichRunMixin
from scraper.nsopw.builder_match import BuilderMatchMixin
from scraper.nsopw.builder_merge_demo import BuilderMergeDemoMixin
from scraper.nsopw.builder_photo import BuilderPhotoMixin


class NSOPWEthnicDatabaseBuilder(
    BuilderInitMixin,
    BuilderQueryLogMixin,
    BuilderSurnamesMixin,
    BuilderBuildMixin,
    BuilderRequeueIncMixin,
    BuilderRequeueWorkMixin,
    BuilderVerifyMixin,
    BuilderEnrichNeedMixin,
    BuilderEnrichRunMixin,
    BuilderMatchMixin,
    BuilderMergeDemoMixin,
    BuilderPhotoMixin,
):
    """Orchestrates NSOPW ethnic harvest, report fetch, enrich, requeue."""


__all__ = [
    "NSOPWEthnicDatabaseBuilder",
    "BuildStats",
    "StateReportStats",
    "RateLimiter",
]
