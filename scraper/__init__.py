"""Public US sex offender registry scraper, search, and analysis package."""

from .database import Database, get_database
from .searcher import SexOffenderSearcher, get_searcher
from .ethnic_names import EthnicNameDatabase, get_ethnic_database
from .config import (
    REGISTRIES,
    RegistryConfig,
    get_registry_by_abbr,
    get_all_state_registries,
    get_direct_download_sources,
)
from .scrapers.base import BaseScraper, ScraperFactory
from .scrapers.direct_download import DirectDownloadScraper
from .scrapers.api_scraper import APIScraper
from .scrapers.html_scraper import HTMLScraper
from .scrapers.hybrid_scraper import HybridScraper
from .scrapers.arcgis_scraper import ArcGISScraper
from .nsopw_client import NSOPWClient
from .nsopw_builder import NSOPWEthnicDatabaseBuilder

__version__ = "1.3.4"

__all__ = [
    "Database",
    "get_database",
    "SexOffenderSearcher",
    "get_searcher",
    "EthnicNameDatabase",
    "get_ethnic_database",
    "REGISTRIES",
    "RegistryConfig",
    "get_registry_by_abbr",
    "get_all_state_registries",
    "get_direct_download_sources",
    "BaseScraper",
    "ScraperFactory",
    "DirectDownloadScraper",
    "APIScraper",
    "HTMLScraper",
    "HybridScraper",
    "ArcGISScraper",
    "NSOPWClient",
    "NSOPWEthnicDatabaseBuilder",
    "__version__",
]