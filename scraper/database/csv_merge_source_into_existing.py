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

class MergeSourceIntoExistingCsvMixin:
    def _merge_source_into_existing(
        self,
        row_id: int,
        incoming: Dict[str, Any],
        *,
        commit: bool = True,
    ) -> bool:
        """Merge sources_json (+ fill blanks) from *incoming* into existing row."""
        from scraper.database.identity import score_identity_match, should_merge_records
        from scraper.database.sources import (
            apply_sources_to_record,
            dumps_sources,
            merge_sources_lists,
        )

        existing = self.get_offender_by_id(int(row_id)) if hasattr(self, "get_offender_by_id") else None
        if existing is None:
            row = self._conn.execute(
                "SELECT * FROM offenders WHERE id = ?", (int(row_id),)
            ).fetchone()
            existing = dict(row) if row else None
        if not existing:
            return False

        # Guard: never merge when DOB/middle/name conflict (even if caller passed id).
        # FL PERSON_NBR can collide with flyer personId for a different person —
        # hard reject must always win over shared external_id / URL.
        _ok, _sc, reasons = should_merge_records(incoming, existing, min_score=6)
        _s, _r, hard = score_identity_match(incoming, existing)
        if hard:
            return False
        if not _ok and "external_id" not in (_r or []):
            ext_i = str(incoming.get("external_id") or "").strip()
            ext_e = str(existing.get("external_id") or "").strip()
            if not (ext_i and ext_e and ext_i.casefold() == ext_e.casefold()):
                return False

        merged_sources = merge_sources_lists(
            existing.get("sources_json"),
            incoming.get("sources_json"),
        )
        patch: Dict[str, Any] = {
            "sources_json": dumps_sources(merged_sources),
        }
        # Build a temp record for display race
        temp = dict(existing)
        temp["sources_json"] = patch["sources_json"]
        apply_sources_to_record(temp)
        if temp.get("race") and temp.get("race") != existing.get("race"):
            existing_race = str(existing.get("race") or "")
            existing_verified = (
                "✓" in existing_race
                or "race_html_verified" in str(existing.get("flags") or "")
            )
            temp_verified = "✓" in str(temp.get("race") or "")
            # Keep an HTML-verified race; do not clobber it with bulk letter race.
            if not (existing_verified and not temp_verified):
                patch["race"] = temp["race"]
        if temp.get("flags"):
            patch["flags"] = temp["flags"]

        # Union multi-listing fields without clobbering
        for col in ("state", "source_state", "source_url", "external_id", "photo_url"):
            inc = incoming.get(col)
            cur = existing.get(col)
            if not inc:
                continue
            if not cur:
                patch[col] = inc
            elif str(inc).strip() and str(inc).strip() not in str(cur):
                # append if distinct
                if str(inc).strip().lower() not in str(cur).lower():
                    patch[col] = f"{cur}{_MERGE_SEP}{inc}"

        # Fill blank physical fields from incoming only when empty
        for col in (
            "height", "weight", "eye_color", "hair_color", "gender",
            "date_of_birth", "age", "city", "address", "zip_code", "county",
            "photo_path", "report_html_path", "crime",
        ):
            if existing.get(col) in (None, "") and incoming.get(col) not in (None, ""):
                patch[col] = incoming[col]

        if not patch:
            return False
        # update without mandatory commit for bulk merge performance
        allowed = set(_OFFENDER_INSERT_COLUMNS) | {"scraped_at"}
        cols = [k for k in patch if k in allowed and k != "id"]
        if not cols:
            return False
        sets = ", ".join(f"{c} = ?" for c in cols)
        vals = [patch[c] for c in cols]
        vals.append(int(row_id))
        cur = self._conn.execute(
            f"UPDATE offenders SET {sets} WHERE id = ?",
            vals,
        )
        if commit:
            self._conn.commit()
        return (cur.rowcount or 0) > 0

