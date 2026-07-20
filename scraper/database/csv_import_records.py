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

class ImportRecordsCsvMixin:
    def import_records(
        self,
        records: List[Dict[str, Any]],
        state: Optional[str] = None,
        *,
        skip_existing_urls: bool = True,
        source_hint: Optional[str] = None,
        merge_sources: bool = True,
    ) -> Dict[str, int]:
        """
        Import in-memory offender dicts (e.g. scrape results) into the DB.

        Same normalization / de-dupe rules as ``import_csv``.
        When *merge_sources* is True, matching existing rows receive a new
        sources_json contribution instead of a duplicate insert (or silent skip).

        Returns dict: {imported, skipped, merged, total_rows}.
        """
        from scraper.database.sources import (
            attach_source_to_record,
            dumps_sources,
            merge_sources_lists,
            apply_sources_to_record,
            parse_sources,
        )

        jur_hint = self._infer_csv_jurisdiction(source_hint or "", state)
        prepared: List[Dict[str, Any]] = []
        for row in records or []:
            if not isinstance(row, dict):
                continue
            record = dict(row)
            self._normalize_record(record)
            if state:
                # Bulk registry CSV: source_state = *publishing* jurisdiction.
                # Residential state may already be set from PERM_STATE / address
                # (FL SOR lists many out-of-state addresses) — do not overwrite
                # source_state with the residence code.
                pub = str(state).strip().upper()
                record["source_state"] = pub
                if not (record.get("state") or "").strip():
                    record["state"] = pub
            elif not record.get("source_state") and record.get("state"):
                record["source_state"] = record["state"]
            if (
                not record.get("state")
                and not record.get("source_state")
                and source_hint
            ):
                stem_j = self._infer_csv_jurisdiction(source_hint, None)
                if stem_j:
                    record["source_state"] = stem_j
                    if not record.get("state"):
                        record["state"] = stem_j
            if not record.get("crime"):
                record["crime"] = (
                    record.get("offense_description")
                    or record.get("offense_type")
                    or record.get("offense")
                    or record.get("charge")
                )
            # Tag provenance before insert/merge
            self._tag_record_source(
                record,
                source_hint=source_hint,
                jurisdiction=jur_hint
                or str(record.get("source_state") or record.get("state") or ""),
                source_type="csv_bulk",
            )
            prepared.append(record)

        total_rows = len(prepared)
        merged = 0
        skipped = 0

        if merge_sources and prepared:
            import sys
            import time as _time

            print(
                f"  Building name index for merge ({len(prepared)} CSV rows)…",
                flush=True,
            )
            t_idx = _time.time()
            name_index = self._build_name_merge_index()
            print(
                f"  Name index ready ({len(name_index)} keys) in "
                f"{_time.time() - t_idx:.1f}s — merging…",
                flush=True,
            )
            still: List[Dict[str, Any]] = []
            pending_commits = 0
            t_merge = _time.time()
            n_prep = len(prepared)
            for i, rec in enumerate(prepared, 1):
                hit_id = self._find_merge_target(rec, name_index)
                if hit_id is None:
                    still.append(rec)
                else:
                    ok = self._merge_source_into_existing(hit_id, rec, commit=False)
                    if ok:
                        merged += 1
                        pending_commits += 1
                        if pending_commits >= 500:
                            self._conn.commit()
                            pending_commits = 0
                    else:
                        still.append(rec)
                if i % 5000 == 0 or i == n_prep:
                    elapsed = max(0.001, _time.time() - t_merge)
                    rate = i / elapsed
                    print(
                        f"  merge progress {i}/{n_prep} "
                        f"merged={merged} unmatched={len(still)} "
                        f"({rate:.0f} rows/s)",
                        flush=True,
                    )
            if pending_commits:
                self._conn.commit()
            prepared = still
            print(
                f"  Merge phase done: merged={merged} left_to_insert={len(prepared)}",
                flush=True,
            )

        if skip_existing_urls:
            existing_urls = self.existing_source_urls()
            kept: List[Dict[str, Any]] = []
            for rec in prepared:
                url = (rec.get("source_url") or rec.get("external_id") or "").strip()
                norm = self.normalize_identity_url(url) if url else ""
                if url and (url in existing_urls or (norm and norm in existing_urls)):
                    # Try source merge onto the URL owner instead of pure skip
                    if merge_sources:
                        row = self._conn.execute(
                            "SELECT id FROM offenders WHERE source_url = ? LIMIT 1",
                            (url,),
                        ).fetchone()
                        if row:
                            if self._merge_source_into_existing(int(row[0]), rec):
                                merged += 1
                                continue
                    skipped += 1
                    continue
                if url:
                    existing_urls.add(url)
                    if norm:
                        existing_urls.add(norm)
                kept.append(rec)
            prepared = kept

        imported = self.insert_offenders_batch(prepared) if prepared else 0
        return {
            "imported": imported,
            "skipped": skipped,
            "merged": merged,
            "total_rows": total_rows,
        }

