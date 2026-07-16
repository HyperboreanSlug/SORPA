"""Multi-source field provenance for offender records.

Each offender may be contributed by several registries / bulk CSVs / HTML
report fetches. Values that disagree (e.g. FL race=W vs CO race=Asian) are
kept side-by-side and tagged with their origin so bulk data cannot silently
overwrite a verified jurisdiction page.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlparse

# Scalar demographic / identity fields we track per-source
TRACKED_FIELDS: Tuple[str, ...] = (
    "race",
    "ethnicity",
    "gender",
    "height",
    "weight",
    "eye_color",
    "hair_color",
    "age",
    "date_of_birth",
    "state",
    "county",
    "city",
    "address",
    "zip_code",
    "crime",
    "offense_type",
    "offense_description",
    "risk_level",
    "photo_url",
    "photo_path",
    "report_html_path",
    "source_url",
    "external_id",
)

# Prefer HTML-verified jurisdiction values over bulk CSV when choosing primary
_SOURCE_TYPE_RANK = {
    "report_html": 100,
    "nsopw_report": 90,
    "nsopw": 70,
    "csv_bulk": 40,
    "inferred": 10,
    "unknown": 0,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _norm_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def parse_sources(raw: Any) -> List[Dict[str, Any]]:
    """Parse sources_json into a list of source dicts."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [s for s in raw if isinstance(s, dict)]
    if isinstance(raw, dict):
        # Allow {"sources": [...]} wrapper
        inner = raw.get("sources")
        if isinstance(inner, list):
            return [s for s in inner if isinstance(s, dict)]
        return [raw]
    try:
        data = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return []
    return parse_sources(data)


def dumps_sources(sources: Sequence[Dict[str, Any]]) -> str:
    return json.dumps(list(sources), ensure_ascii=False)


def source_id_for(
    *,
    source_type: str,
    jurisdiction: str = "",
    origin: str = "",
    external_id: str = "",
    source_url: str = "",
) -> str:
    """Stable id for a source contribution."""
    jur = (jurisdiction or "").strip().upper() or "UNK"
    st = (source_type or "unknown").strip().lower()
    ext = (external_id or "").strip()
    if ext:
        return f"{st}:{jur}:{ext}".lower()
    origin_key = re.sub(r"[^a-z0-9]+", "_", (origin or "").strip().lower()).strip("_")
    if origin_key:
        return f"{st}:{jur}:{origin_key}".lower()
    url = (source_url or "").strip().lower()
    if url:
        # short stable tail
        tail = re.sub(r"[^a-z0-9]+", "", url[-48:])
        return f"{st}:{jur}:url:{tail}".lower()
    return f"{st}:{jur}:anon".lower()


def jurisdiction_from_url(url: str) -> str:
    """Best-effort state/jurisdiction from a registry URL host."""
    u = (url or "").lower()
    if not u:
        return ""
    host = ""
    try:
        host = (urlparse(u).netloc or "").lower()
    except Exception:
        host = u
    mapping = (
        ("fdle.state.fl", "FL"),
        ("florida", "FL"),
        ("colorado.gov", "CO"),
        ("apps.colorado.gov", "CO"),
        ("gbi.ga.gov", "GA"),
        ("state.sor.gbi.ga.gov", "GA"),
        ("icrimewatch", "AZ"),  # AZ bulk host often
        ("azdps", "AZ"),
        ("scor.sled.sc.gov", "SC"),
        ("sled.sc.gov", "SC"),
        ("sor.tbi.tn.gov", "TN"),
        ("tbi.tn.gov", "TN"),
        ("txdps", "TX"),
        ("texas.gov", "TX"),
        ("nsopw.gov", "NSOPW"),
    )
    for frag, code in mapping:
        if frag in host or frag in u:
            return code
    return ""


