from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from scraper.database.csv_helpers import *  # noqa: F401,F403
from scraper.database.constants import (
    SCHEMA_VERSION,
    DUPLICATE_STRATEGIES,
    DEFAULT_DEDUPE_STRATEGIES,
    _VOLATILE_URL_PARAMS,
    _MERGE_SEP,
    _MERGE_UNION_FIELDS,
    DEFAULT_DB_PATH,
    _OFFENDER_INSERT_COLUMNS,
    _OFFENDER_INSERT_SQL,
    _record_to_insert_tuple,
    _utc_now_iso,
    _escape_like,
)

class TagRecordSourceCsvMixin:
    def _tag_record_source(
        self,
        record: Dict[str, Any],
        *,
        source_hint: Optional[str] = None,
        jurisdiction: str = "",
        source_type: str = "csv_bulk",
    ) -> None:
        """Attach a sources_json entry for this import and refresh multi-source race."""
        from scraper.database.sources import (
            attach_source_to_record,
            make_source,
            extract_tracked_fields,
        )

        jur = (
            jurisdiction
            or (record.get("source_state") or "")
            or (record.get("state") or "")
            or ""
        )
        jur = str(jur).strip().upper()
        if " | " in jur:
            jur = jur.split(" | ", 1)[0].strip()

        origin = (source_hint or "").strip() or "import"
        ext = str(record.get("external_id") or record.get("person_nbr") or "").strip()
        url = str(record.get("source_url") or "").strip()
        # Never invent FDLE flyer URLs from PERSON_NBR — that id is not personId
        # and causes wrong-person scrapes (Ossiel Zuniga → Jose Triana).

        fields = extract_tracked_fields(record)
        src = make_source(
            source_type=source_type,
            jurisdiction=jur or "UNK",
            origin=origin,
            external_id=ext,
            source_url=url,
            fields=fields,
            html_path=str(record.get("report_html_path") or "") or None,
            html_verified=False,
            html_status="pending" if url else "no_url",
        )
        # Never clobber an existing html-verified chart race with bulk codes
        attach_source_to_record(record, src, prefer_new_fields=False)

