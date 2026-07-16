"""HTML-scrape race consensus and verified display helpers."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPORT_TYPES = frozenset({"report_html", "nsopw_report"})


def _norm_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def race_key(val: str) -> str:
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
        "AMERICAN INDIAN": "AMERICAN INDIAN",
        "NATIVE AMERICAN": "AMERICAN INDIAN",
        "U": "UNKNOWN",
        "UNKNOWN": "UNKNOWN",
        "H": "HISPANIC",
        "HISPANIC": "HISPANIC",
        "HISPANIC OR LATINO": "HISPANIC",
        "LATINO": "HISPANIC",
    }
    return aliases.get(t, t)


def _prefer_race_label(a: str, b: str) -> str:
    """Prefer spelled-out race labels over single-letter codes."""
    sa, sb = (a or "").strip(), (b or "").strip()
    if not sa:
        return sb
    if not sb:
        return sa
    # Longer non-code wins (Black over B, White over W)
    score_a = (0 if len(sa) <= 1 else 2) + (1 if sa[0].isupper() and len(sa) > 1 else 0)
    score_b = (0 if len(sb) <= 1 else 2) + (1 if sb[0].isupper() and len(sb) > 1 else 0)
    return sa if score_a >= score_b else sb


def collect_html_chart_races(
    sources: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Race values from online report charts (HTML-verified or report-type sources).

    These are the 'charts' that live scrape can confirm — not bulk CSV rows.
    """
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for s in sources:
        if not isinstance(s, dict):
            continue
        fields = s.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        val = fields.get("race")
        if val is None or not str(val).strip():
            continue
        st = str(s.get("type") or "").strip().lower()
        verified = bool(s.get("html_verified"))
        is_report = st in _REPORT_TYPES
        if not verified and not is_report:
            continue
        v = str(val).strip()
        key = (race_key(v), _norm_str(s.get("id")).lower(), st)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "value": v,
                "source_id": s.get("id"),
                "jurisdiction": s.get("jurisdiction"),
                "label": s.get("label"),
                "type": st,
                "html_verified": verified,
            }
        )
    return rows


def html_race_consensus(
    sources: Sequence[Dict[str, Any]],
    *,
    require_verified: bool = True,
) -> Optional[str]:
    """
    When every online chart that reports race agrees, return that race label.

    If require_verified, only html_verified charts count (current online scrape OK).
    """
    charts = collect_html_chart_races(sources)
    if require_verified:
        charts = [c for c in charts if c.get("html_verified")]
    if not charts:
        return None
    keys = {race_key(c["value"]) for c in charts}
    if len(keys) != 1:
        return None
    label = charts[0]["value"]
    for c in charts[1:]:
        label = _prefer_race_label(label, c["value"])
    return label


def format_verified_race(label: str) -> str:
    """Display mark for race confirmed by online scrape consensus."""
    base = (label or "").strip()
    if not base:
        return ""
    if base.endswith("✓") or "html✓" in base:
        return base
    return f"{base} ✓"


def parse_report_enrichment_race(raw_data_json: Any) -> Tuple[Optional[str], Dict[str, Any]]:
    """Extract race + demo fields from raw_data_json.report_enrichment."""
    import json

    if not raw_data_json:
        return None, {}
    try:
        if isinstance(raw_data_json, dict):
            raw = raw_data_json
        else:
            raw = json.loads(str(raw_data_json))
    except (TypeError, json.JSONDecodeError):
        return None, {}
    if not isinstance(raw, dict):
        return None, {}
    demo = raw.get("report_enrichment")
    if not isinstance(demo, dict):
        return None, {}
    race = demo.get("race")
    race_s = str(race).strip() if race is not None else ""
    fields: Dict[str, Any] = {}
    for key in (
        "race",
        "ethnicity",
        "gender",
        "height",
        "weight",
        "eye_color",
        "hair_color",
        "photo_path",
        "photo_url",
        "report_html_path",
    ):
        val = demo.get(key)
        if val is None or val == "":
            continue
        fields[key] = val if not isinstance(val, str) else val.strip()
    return (race_s or None), fields


def sources_have_verified_race(sources: Sequence[Dict[str, Any]]) -> bool:
    """True if any html-verified chart already carries a race value."""
    for s in sources:
        if not isinstance(s, dict):
            continue
        if not s.get("html_verified"):
            continue
        race = (s.get("fields") or {}).get("race")
        if race is not None and str(race).strip():
            return True
    return False


