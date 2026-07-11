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

    Field maps are unioned; on conflict for the same field key, keep both only
    at the multi-source (list) level — within one source entry the newer value
    wins when prefer_new_fields or old is empty.
    """
    if not new_src:
        return list(sources)
    out = [dict(s) for s in sources if isinstance(s, dict)]
    new_id = _norm_str(new_src.get("id")).lower()
    new_url = _norm_str(new_src.get("source_url")).lower()
    new_ext = _norm_str(new_src.get("external_id")).lower()

    match_idx = None
    for i, s in enumerate(out):
        sid = _norm_str(s.get("id")).lower()
        if new_id and sid == new_id:
            match_idx = i
            break
        if new_url and _norm_str(s.get("source_url")).lower() == new_url:
            match_idx = i
            break
        if (
            new_ext
            and _norm_str(s.get("external_id")).lower() == new_ext
            and _norm_str(s.get("jurisdiction")).upper()
            == _norm_str(new_src.get("jurisdiction")).upper()
        ):
            match_idx = i
            break

    if match_idx is None:
        out.append(dict(new_src))
        return out

    cur = dict(out[match_idx])
    # Merge scalar metadata
    for key in (
        "type", "jurisdiction", "label", "origin", "external_id", "source_url",
        "html_path", "html_status",
    ):
        nv = new_src.get(key)
        if nv is not None and str(nv).strip():
            if prefer_new_fields or not _norm_str(cur.get(key)):
                cur[key] = nv
    if new_src.get("html_verified"):
        cur["html_verified"] = True
    elif "html_verified" in new_src and not cur.get("html_verified"):
        cur["html_verified"] = bool(new_src.get("html_verified"))

    # Merge field maps
    cur_fields = dict(cur.get("fields") or {})
    new_fields = dict(new_src.get("fields") or {})
    for k, v in new_fields.items():
        if v is None or (isinstance(v, str) and not str(v).strip()):
            continue
        old = cur_fields.get(k)
        if prefer_new_fields or old is None or not str(old).strip():
            cur_fields[k] = v
        # else keep old (same source shouldn't flip-flop without prefer_new)
    cur["fields"] = cur_fields
    cur["updated_at"] = _utc_now_iso()
    if new_src.get("id"):
        cur["id"] = new_src["id"]
    out[match_idx] = cur
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
    t = re.sub(r"[^A-Z]+", " ", (val or "").upper()).strip()
    aliases = {
        "W": "WHITE",
        "WHITE": "WHITE",
        "CAUCASIAN": "WHITE",
        "B": "BLACK",
        "BLACK": "BLACK",
        "AFRICAN AMERICAN": "BLACK",
        "A": "ASIAN",
        "ASIAN": "ASIAN",
        "ASIAN OR PACIFIC ISLANDER": "ASIAN",
        "ASIAN PACIFIC ISLANDER": "ASIAN",
        "API": "ASIAN",
        "I": "AMERICAN INDIAN",
        "U": "UNKNOWN",
        "UNKNOWN": "UNKNOWN",
        "H": "HISPANIC",
        "HISPANIC": "HISPANIC",
    }
    return aliases.get(t, t)


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
        return only["value"]

    parts: List[str] = []
    for r in by_canon.values():
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
    if race_disp:
        record["race"] = race_disp

    # Fill blanks from best primary when empty
    for field in ("ethnicity", "gender", "height", "weight", "eye_color", "hair_color"):
        if not _norm_str(record.get(field)):
            pv = primary_field_value(sources, field)
            if pv:
                record[field] = pv

    # Flags
    try:
        flags_raw = record.get("flags")
        if isinstance(flags_raw, list):
            flags_list = [str(x) for x in flags_raw]
            flags_mode = "list"
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
                elif isinstance(parsed, dict):
                    flags_list = [str(x) for x in (parsed.get("tags") or [])]
                    flags_mode = "dict"
                    flags_dict = dict(parsed)
                else:
                    flags_list = [str(flags_raw)]
                    flags_mode = "list"
            except json.JSONDecodeError:
                flags_list = [str(flags_raw)]
                flags_mode = "list"
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

    _ensure_tag("multi_source")
    race_vals = collect_field_values(sources, "race")
    canons = {_race_key(r["value"]) for r in race_vals}
    if len(canons) > 1:
        _ensure_tag("multi_source_race")
    if any(not r.get("html_verified") for r in race_vals):
        _ensure_tag("source_html_unverified")

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
    """Best-effort FDLE flyer URL from PERSON_NBR (may need personId, not always equal)."""
    n = (person_nbr or "").strip()
    if not n:
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
