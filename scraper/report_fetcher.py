"""Shim: report_fetcher moved to scraper.reports (stable import path)."""
from __future__ import annotations

import scraper.reports.util as _reports_util
from scraper.reports.fetcher import ReportFetcher

for _name in dir(_reports_util):
    if _name.startswith("__"):
        continue
    globals()[_name] = getattr(_reports_util, _name)
del _name, _reports_util

__all__ = ["ReportFetcher"]