def scrub_bulk_race_conflicting_with_html(record: Dict[str, Any]) -> bool:
    """
    Drop bulk CSV race when it conflicts with html-verified race *and* the bulk
    source DOB conflicts with the record (wrong PERSON_NBR / flyer join).

    Example: FL CSV PERSON_NBR 119449 = Josue Ferreira W / 1967 attached to
    Antonio Jackson Black / 1983 whose flyer is personId=119449.

    Returns True when sources_json was modified.
    """
    from scraper.database.identity import dobs_compatible
    from scraper.database.sources import apply_sources_to_record, dumps_sources, parse_sources

    sources = parse_sources(record.get("sources_json"))
    if not sources:
        return False
    consensus = html_race_consensus(sources, require_verified=True)
    if not consensus:
        return False
    ckey = race_key(consensus)
    rec_dob = record.get("date_of_birth")
    changed = False
    for s in sources:
        if not isinstance(s, dict):
            continue
        st = str(s.get("type") or "").strip().lower()
        if st not in ("csv_bulk", "inferred", "nsopw"):
            continue
        if s.get("html_verified"):
            continue
        fields = dict(s.get("fields") or {})
        br = fields.get("race")
        if br is None or not str(br).strip():
            continue
        if race_key(str(br)) == ckey:
            continue
        bdob = fields.get("date_of_birth")
        if not rec_dob or not bdob:
            continue
        if dobs_compatible(rec_dob, bdob) is not False:
            continue
        # Conflicting bulk demographics — not the same person as this listing
        fields.pop("race", None)
        s["fields"] = fields
        origin = str(s.get("origin") or "").strip()
        if "race_scrubbed" not in origin:
            s["origin"] = f"{origin}+race_scrubbed" if origin else "race_scrubbed"
        changed = True
    if not changed:
        return False
    record["sources_json"] = dumps_sources(sources)
    apply_sources_to_record(record)
    return True


def recover_report_enrichment_into_sources(record: Dict[str, Any]) -> bool:
    """
    Re-attach live-scrape race from raw_data_json.report_enrichment when
    sources_json lost the report chart (e.g. CSV re-tag collapsed by URL).

    Returns True when sources were updated.
    """
    from scraper.database.sources import (
        attach_source_to_record,
        jurisdiction_from_url,
        make_source,
        parse_sources,
    )
    from scraper.reports.identity_gate import demo_identity_ok

    race, fields = parse_report_enrichment_race(record.get("raw_data_json"))
    if not race and not fields.get("race"):
        return False
    if not fields.get("race") and race:
        fields["race"] = race

    # Build a mini-demo for identity gate (reject Jose Triana on Ossiel, etc.)
    demo_probe: Dict[str, Any] = {
        "report_fetch_ok": True,
        "report_html_path": record.get("report_html_path"),
        "race": fields.get("race"),
    }
    try:
        import json as _json

        raw = record.get("raw_data_json")
        raw_d = raw if isinstance(raw, dict) else _json.loads(str(raw or "{}"))
        en = (raw_d or {}).get("report_enrichment") or {}
        if en.get("report_html_path"):
            demo_probe["report_html_path"] = en.get("report_html_path")
        if en.get("full_name") or en.get("name"):
            demo_probe["full_name"] = en.get("full_name") or en.get("name")
        if en.get("identity_ok") is False:
            return False
    except Exception:
        pass
    # Prefer page name; if enrichment stored full_name matching record, allow
    ok, _reason = demo_identity_ok(record, demo_probe)
    if not ok:
        return False

    sources = parse_sources(record.get("sources_json"))
    if sources_have_verified_race(sources):
        return False

    # Prefer enrichment URL, else record flyer
    url = ""
    try:
        import json

        raw = record.get("raw_data_json")
        raw_d = raw if isinstance(raw, dict) else json.loads(str(raw or "{}"))
        demo = (raw_d or {}).get("report_enrichment") or {}
        url = (
            str(demo.get("report_final_url") or demo.get("report_url") or "").strip()
        )
    except Exception:
        url = ""
    if not url:
        url = str(record.get("source_url") or "").split(" | ")[0].strip()

    jur = (
        str(record.get("state") or record.get("source_state") or "")
        .split(" | ")[0]
        .strip()
        .upper()
    )
    if not jur and url:
        jur = jurisdiction_from_url(url)

    html_path = (
        fields.pop("report_html_path", None)
        or record.get("report_html_path")
        or None
    )
    # photo fields stay in fields if present
    report_src = make_source(
        source_type="report_html",
        jurisdiction=jur or "UNK",
        origin="report_enrichment_recover",
        label=f"{jur or 'Registry'} report HTML",
        external_id=str(record.get("external_id") or ""),
        source_url=url,
        fields=fields,
        html_path=str(html_path) if html_path else None,
        html_verified=True,
        html_status="ok",
    )
    attach_source_to_record(record, report_src, prefer_new_fields=True)
    return True
