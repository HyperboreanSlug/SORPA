"""API-based scraper for state registries with REST-like endpoints."""

import json
import math
from typing import List, Dict, Any, Optional

import requests

from .base import BaseScraper
from .normalize import normalize_records


class APIScraper(BaseScraper):
    """Scrapes data from state registries that expose API endpoints."""

    # Common API patterns for pagination
    PAGE_SIZE = 1000
    MAX_PAGES = 50

    def __init__(self, state_abbr: str, delay: float = 2.0):
        super().__init__(state_abbr, delay)

    def get_direct_download_urls(self) -> List[str]:
        """Return bulk download URLs from config (if any)."""
        from ..config import get_registry_by_abbr
        registry = get_registry_by_abbr(self.state_abbr)
        return list(registry.direct_downloads) if registry else []

    def _get_api_url(self) -> Optional[str]:
        """Get the API endpoint for this state."""
        from ..config import get_registry_by_abbr
        registry = get_registry_by_abbr(self.state_abbr)
        return registry.search_api if registry else None

    def scrape(self, page_size: int = PAGE_SIZE, max_pages: int = MAX_PAGES) -> List[Dict[str, Any]]:
        """Scrape all records from the API, falling back to direct downloads if needed."""
        api_url = self._get_api_url()
        if not api_url:
            # Prefer bulk files when no API is configured
            direct_urls = self.get_direct_download_urls()
            if direct_urls:
                from .direct_download import DirectDownloadScraper
                return DirectDownloadScraper(self.state_abbr, delay=self.delay).scrape()
            return []

        records: List[Dict[str, Any]] = []
        page = 1

        while page <= max_pages:
            params = {
                "page": page,
                "pageSize": page_size,
            }

            try:
                resp = self._get(api_url, params=params)
                data = resp.json()

                # Parse the response (handle various API formats)
                batch = self._parse_api_response(data)
                if not batch:
                    break  # No more records

                records.extend(batch)
                print(f"  [{self.state_abbr}] Page {page}: {len(batch)} records")

                # Check if there are more pages
                total_pages = self._get_total_pages(data, page, len(batch), page_size)
                if page >= total_pages:
                    break

                page += 1

            except (requests.RequestException, json.JSONDecodeError) as e:
                print(f"  [{self.state_abbr}] Error on page {page}: {e}")
                break

        return normalize_records(records, state=self.state_abbr)

    def _parse_api_response(self, data: Any) -> List[Dict[str, Any]]:
        """Parse various API response formats into list of record dicts."""
        if isinstance(data, list):
            return data

        elif isinstance(data, dict):
            # Common patterns for the results array
            for key in ("results", "data", "records", "items", "offenders", "rows"):
                if key in data and isinstance(data[key], list):
                    return data[key]

            # If it's a single record wrapped in metadata
            if any(k in data for k in ("total", "count", "page")):
                return []  # Metadata-only response, no records to extract

        return []

    def _get_total_pages(
        self, data: Any, current_page: int, batch_size: int, page_size: int = PAGE_SIZE
    ) -> int:
        """Determine total pages from API response."""
        if isinstance(data, dict):
            for key in ("totalPages", "pages", "total_pages"):
                if key in data:
                    return data[key]

            # Calculate from total count and the page size actually requested
            for key in ("total", "count", "totalCount"):
                if key in data:
                    total = data[key]
                    return max(1, math.ceil(total / max(1, int(page_size))))

        return current_page + 1

    def scrape_by_search(
        self,
        name: Optional[str] = None,
        state: Optional[str] = None,
        race: Optional[str] = None,
        min_age: int = 0,
        max_age: int = 120
    ) -> List[Dict[str, Any]]:
        """Search the API with filters."""
        api_url = self._get_api_url()
        if not api_url:
            return []

        params = {
            "minAge": min_age,
            "maxAge": max_age,
        }

        if name:
            params["name"] = name
        if state:
            params["state"] = state
        if race:
            params["race"] = race

        try:
            resp = self._get(api_url, params=params)
            data = resp.json()
            return self._parse_api_response(data)
        except (requests.RequestException, json.JSONDecodeError):
            return []