def label_for_source(
    *,
    source_type: str,
    jurisdiction: str = "",
    origin: str = "",
) -> str:
    jur = (jurisdiction or "").strip().upper()
    st = (source_type or "").strip().lower()
    origin_s = (origin or "").strip()
    if st == "csv_bulk":
        if jur:
            return f"{jur} SOR CSV" + (f" ({origin_s})" if origin_s else "")
        return f"CSV bulk ({origin_s})" if origin_s else "CSV bulk"
    if st in ("report_html", "nsopw_report"):
        return f"{jur or 'Registry'} report HTML"
    if st == "nsopw":
        return f"NSOPW ({jur})" if jur else "NSOPW"
    if st == "inferred":
        return f"Inferred ({jur or origin_s or 'record'})"
    return origin_s or st or "unknown"


def extract_tracked_fields(record: Dict[str, Any]) -> Dict[str, Any]:
    """Pull non-empty tracked fields from a record/CSV row."""
    out: Dict[str, Any] = {}
    for key in TRACKED_FIELDS:
        val = record.get(key)
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        out[key] = val if not isinstance(val, str) else val.strip()
    return out


def make_source(
    *,
    source_type: str,
    jurisdiction: str = "",
    origin: str = "",
    label: str = "",
    external_id: str = "",
    source_url: str = "",
    fields: Optional[Dict[str, Any]] = None,
    html_path: Optional[str] = None,
    html_verified: bool = False,
    html_status: str = "pending",
    source_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    jur = (jurisdiction or "").strip().upper()
    if not jur and source_url:
        jur = jurisdiction_from_url(source_url)
    sid = source_id or source_id_for(
        source_type=source_type,
        jurisdiction=jur,
        origin=origin,
        external_id=external_id,
        source_url=source_url,
    )
    src: Dict[str, Any] = {
        "id": sid,
        "type": (source_type or "unknown").strip().lower(),
        "jurisdiction": jur or None,
        "label": label
        or label_for_source(source_type=source_type, jurisdiction=jur, origin=origin),
        "origin": (origin or "").strip() or None,
        "external_id": (external_id or "").strip() or None,
        "source_url": (source_url or "").strip() or None,
        "fields": dict(fields or {}),
        "html_path": (html_path or "").strip() or None,
        "html_verified": bool(html_verified),
        "html_status": (html_status or "pending").strip(),
        "updated_at": _utc_now_iso(),
    }
    if extra:
        for k, v in extra.items():
            if k not in src and v is not None:
                src[k] = v
    return src


def merge_source_into_list(
    sources: List[Dict[str, Any]],
    new_src: Dict[str, Any],
    *,
    prefer_new_fields: bool = False,
) -> List[Dict[str, Any]]:
    """
    Upsert *new_src* into *sources* by id (or matching url/external_id).

    CSV bulk and live report HTML are never collapsed into one chart entry
    (same personId URL can exist on both). HTML-verified demographics are
    protected from bulk overwrite. Within one source, newer wins when
    prefer_new_fields or old is empty.
    """
    from scraper.database.sources_merge import find_merge_index, merge_source_fields

    if not new_src:
        return list(sources)
    out = [dict(s) for s in sources if isinstance(s, dict)]
    match_idx = find_merge_index(out, new_src)
    if match_idx is None:
        out.append(dict(new_src))
        return out
    out[match_idx] = merge_source_fields(
        out[match_idx],
        new_src,
        prefer_new_fields=prefer_new_fields,
        utc_now_iso=_utc_now_iso(),
    )
    return out


def merge_sources_lists(
    *lists: Iterable[Any],
) -> List[Dict[str, Any]]:
    """Merge several sources_json payloads into one list."""
    out: List[Dict[str, Any]] = []
    for raw in lists:
        for s in parse_sources(raw):
            out = merge_source_into_list(out, s)
    return out


def _race_key(val: str) -> str:
    """Loose canonical race key for conflict detection."""
    from scraper.database.sources_race_verify import race_key

    return race_key(val)


def collect_field_values(
    sources: Sequence[Dict[str, Any]],
    field: str,
) -> List[Dict[str, Any]]:
    """Return [{value, source_id, jurisdiction, label, html_verified, type}, ...] for field."""
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for s in sources:
        fields = s.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        val = fields.get(field)
        if val is None or not str(val).strip():
            continue
        v = str(val).strip()
        key = (_race_key(v) if field == "race" else v.casefold(), _norm_str(s.get("id")).lower())
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "value": v,
                "source_id": s.get("id"),
                "jurisdiction": s.get("jurisdiction"),
                "label": s.get("label"),
                "type": s.get("type"),
                "html_verified": bool(s.get("html_verified")),
                "html_status": s.get("html_status"),
            }
        )
    return rows


