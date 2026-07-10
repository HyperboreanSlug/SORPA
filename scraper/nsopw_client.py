"""Shim: nsopw_client moved to scraper.nsopw.client (stable import path)."""
from scraper.nsopw.client import (  # noqa: F401
    BROWSER_UA,
    DEFAULT_JURISDICTIONS,
    NSOPW_OFFLINE_URL,
    NSOPW_ORIGIN,
    NSOPW_SEARCH_PAGE,
    NSOPW_SEARCH_URL,
    NSOPWClient,
    NSOPWOffender,
    normalize_jurisdiction_code,
    offender_matches_name_prefixes,
    _stable_source_url,
    _stable_external_id,
    _is_cloudflare_block,
    _make_http_session,
)
