"""Search and filter engine for sex offender records."""

import re
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from .database import Database
from .ethnic_names import EthnicNameDatabase


@dataclass
class SearchResults:
    """Container for search results with metadata."""
    records: List[Dict[str, Any]]
    total_count: int
    query_time_ms: float
    filters_applied: Dict[str, str] = field(default_factory=dict)


@dataclass
class Misclassification:
    """A record that may have been misclassified by race/ethnicity."""
    record: Dict[str, Any]
    expected_race: str
    likely_ethnicity: str
    confidence: float
    matching_names: List[str] = field(default_factory=list)


# Map likely-ethnicity labels to race strings that are considered a match
# (i.e. not a misclassification when the recorded race is one of these).
# Keys are *canonical* race codes from _canonical_race_key().
#
# Hispanic: race codes that *are* the ethnicity (Hispanic/Latino/White Hispanic).
# Race=White alone is NOT enough — that is only compatible when the separate
# ethnicity field is also marked Hispanic (see _is_compatible / _has_hispanic_ethnicity).
_ETHNICITY_COMPATIBLE_RACES = {
    "hispanic": {
        "HISPANIC", "LATINO", "LATINA", "LATINX", "H",
        "WHITE HISPANIC", "HISPANIC OR LATINO", "LATINO OR HISPANIC",
    },
    "asian": {
        "ASIAN", "ASIAN / PACIFIC ISLANDER", "ASIAN/PACIFIC ISLANDER",
        "PACIFIC ISLANDER", "A", "API", "CHINESE", "KOREAN", "JAPANESE",
        "VIETNAMESE", "FILIPINO", "OTHER ASIAN",
    },
    # South Asian / Indian + MENA (merged). Asian/Other/Unknown for Indic;
    # White / Middle Eastern / Arab accepted for MENA-arabic labels via
    # _is_compatible (not all Indian/MENA rows — see _is_mena_arabic_label).
    "indian": {
        "ASIAN", "ASIAN / PACIFIC ISLANDER", "ASIAN/PACIFIC ISLANDER",
        "ASIAN INDIAN", "EAST INDIAN", "INDIAN", "SOUTH ASIAN",
        "A", "API", "OTHER", "OTHER ASIAN", "UNKNOWN", "U",
        "MIDDLE EASTERN", "ARAB",
    },
    "african_american": {
        "BLACK", "AFRICAN AMERICAN", "AFRICAN-AMERICAN", "B", "BLACK OR AFRICAN AMERICAN",
    },
    "native_american": {
        "NATIVE AMERICAN", "AMERICAN INDIAN", "AMERICAN INDIAN OR ALASKA NATIVE",
        "ALASKA NATIVE", "I", "NATIVE",
    },
    # MENA / Arabic (separate filter branch; also covered by indian/mena merged)
    "mena": {"WHITE", "OTHER", "MIDDLE EASTERN", "ARAB", "UNKNOWN", "U"},
    "arabic": {"WHITE", "OTHER", "MIDDLE EASTERN", "ARAB", "UNKNOWN", "U"},
    "jewish": {"WHITE", "OTHER"},
    "portuguese": {"WHITE", "HISPANIC", "OTHER"},
    "european": {"WHITE", "CAUCASIAN", "W"},
    "african": {
        "BLACK", "AFRICAN AMERICAN", "AFRICAN-AMERICAN", "B", "BLACK OR AFRICAN AMERICAN",
    },
}

# Collapse case/spelling variants so stats and comparisons share one bucket.
_RACE_ALIASES = {
    "W": "WHITE",
    "CAUCASIAN": "WHITE",
    "CAUCASION": "WHITE",  # common misspelling
    "WHITE": "WHITE",
    "B": "BLACK",
    "BLACK": "BLACK",
    "AFRICAN AMERICAN": "BLACK",
    "AFRICAN-AMERICAN": "BLACK",
    "BLACK OR AFRICAN AMERICAN": "BLACK",
    "H": "HISPANIC",
    "LATINO": "HISPANIC",
    "LATINA": "HISPANIC",
    "LATINX": "HISPANIC",
    "HISPANIC": "HISPANIC",
    "HISPANIC OR LATINO": "HISPANIC",
    "LATINO OR HISPANIC": "HISPANIC",
    "HISPANIC/LATINO": "HISPANIC",
    "LATINO/HISPANIC": "HISPANIC",
    "A": "ASIAN",
    "API": "ASIAN",
    "ASIAN": "ASIAN",
    "U": "UNKNOWN",
    "UNK": "UNKNOWN",
    "UNKNOWN": "UNKNOWN",
    "N/A": "UNKNOWN",
    "NA": "UNKNOWN",
    "NONE": "UNKNOWN",
    "NULL": "UNKNOWN",
    "": "UNKNOWN",
}