def primary_field_value(
    sources: Sequence[Dict[str, Any]],
    field: str,
) -> Optional[str]:
    """
    Choose a primary display value for *field*.

    Prefers html-verified / report_html sources, then higher-ranked types,
    then first non-empty.
    """
    candidates = collect_field_values(sources, field)
    if not candidates:
        return None

    def rank(c: Dict[str, Any]) -> Tuple[int, int]:
        verified = 1 if c.get("html_verified") else 0
        t_rank = _SOURCE_TYPE_RANK.get(str(c.get("type") or "").lower(), 0)
        return (verified, t_rank)

    candidates_sorted = sorted(candidates, key=rank, reverse=True)
    return candidates_sorted[0]["value"]


def multi_source_display(
    sources: Sequence[Dict[str, Any]],
    field: str,
    *,
    sep: str = " | ",
) -> str:
    """
    Human-readable multi-source field: ``W [FL·csv] | Asian [CO·html]``.
    Dedupes by canonical race key when field is race.
    """
    rows = collect_field_values(sources, field)
    if not rows:
        return ""
    # Collapse identical canonical values but keep first source tag
    by_canon: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        canon = _race_key(r["value"]) if field == "race" else r["value"].casefold()
        if canon not in by_canon:
            by_canon[canon] = r
        else:
            # Prefer verified
            if r.get("html_verified") and not by_canon[canon].get("html_verified"):
                by_canon[canon] = r

    if len(by_canon) == 1:
        only = next(iter(by_canon.values()))
        # Single race confirmed by online scrape → mark verified
        if only.get("html_verified") and field == "race":
            from scraper.database.sources_race_verify import format_verified_race

            return format_verified_race(only["value"])
        return only["value"]

    # Verified online charts first so primary race is not buried under bulk CSV
    ordered = sorted(
        by_canon.values(),
        key=lambda r: (0 if r.get("html_verified") else 1, str(r.get("value") or "")),
    )
    parts: List[str] = []
    for r in ordered:
        jur = (r.get("jurisdiction") or "").strip().upper()
        st = str(r.get("type") or "").lower()
        kind = {
            "csv_bulk": "csv",
            "report_html": "html",
            "nsopw_report": "html",
            "nsopw": "nsopw",
            "inferred": "inf",
        }.get(st, st or "?")
        tag = f"{jur}·{kind}" if jur else kind
        verified = "✓" if r.get("html_verified") else ""
        parts.append(f"{r['value']} [{tag}{verified}]")
    return sep.join(parts)


