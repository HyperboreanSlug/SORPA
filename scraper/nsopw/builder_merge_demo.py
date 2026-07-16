from __future__ import annotations

import json

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple


from scraper.nsopw.builder_types import *  # noqa: F401,F403
from scraper.database import Database
from scraper.ethnic_names import get_ethnic_database
from scraper.reports.fetcher import ReportFetcher
from scraper.nsopw.client import (
    DEFAULT_JURISDICTIONS,
    NSOPWClient,
    NSOPWOffender,
    normalize_jurisdiction_code,
)
from scraper.nsopw.parallel import JurisdictionReportPool, ReportJob

class BuilderMergeDemoMixin:
    def _merge_demographics(self, record: Dict[str, Any], demo: Dict[str, Any]) -> None:
        """
        Merge report-page demographics into *record* without erasing other sources.

        Race/ethnicity from HTML are stored as a separate sources_json entry so
        a bulk CSV value (e.g. FL ``W``) coexists with a jurisdiction value
        (e.g. CO ``Asian``). Top-level race is rewritten to a multi-source
        display when they disagree.
        """
        from scraper.database.sources import (
            TRACKED_FIELDS,
            attach_source_to_record,
            extract_tracked_fields,
            jurisdiction_from_url,
            make_source,
        )

        report_ok = bool(demo.get("report_fetch_ok"))
        url = (
            (demo.get("report_final_url") or demo.get("report_url") or record.get("source_url") or "")
            .strip()
        )
        if " | " in url:
            url = url.split(" | ", 1)[0].strip()
        jur = (
            (record.get("state") or record.get("source_state") or "")
            or jurisdiction_from_url(url)
        )
        if isinstance(jur, str) and " | " in jur:
            jur = jur.split(" | ", 1)[0].strip()
        jur = str(jur or "").strip().upper()

        # Identity gate: never merge demographics from a different person's flyer
        from scraper.reports.identity_gate import demo_identity_ok

        id_ok, id_reason = demo_identity_ok(record, demo)
        if report_ok and not id_ok:
            # Reject wrong-person HTML (e.g. PERSON_NBR flyer → Jose Triana for Ossiel)
            report_ok = False
            demo = dict(demo)
            demo["report_fetch_ok"] = False
            demo["report_block_reason"] = f"identity:{id_reason}"
            # Do not keep wrong photo/html as primary on the record
            for poison in ("photo_path", "photo_url", "report_html_path"):
                # only clear if demo was about to set them from this fetch
                pass

        # Field values observed on this report fetch (only non-empty)
        demo_fields: Dict[str, Any] = {}
        if id_ok:
            for key in TRACKED_FIELDS:
                val = demo.get(key)
                if val is None or val == "":
                    continue
                demo_fields[key] = val if not isinstance(val, str) else val.strip()
            # Also pull crime aliases
            if not demo_fields.get("crime"):
                for k in ("offense_description", "offense_type"):
                    if demo.get(k):
                        demo_fields["crime"] = str(demo.get(k)).strip()
                        break

        html_status = "ok" if (report_ok and id_ok) else "empty"
        if not id_ok and id_reason:
            html_status = f"identity:{id_reason}"
        elif demo.get("report_block_reason"):
            html_status = f"blocked:{demo.get('report_block_reason')}"
        elif str(demo.get("report_fetch_status") or "").startswith("error"):
            html_status = str(demo.get("report_fetch_status"))
        elif str(demo.get("report_fetch_status") or "").startswith("blocked"):
            html_status = str(demo.get("report_fetch_status"))

        report_src = make_source(
            source_type="report_html" if (report_ok and id_ok) else "nsopw_report",
            jurisdiction=jur,
            origin="report_fetch",
            label=f"{jur or 'Registry'} report HTML",
            external_id=str(record.get("external_id") or ""),
            source_url=url,
            fields=demo_fields,
            html_path=(
                (demo.get("report_html_path") or record.get("report_html_path") or None)
                if id_ok
                else None
            ),
            html_verified=bool(report_ok and id_ok and demo_fields.get("race")),
            html_status=html_status,
        )
        # Preserve any pre-existing sources (e.g. FL CSV) and add/update this one
        attach_source_to_record(record, report_src, prefer_new_fields=bool(id_ok))

        # Top-level fill only when identity matched — never copy wrong person
        if id_ok:
            for key in (
                "ethnicity", "gender", "height", "weight",
                "eye_color", "hair_color", "skin_tone", "build", "age",
                "date_of_birth", "county", "city", "address", "risk_level",
                "offense_type", "offense_description", "crime",
                "photo_path", "photo_url", "report_html_path",
            ):
                val = demo.get(key)
                if val is None or val == "":
                    continue
                if key in ("crime", "offense_type", "offense_description"):
                    if not record.get(key):
                        record[key] = val
                elif not record.get(key):
                    record[key] = val
                elif key in ("photo_path", "photo_url", "report_html_path"):
                    if not record.get(key):
                        record[key] = val

            # Keep crime in sync with offense fields if only one side was set
            if not record.get("crime"):
                odesc = (record.get("offense_description") or "").strip()
                otype = (record.get("offense_type") or "").strip()
                if odesc or otype:
                    record["crime"] = odesc or otype
        else:
            # Drop any previously attached wrong HTML for this poisoned URL
            from scraper.reports.identity_gate import strip_wrong_person_html

            strip_wrong_person_html(record, reason=id_reason)

        try:
            raw = json.loads(record.get("raw_data_json") or "{}")
            if not isinstance(raw, dict):
                raw = {}
        except json.JSONDecodeError:
            raw = {}
        # Preserve original NSOPW payload if present; nest enrichment
        enr = {
            k: demo.get(k)
            for k in (
                "report_url", "report_final_url", "report_resolved_url",
                "report_fetch_status", "report_fetch_ok", "report_html_path",
                "report_block_reason", "photo_path", "photo_url",
                "race", "ethnicity", "gender",
                "height", "weight", "hair_color", "eye_color",
            )
            if k in demo
        }
        enr["identity_ok"] = bool(id_ok)
        enr["identity_reason"] = id_reason
        if not id_ok:
            # Do not store wrong-person demographics as enrichment
            for k in (
                "race", "ethnicity", "gender", "height", "weight",
                "hair_color", "eye_color", "photo_path", "photo_url",
                "report_html_path",
            ):
                enr.pop(k, None)
        raw["report_enrichment"] = enr
        record["raw_data_json"] = json.dumps(raw, ensure_ascii=False)[:50000]

        try:
            flags = json.loads(record.get("flags") or "[]")
            if isinstance(flags, dict):
                tags = [str(t) for t in (flags.get("tags") or [])]
                flag_mode = "dict"
                flag_dict = flags
            elif isinstance(flags, list):
                tags = [str(t) for t in flags]
                flag_mode = "list"
                flag_dict = {}
            else:
                tags = [str(flags)]
                flag_mode = "list"
                flag_dict = {}
        except json.JSONDecodeError:
            tags = []
            flag_mode = "list"
            flag_dict = {}

        def _tag(t: str) -> None:
            if t not in tags:
                tags.append(t)

        if id_ok and demo.get("report_html_path"):
            _tag("html_archived")
        if id_ok and demo.get("photo_path"):
            _tag("photo_archived")
        if id_ok and demo.get("report_fetch_ok"):
            _tag("report_enriched")
        elif not id_ok:
            _tag("identity_html_mismatch")
            _tag(f"identity:{id_reason}"[:80])
            # Clear wrong-person archive pointers if still set
            if record.get("report_html_path") and not id_ok:
                # strip_wrong_person_html already ran; ensure no re-tag as enriched
                pass
        else:
            _tag("report_link_saved")
            if demo.get("report_block_reason"):
                _tag(f"blocked:{demo['report_block_reason']}")
        _tag("multi_source")

        if flag_mode == "dict":
            flag_dict["tags"] = tags
            record["flags"] = json.dumps(flag_dict, ensure_ascii=False)
        else:
            record["flags"] = json.dumps(tags)