def _split_multi_race_parts(recorded_race: str) -> List[str]:
    """Split multi-source race strings (``Black | B``, ``W [FL·csv] | Asian``)."""
    raw = (recorded_race or "").strip()
    if not raw:
        return []
    parts: List[str] = []
    for chunk in raw.split("|"):
        # Drop provenance tags like [FL·csv✓]
        piece = re.sub(r"\[[^\]]*\]", "", chunk).strip()
        if piece:
            parts.append(piece)
    return parts


def _canonical_race_key_one(recorded_race: str) -> str:
    """Canonicalize a single race token (no multi-source pipes)."""
    raw = (recorded_race or "").strip()
    if not raw or raw.upper() in ("N/A", "NA"):
        return "UNKNOWN"
    # collapse whitespace and punctuation noise for matching
    r = " ".join(raw.upper().replace("_", " ").replace("-", " ").split())
    r = r.replace(" / ", "/").replace("/ ", "/").replace(" /", "/")
    # "OTHER-ASIAN", "OTHER / ASIAN" → form used below
    r_spaced = r.replace("/", " ")
    r_spaced = " ".join(r_spaced.split())

    if r_spaced in _RACE_ALIASES:
        return _RACE_ALIASES[r_spaced]

    # Other Asian variants (Indian-friendly bucket)
    if r_spaced in ("OTHER ASIAN", "ASIAN OTHER", "OTHER ASIAN PACIFIC ISLANDER"):
        return "OTHER ASIAN"
    if "OTHER" in r_spaced and "ASIAN" in r_spaced:
        return "OTHER ASIAN"

    # White Hispanic kept distinct from White
    if "HISPANIC" in r_spaced and "WHITE" in r_spaced:
        return "WHITE HISPANIC"
    # "Hispanic or Latino" / "Latino or Hispanic" without White
    if "HISPANIC" in r_spaced or "LATINO" in r_spaced or "LATINA" in r_spaced:
        return "HISPANIC"
    if r_spaced.startswith("WHITE") or r_spaced.endswith(" WHITE"):
        return "WHITE"
    if r_spaced.startswith("BLACK") or r_spaced.endswith(" BLACK"):
        return "BLACK"

    if r_spaced in ("OTHER", "OTHER RACE", "OTHER RACES", "OT"):
        return "OTHER"

    # Asian Pacific Islander phrasing
    if "ASIAN" in r_spaced and "PACIFIC" in r_spaced:
        return "ASIAN / PACIFIC ISLANDER"
    if r_spaced in ("PACIFIC ISLANDER", "NATIVE HAWAIIAN", "NATIVE HAWAIIAN OR OTHER PACIFIC ISLANDER"):
        return "PACIFIC ISLANDER"

    return r_spaced


def _canonical_race_key(recorded_race: str) -> str:
    """
    Normalize recorded race for comparison and grouping.

    Merges case variants (White / WHITE → WHITE) and common aliases.
    Multi-source values (``Black | B``) collapse when all parts agree;
    true conflicts become a sorted join of distinct keys.
    """
    parts = _split_multi_race_parts(recorded_race)
    if not parts:
        return "UNKNOWN"
    keys: List[str] = []
    seen: set = set()
    for p in parts:
        k = _canonical_race_key_one(p)
        if k == "UNKNOWN":
            continue
        if k not in seen:
            seen.add(k)
            keys.append(k)
    if not keys:
        return "UNKNOWN"
    if len(keys) == 1:
        return keys[0]
    return " | ".join(keys)