def apply_sources_to_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Write sources_json + multi-source race (and related) onto *record* in place.

    - race becomes multi-tagged display when sources disagree
    - primary (verified) race kept when only formatting differs
    - flags get multi_source_race / multi_source tags
    """
    sources = parse_sources(record.get("sources_json"))
    if not sources:
        return record

    record["sources_json"] = dumps_sources(sources)

    # Multi-source race representation
    race_disp = multi_source_display(sources, "race")

    # When every online chart (html-verified) agrees, that is the listed race.
    # Bulk CSV letters that disagree (often a wrong PERSON_NBR join) stay in
    # sources_json for provenance only — they must not show as co-listed White.
    from scraper.database.sources_race_verify import (
        format_verified_race,
        html_race_consensus,
    )

    race_consensus = html_race_consensus(sources, require_verified=True)
    if race_consensus:
        record["race"] = format_verified_race(race_consensus)
    elif race_disp:
        record["race"] = race_disp

    # Fill blanks from best primary when empty (never treat race as ethnicity)
    for field in ("ethnicity", "gender", "height", "weight", "eye_color", "hair_color"):
        if not _norm_str(record.get(field)):
            pv = primary_field_value(sources, field)
            if pv:
                # Guard: do not copy race codes into ethnicity
                if field == "ethnicity" and _race_key(str(pv)) in (
                    "WHITE", "BLACK", "ASIAN", "UNKNOWN", "AMERICAN INDIAN",
                ):
                    # Letter-only or pure race words are not ethnicity
                    if re.fullmatch(
                        r"(?i)W|B|A|I|U|White|Black|Asian|Unknown|Caucasian|"
                        r"African American|Black or African American",
                        str(pv).strip(),
                    ):
                        continue
                record[field] = pv

    # Flags
    try:
        flags_raw = record.get("flags")
        if isinstance(flags_raw, list):
            flags_list = [str(x) for x in flags_raw]
            flags_mode = "list"
            flags_dict: Dict[str, Any] = {}
        elif isinstance(flags_raw, dict):
            flags_list = [str(x) for x in (flags_raw.get("tags") or [])]
            flags_mode = "dict"
            flags_dict = dict(flags_raw)
        elif flags_raw:
            try:
                parsed = json.loads(str(flags_raw))
                if isinstance(parsed, list):
                    flags_list = [str(x) for x in parsed]
                    flags_mode = "list"
                    flags_dict = {}
                elif isinstance(parsed, dict):
                    flags_list = [str(x) for x in (parsed.get("tags") or [])]
                    flags_mode = "dict"
                    flags_dict = dict(parsed)
                else:
                    flags_list = [str(flags_raw)]
                    flags_mode = "list"
                    flags_dict = {}
            except json.JSONDecodeError:
                flags_list = [str(flags_raw)]
                flags_mode = "list"
                flags_dict = {}
        else:
            flags_list = []
            flags_mode = "list"
            flags_dict = {}
    except Exception:
        flags_list = []
        flags_mode = "list"
        flags_dict = {}

    def _ensure_tag(tag: str) -> None:
        if tag not in flags_list:
            flags_list.append(tag)

    def _drop_tag(tag: str) -> None:
        while tag in flags_list:
            flags_list.remove(tag)

    _ensure_tag("multi_source")
    race_vals = collect_field_values(sources, "race")
    canons = {_race_key(r["value"]) for r in race_vals}
    if len(canons) > 1:
        _ensure_tag("multi_source_race")
    if any(not r.get("html_verified") for r in race_vals):
        _ensure_tag("source_html_unverified")
    else:
        _drop_tag("source_html_unverified")

    if race_consensus:
        _ensure_tag("race_html_verified")
        # All online charts confirm the same race
        if len(canons) <= 1:
            _drop_tag("multi_source_race")
    else:
        _drop_tag("race_html_verified")

    if flags_mode == "dict":
        flags_dict["tags"] = flags_list
        flags_dict["source_count"] = len(sources)
        record["flags"] = json.dumps(flags_dict, ensure_ascii=False)
    else:
        record["flags"] = json.dumps(flags_list, ensure_ascii=False)

    return record


def attach_source_to_record(
    record: Dict[str, Any],
    source: Dict[str, Any],
    *,
    prefer_new_fields: bool = False,
    apply_display: bool = True,
) -> Dict[str, Any]:
    """Merge one source into record['sources_json'] and refresh display fields."""
    sources = parse_sources(record.get("sources_json"))
    sources = merge_source_into_list(sources, source, prefer_new_fields=prefer_new_fields)
    record["sources_json"] = dumps_sources(sources)
    if apply_display:
        apply_sources_to_record(record)
    return record


def source_from_record_snapshot(
    record: Dict[str, Any],
    *,
    source_type: str,
    jurisdiction: str = "",
    origin: str = "",
    label: str = "",
    html_verified: bool = False,
    html_status: str = "pending",
) -> Dict[str, Any]:
    """Build a source entry from the current top-level fields of a record."""
    jur = (
        jurisdiction
        or _norm_str(record.get("source_state"))
        or _norm_str(record.get("state"))
        or jurisdiction_from_url(_norm_str(record.get("source_url")))
    )
    # Multi-state "CO | FL" — take first token for jurisdiction tag
    if " | " in jur:
        jur = jur.split(" | ", 1)[0].strip()
    fields = extract_tracked_fields(record)
    # Don't put multi-tagged race back into a single source as the raw value
    # if it already looks multi-tagged
    race = fields.get("race")
    if race and "[" in str(race) and "]" in str(race):
        # Prefer first segment before |
        fields["race"] = str(race).split("|", 1)[0].strip()
        # strip trailing tag if present like "W [FL·csv]"
        fields["race"] = re.sub(r"\s*\[[^\]]+\]\s*$", "", fields["race"]).strip()

    return make_source(
        source_type=source_type,
        jurisdiction=jur,
        origin=origin,
        label=label,
        external_id=_norm_str(record.get("external_id")),
        source_url=_norm_str(record.get("source_url")).split(" | ")[0]
        if record.get("source_url")
        else "",
        fields=fields,
        html_path=_norm_str(record.get("report_html_path")) or None,
        html_verified=html_verified,
        html_status=html_status,
    )


def infer_source_type(record: Dict[str, Any]) -> Tuple[str, str, str]:
    """
    Infer (source_type, origin, html_status) for a legacy untagged row.
    """
    flags = str(record.get("flags") or "").lower()
    url = _norm_str(record.get("source_url"))
    html = _norm_str(record.get("report_html_path"))
    raw = _norm_str(record.get("raw_data_json"))
    race = _norm_str(record.get("race"))
    height = _norm_str(record.get("height"))

    if "nsopw" in flags or (raw.startswith("{") and '"givenName"' in raw):
        if html:
            return "nsopw_report", "nsopw", "ok" if race and "[" not in race else "pending"
        return "nsopw", "nsopw", "pending" if url else "no_url"
    if html and url:
        return "report_html", "report", "pending"
    if url:
        return "report_html", "url", "pending"
    # Letter race + 3-digit height → classic FDLE bulk style
    if re.fullmatch(r"[WBAIU]", race.upper()) and re.fullmatch(r"\d{3}", height):
        return "csv_bulk", "fl_sor_style", "no_url"
    if race and race.upper() in ("WHITE", "BLACK", "ASIAN OR PACIFIC ISLANDER"):
        return "csv_bulk", "ga_sor_style", "no_url"
    return "inferred", "legacy", "no_url"


def fl_person_url(person_nbr: str) -> str:
    """
    FDLE flyer URL from an id.

    **Do not call this with FL bulk PERSON_NBR.** PERSON_NBR is not flyer
    ``personId`` — synthesizing ``flyer.jsf?personId=<PERSON_NBR>`` attaches
    the wrong person (e.g. Ossiel Zuniga /5478 → Jose Triana flyer).

    Only use when *person_nbr* is a confirmed flyer personId (NSOPW URI or
    verified HTML). Prefer returning empty and leaving source_url unset for
    bulk CSV imports.
    """
    # Disabled by default: callers that still pass PERSON_NBR must not invent URLs.
    # Explicit opt-in only via fl_flyer_url_from_person_id().
    _ = person_nbr
    return ""


def fl_flyer_url_from_person_id(person_id: str) -> str:
    """Build FDLE flyer URL only from a confirmed flyer personId (not PERSON_NBR)."""
    n = (person_id or "").strip()
    if not n or not re.fullmatch(r"\d+", n):
        return ""
    return f"https://offender.fdle.state.fl.us/offender/sops/flyer.jsf?personId={n}"


def format_sources_detail(sources: Sequence[Dict[str, Any]]) -> List[str]:
    """Lines for the detail drawer."""
    if not sources:
        return ["Sources: —"]
    lines = [f"Sources ({len(sources)}):"]
    for i, s in enumerate(sources, 1):
        jur = s.get("jurisdiction") or "?"
        label = s.get("label") or s.get("type") or "source"
        verified = "html✓" if s.get("html_verified") else f"html:{s.get('html_status') or '—'}"
        lines.append(f"  {i}. [{jur}] {label} ({verified})")
        fields = s.get("fields") or {}
        race = fields.get("race")
        if race:
            lines.append(f"      race={race}")
        for key in ("gender", "height", "weight", "date_of_birth", "city", "state"):
            if fields.get(key):
                lines.append(f"      {key}={fields[key]}")
        if s.get("source_url"):
            lines.append(f"      url={s['source_url']}")
        if s.get("html_path"):
            lines.append(f"      html={s['html_path']}")
        if s.get("origin"):
            lines.append(f"      origin={s['origin']}")
    return lines
