"""Virginia vspsor.com offense extraction (gold card headers)."""
from __future__ import annotations

import re
from typing import List, Set

from bs4 import BeautifulSoup

from scraper.reports.fetcher_crime import (
    _is_crime_cell,
    is_demographic_crime_junk,
    is_label_chrome_value,
)
from scraper.reports.util import _MAX_CRIME_LEN, _clean_value


def extract_va_card_offenses(soup: BeautifulSoup) -> str:
    """Virginia vspsor.com: offense titles in ``.card-header.gold`` cards.

    Example::

        <div class="card-header gold">
          <span>18.2-374.1:1(C) - POSSESSION OF CHILD PORNOGRAPHY -</span>
        </div>
    """
    collected: List[str] = []
    seen: Set[str] = set()
    selectors = (
        "#convictions .card-header",
        ".card-header.gold",
        "div.card-header.gold",
    )
    nodes = []
    for sel in selectors:
        nodes.extend(soup.select(sel))
    for node in nodes:
        raw = _clean_value(node.get_text(" ", strip=True))
        # Strip trailing statute/chrome dashes: "… PORNOGRAPHY -"
        raw = re.sub(r"\s*[-–—]+\s*$", "", raw).strip()
        if not raw or len(raw) < 5:
            continue
        if is_label_chrome_value(raw) or is_demographic_crime_junk(raw):
            continue
        if not _is_crime_cell(raw):
            continue
        # Prefer statute-ish or offense-word titles (skip pure UI chrome)
        if not re.search(
            r"(?i)\d|rape|assault|battery|lewd|sex|child|molest|porn|"
            r"kidnap|indecent|fail|offense|sodomy|murder|abuse|fondl",
            raw,
        ):
            continue
        key = raw.casefold()
        if key in seen:
            continue
        seen.add(key)
        collected.append(raw)
        if len(collected) >= 8:
            break
    return "; ".join(collected)[:_MAX_CRIME_LEN]