def _format_race_key(key: str) -> str:
    if key == "UNKNOWN":
        return "—"
    if len(key) <= 2:
        return key
    return key.title().replace("Or", "or").replace("/ ", "/")


def format_race_label(recorded_race: str) -> str:
    """Human-readable race label (White not WHITE; Black | B → Black)."""
    raw = (recorded_race or "").strip()
    if not raw:
        return "—"
    key = _canonical_race_key(raw)
    if key == "UNKNOWN":
        return raw
    # Conflict: "BLACK | WHITE" → "Black | White"
    if " | " in key:
        return " | ".join(_format_race_key(k) for k in key.split(" | "))
    return _format_race_key(key)


# UI / CLI filter keys (analyze, search, enrich, NSOPW)
INDIAN_ONLY_FILTERS = frozenset({
    "indian",
    # legacy aliases (no longer shown in UI)
    "indian_high_confidence",
    "high_confidence_indian",
    "high-confidence indian",
    "indian_hc",
})
MENA_ONLY_FILTERS = frozenset({
    "mena",
    # legacy aliases (no longer shown in UI)
    "arabic",
    "middle_eastern",
    "middle eastern",
})
# Merged Indian + MENA (canonical UI label: indian/mena (merged))
INDIAN_MENA_MERGED_FILTERS = frozenset({
    "indian/mena (merged)",
    # legacy aliases
    "indian/mena",
    "indian_mena",
    "merged",
    "indian/mena_merged",
    "indian_mena_merged",
})

# --- Misclassify coarse buckets (all other surname families fold into these) ---
# white ← european, jewish, portuguese, native_american
# black ← african_american, african
# hispanic ← hispanic
# indian ← Indic only (East/SE Asian is separate)
# asian ← East/SE Asian
# mena ← mena / arabic
WHITE_FILTERS = frozenset({"white", "european", "jewish", "portuguese", "native_american"})
WHITE_FAMILIES = frozenset({"european", "jewish", "portuguese", "native_american", "white"})
BLACK_FILTERS = frozenset({"black", "african_american", "african"})
BLACK_FAMILIES = frozenset({"african_american", "african", "black"})
HISPANIC_FILTERS = frozenset({"hispanic"})
# Indic surnames only — do not fold East/SE Asian into indian
INDIAN_BUCKET_FAMILIES = frozenset({"indian"})
ASIAN_FILTERS = frozenset({"asian", "east_asian", "southeast_asian"})
ASIAN_FAMILIES = frozenset({"asian"})

NON_WHITE_FAMILIES = frozenset({
    "hispanic", "asian", "indian", "mena",
    "african_american", "african", "native_american",
})
NON_WHITE_FILTERS = frozenset({
    "non-white", "non_white", "nonwhite", "non white",
})

# Full list for Search / NSOPW / Integrity (fine-grained harvest)
ETHNICITY_FILTER_UI = (
    "hispanic",
    "asian",
    "indian/mena (merged)",
    "indian",
    "mena",
    "african_american",
    "african",
    "jewish",
    "portuguese",
    "native_american",
    "european",
)
# Misclassify likely ethnicity — coarse buckets with Asian separate from Indian
ETHNICITY_FILTER_UI_MISCLASS = (
    "white",
    "black",
    "hispanic",
    "asian",
    "indian",
    "mena",
)
# CLI accepts coarse + fine + legacy aliases
ETHNICITY_FILTER_CLI = tuple(
    dict.fromkeys(
        ("all", "white", "black", "non-white")
        + ETHNICITY_FILTER_UI
        + (
            "non_white",
            "nonwhite",
            "indian/mena",
            "merged",
            "arabic",  # → mena
            "indian_high_confidence",  # → indian
        )
    )
)


def _is_mena_arabic_label(likely_ethnicity: str) -> bool:
    """True when the name hit is MENA/Arabic-sourced (not Indic subgroup)."""
    eth = (likely_ethnicity or "").strip().lower()
    if eth in ("arabic", "indian/mena (arabic)"):
        return True
    if "(arabic)" in eth:
        return True
    return eth == "arabic" or eth.startswith("arabic")


