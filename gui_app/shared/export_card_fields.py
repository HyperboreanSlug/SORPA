"""Field extractors and fonts for export mugshot cards."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Optional

from PIL import ImageFont

_WATERMARK = "@DoDeportations"
_SEAL_PATH = (
    Path(__file__).resolve().parents[2] / "assets" / "department_of_deportations_seal.png"
)
_CARD_W = 1080
_CARD_H = 1350
# Legacy; premium layout sizes photo dynamically.
_PHOTO_H = 760
# Premium card palette (matches premium_card_blank.html)
_BG = (10, 11, 14, 255)
_PANEL = (20, 22, 27, 255)
_CRIME_PANEL = (20, 22, 27, 255)
_LINE = (38, 42, 51, 255)
_TEXT = (243, 241, 234, 255)
_MUTED = (139, 143, 153, 255)
_FOIL = (240, 206, 132, 255)
_ACCENT = (240, 206, 132, 255)
_BANNER_RED = (140, 31, 31, 255)
_BANNER_TEXT = (255, 255, 255, 255)


def os_environ_get(key: str, default: str = "") -> str:
    import os

    return os.environ.get(key, default)


def desktop_dir() -> Path:
    home = Path.home()
    for name in ("Desktop", "OneDrive/Desktop", "OneDrive/Рабочий стол"):
        candidate = home / name
        if candidate.is_dir():
            return candidate
    desktop = home / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    return desktop


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w\s\-]+", "", name, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", "_", cleaned) or "offender"
    return cleaned[:80]


def load_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    windir = Path(os_environ_get("WINDIR", r"C:\Windows"))
    candidates = []
    if bold:
        candidates.extend(
            [
                windir / "Fonts" / "segoeuib.ttf",
                windir / "Fonts" / "arialbd.ttf",
                windir / "Fonts" / "calibrib.ttf",
            ]
        )
    candidates.extend(
        [
            windir / "Fonts" / "segoeui.ttf",
            windir / "Fonts" / "arial.ttf",
            windir / "Fonts" / "calibri.ttf",
        ]
    )
    for path in candidates:
        try:
            if path.is_file():
                return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _clean_field(val: Any) -> str:
    """Strip placeholders — never surface 'Unknown' on export cards."""
    s = " ".join(str(val or "").split()).strip()
    if not s:
        return ""
    low = s.casefold()
    if low in (
        "unknown",
        "unknown location",
        "unknown offense",
        "n/a",
        "na",
        "none",
        "null",
        "—",
        "-",
    ):
        return ""
    if low.startswith("unknown "):
        return ""
    return s


def person_name(record: Mapping[str, Any]) -> str:
    """Display name for cards — always fully UPPERCASE."""
    full = _clean_field(record.get("full_name"))
    if full:
        return full.upper()
    parts = [
        _clean_field(record.get("first_name")),
        _clean_field(record.get("middle_name")),
        _clean_field(record.get("last_name")),
    ]
    out = " ".join(p for p in parts if p)
    return out.upper() if out else ""


def location(record: Mapping[str, Any]) -> str:
    """City and state only (export cards). Empty if none."""
    return last_known_location(record)


def last_known_location(record: Mapping[str, Any]) -> str:
    """City + state only. Never address/county; never 'Unknown'."""
    city = _clean_field(record.get("city"))
    state = _clean_field(record.get("state") or record.get("source_state")).upper()
    if state in ("YY", "XX", "ZZ", "US", "NA", "UN", "UNK"):
        state = ""
    if city:
        # Title-case all-caps/all-lower city names only
        if city == city.upper() or city == city.lower():
            city = city.title()
    bits = [b for b in (city, state) if b]
    return ", ".join(bits)


def crime(record: Mapping[str, Any]) -> str:
    """Offense text for share cards. Empty string if missing — never 'Unknown'."""
    for key in (
        "crime",
        "offense_description",
        "offense_type",
        "charge_description",
    ):
        val = _clean_field(record.get(key))
        if not val:
            continue
        # Guard: MA demographic misparse (Photo Date / Name / Level / YOB)
        try:
            from scraper.reports.fetcher_crime import is_demographic_crime_junk

            if is_demographic_crime_junk(val):
                continue
        except Exception:
            pass
        try:
            from scraper.crime_summary import summarize_crime
            from scraper.crime_summary_clause import to_regular_case
            from scraper.crime_summary_junk import is_junk_label

            # Never fall back to raw registry dumps (they embed 23-CF dockets).
            summarized = summarize_crime(val) or ""
            out = _clean_field(to_regular_case(summarized))
            if not out or is_junk_label(out):
                continue
            # Hard ban residual FL case numbers / CF crumbs after title-case
            if re.search(
                r"(?i)\b\d{2,4}\s*[-–—]?\s*(?:cf|mm|ct|dr)\b", out
            ):
                continue
            try:
                from scraper.reports.fetcher_crime import is_demographic_crime_junk

                if is_demographic_crime_junk(out):
                    continue
            except Exception:
                pass
            return out
        except Exception:
            continue
    return ""


def arrest_datetime(record: Mapping[str, Any], *, assign: bool = False) -> str:
    """Footer right-side label: card export number (not a date).

    Name kept for mapa chassis compatibility. By default only *shows* an
    already-assigned number (peek). Pass ``assign=True`` only from deliberate
    export paths so preview/tests cannot burn sequence numbers.
    """
    from gui_app.shared.export_card_release import (
        format_release_label,
        peek_release_number,
        release_number_for,
    )

    if assign:
        return format_release_label(release_number_for(record, persist_db=True))
    return format_release_label(peek_release_number(record))