def _ethnicity_family(likely_ethnicity: str) -> str:
    """Normalize a classify_by_name label to a coarse family key.

    Display labels stay ``Indian/MENA (…)``; families split for filters:
    ``indian`` (Indic / HC) vs ``mena`` (Arabic). Use
    ``ethnicity_filter_matches`` for the merged filter.
    """
    eth = (likely_ethnicity or "").strip().lower()
    # MENA first — labels like Indian/MENA (arabic) also startswith "indian"
    if _is_mena_arabic_label(likely_ethnicity) or eth in ("arabic", "mena") or (
        eth.startswith("arabic") or eth.startswith("mena")
    ):
        return "mena"
    if (
        eth == "indian"
        or eth.startswith("indian")
        or ("high_confidence" in eth and "indian" in eth)
    ):
        return "indian"
    if eth.startswith("asian"):
        return "asian"
    if eth.startswith("european"):
        return "european"
    if eth.startswith("african (") or eth == "african":
        return "african"
    if eth in ("african american", "african-american"):
        return "african_american"
    if eth in ("native american", "native-american"):
        return "native_american"
    if eth == "hispanic":
        return "hispanic"
    if eth == "jewish":
        return "jewish"
    if eth == "portuguese":
        return "portuguese"
    return eth.replace(" ", "_")


def ethnicity_filter_matches(
    family: str,
    filter_key: Optional[str],
) -> bool:
    """True when *family* (from ``_ethnicity_family``) matches a UI/CLI filter.

    Misclassify coarse keys:
    white / black / hispanic / asian / indian / mena.
    Asian and Indian are separate — East/SE Asian does not match ``indian``.
    """
    key = (filter_key or "").strip().lower() or None
    if not key or key == "all":
        return True
    fam = (family or "").strip().lower()
    if key in NON_WHITE_FILTERS:
        return fam in NON_WHITE_FAMILIES
    if key in WHITE_FILTERS:
        return fam in WHITE_FAMILIES
    if key in BLACK_FILTERS:
        return fam in BLACK_FAMILIES
    if key in HISPANIC_FILTERS:
        return fam == "hispanic"
    if key in ASIAN_FILTERS:
        return fam in ASIAN_FAMILIES
    if key in INDIAN_MENA_MERGED_FILTERS:
        # Indic + MENA only (not East/SE Asian)
        return fam in ("indian", "mena")
    if key in INDIAN_ONLY_FILTERS:
        return fam in INDIAN_BUCKET_FAMILIES
    if key in MENA_ONLY_FILTERS:
        return fam == "mena"
    return fam == key


def _is_other_or_other_asian(race_key: str) -> bool:
    """True for generic Other / Other Asian codes (not mismatches for Indian names)."""
    r = (race_key or "").strip().upper()
    if r in ("OTHER", "OTHER ASIAN", "UNKNOWN"):
        return True
    if "OTHER" in r and "ASIAN" in r:
        return True
    return False


def _has_hispanic_ethnicity(recorded_ethnicity: Optional[str]) -> bool:
    """True when the registry ethnicity field marks Hispanic / Latino."""
    eth = (recorded_ethnicity or "").strip().upper()
    if not eth:
        return False
    # Explicit non-Hispanic markers first
    if re.search(r"\bNON[\s\-]?HISPANIC\b", eth) or "NOT HISPANIC" in eth:
        return False
    markers = (
        "HISPANIC", "LATINO", "LATINA", "LATINX",
        "HISPANIC OR LATINO", "LATINO OR HISPANIC",
    )
    if any(m in eth for m in markers):
        return True
    # Single-letter ethnicity codes used by some bulk feeds
    if eth in ("H", "HIS", "HISP"):
        return True
    return False


def recorded_ethnicity_from_record(record: Optional[Dict[str, Any]]) -> str:
    """Best registry ethnicity for misclass checks (column, then sources_json).

    TX/SC bulk and HTML often store ethnicity only under sources_json.fields
    while the top-level ``ethnicity`` column is empty — that used to make
    White + online-Hispanic people (e.g. CARLOS ALVARADO) false-flag.
    """
    if not record:
        return ""
    top = record.get("ethnicity") or record.get("Ethnicity") or ""
    if isinstance(top, str):
        top = top.strip()
    else:
        top = str(top or "").strip()
    if top:
        return top
    try:
        from scraper.database.sources import parse_sources, primary_field_value

        sources = parse_sources(record.get("sources_json"))
        if not sources:
            return ""
        # Prefer an explicit Hispanic/Latino value from any source when present
        # ( steers White+HISPANIC TX bulk over blank HTML fields).
        try:
            from scraper.database.sources import collect_field_values

            for row in collect_field_values(sources, "ethnicity"):
                val = str(row.get("value") or "").strip()
                if val and _has_hispanic_ethnicity(val):
                    return val
        except Exception:
            pass
        pv = primary_field_value(sources, "ethnicity")
        if pv:
            return str(pv).strip()
    except Exception:
        return ""
    return ""


def _is_compatible(
    likely_ethnicity: str,
    recorded_race: str,
    recorded_ethnicity: Optional[str] = None,
    last_name: Optional[str] = None,
) -> bool:
    """Return True if recorded race/ethnicity is consistent with the name-based ethnicity.

    Hispanic + race White: only compatible when *recorded_ethnicity* is also
    Hispanic/Latino. White with blank/non-Hispanic ethnicity is a mismatch.

    African / African American + race White: only a mismatch when the surname
    is *uniquely* Black (not shared Anglo names like Wade / Washington).
    """
    if not recorded_race or not likely_ethnicity or likely_ethnicity == "Unknown":
        return True
    family = _ethnicity_family(likely_ethnicity)
    race = _canonical_race_key(recorded_race)

    # Indic: Other / Other Asian are common registry codes — not mismatches
    if family == "indian" and _is_other_or_other_asian(race):
        return True

    # MENA: US registries usually code White / Other / ME
    if family == "mena" and race in (
        "WHITE", "OTHER", "MIDDLE EASTERN", "ARAB", "UNKNOWN",
    ):
        return True

    # Shared Anglo/Black surnames: White is not a misclassification
    if family in ("african_american", "african") and race == "WHITE":
        from scraper.ethnic_names_black_unique import is_shared_black_white_surname

        if last_name and is_shared_black_white_surname(last_name):
            return True

    # Hispanic surnames: empty / unknown race are not useful mismatch signals
    if family == "hispanic" and race in ("UNKNOWN", "OTHER"):
        return True

    # Hispanic + White/Caucasian: require ethnicity field = Hispanic
    if family == "hispanic" and race == "WHITE":
        return _has_hispanic_ethnicity(recorded_ethnicity)

    compatible = _ETHNICITY_COMPATIBLE_RACES.get(family)
    if not compatible:
        # Unknown family: treat exact string equality (case-insensitive) as match
        return race == likely_ethnicity.strip().upper()
    if race in compatible:
        return True
    # Also accept un-canonicalized membership for odd registry strings already uppercased
    raw_u = " ".join((recorded_race or "").strip().upper().split())
    return raw_u in compatible


def _last_name_from_record(record: Dict[str, Any]) -> str:
    last = (record.get("last_name") or record.get("LastName") or "").strip()
    if last:
        return last
    full = (record.get("full_name") or record.get("Name") or "").strip()
    if full:
        parts = full.split()
        if parts:
            return parts[-1]
    return ""


def _first_name_from_record(record: Dict[str, Any]) -> str:
    first = (record.get("first_name") or record.get("FirstName") or "").strip()
    if first:
        # Drop middle names / initials for first-name lists
        return first.split()[0]
    full = (record.get("full_name") or record.get("Name") or "").strip()
    if full:
        parts = full.replace(",", " ").split()
        if len(parts) >= 2:
            return parts[0]
    return ""


def _middle_name_from_record(record: Dict[str, Any]) -> str:
    """Middle name from column, multi-token first_name, or full_name."""
    mid = (record.get("middle_name") or record.get("MiddleName") or "").strip()
    if mid:
        return mid
    first = (record.get("first_name") or record.get("FirstName") or "").strip()
    if first:
        parts = first.split()
        if len(parts) >= 2:
            return " ".join(parts[1:])
    full = (record.get("full_name") or record.get("Name") or "").strip()
    if full:
        parts = full.replace(",", " ").split()
        if len(parts) >= 3:
            return " ".join(parts[1:-1])
    return ""


