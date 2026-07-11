"""CSV import/export for offender records."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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


class CsvMixin:
    # ---- CSV import/export ----

    @staticmethod
    def _infer_csv_jurisdiction(path_stem: str, state: Optional[str] = None) -> str:
        """Map filename stem → jurisdiction code (fl_sor → FL, sor → GA, …)."""
        if state and str(state).strip():
            return str(state).strip().upper()
        stem = (path_stem or "").lower().strip()
        stem = stem.replace("_offenders", "").replace("_data", "").replace("-", "_")
        aliases = {
            "fl": "FL",
            "fl_sor": "FL",
            "florida": "FL",
            "florida_sor": "FL",
            "ga": "GA",
            "ga_sor": "GA",
            "sor": "GA",  # GA GBI bulk often named sor.csv
            "georgia": "GA",
            "az": "AZ",
            "dc": "DC",
            "co": "CO",
        }
        if stem in aliases:
            return aliases[stem]
        if len(stem) == 2 and stem.isalpha():
            return stem.upper()
        # fl_sor_export → FL
        for key, code in aliases.items():
            if stem.startswith(key + "_") or stem.endswith("_" + key):
                return code
        return ""

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
            fl_person_url,
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
        # FDLE bulk: synthesize flyer URL when we only have PERSON_NBR
        if not url and jur == "FL" and ext and ext.isdigit():
            url = fl_person_url(ext)
            if url and not record.get("source_url"):
                record["source_url"] = url

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
        attach_source_to_record(record, src, prefer_new_fields=True)

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
                # Bulk registry CSV: source_state = publishing jurisdiction;
                # residential state may differ (FL SOR lists out-of-state addresses).
                record.setdefault("source_state", state)
                if not record.get("state"):
                    record["state"] = state
            if not record.get("source_state") and record.get("state"):
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
                            "SELECT id FROM offenders WHERE source_url = ? OR source_url LIKE ? LIMIT 1",
                            (url, f"%{url}%"),
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

    def _build_name_merge_index(self) -> Dict[str, List[int]]:
        """Map ``LAST|FIRST`` → list of row ids (for CSV merge-into-existing)."""
        idx: Dict[str, List[int]] = defaultdict(list)
        cur = self._conn.execute(
            "SELECT id, first_name, last_name FROM offenders "
            "WHERE last_name IS NOT NULL AND TRIM(last_name) != ''"
        )
        for row in cur:
            last = str(row[2] or "").strip().casefold()
            first = str(row[1] or "").strip().casefold()
            if not last:
                continue
            key = f"{last}|{first.split()[0] if first else ''}"
            idx[key].append(int(row[0]))
        return idx

    def _find_merge_target(
        self,
        rec: Dict[str, Any],
        name_index: Dict[str, List[int]],
    ) -> Optional[int]:
        """
        Find an existing row that is the same person as *rec*.

        Uses multi-identifier scoring (``scraper.database.identity``):
          - external_id match (if no DOB/middle hard conflict)
          - first + last + DOB / middle / height+weight / location
          - **never** first+last alone
          - hard-reject on conflicting DOB or middle names
            (e.g. NIRAJ V + 1978  vs  NIRAJ RASHMIBABU + 1973)
        """
        from scraper.database.identity import should_merge_records, score_identity_match

        ext = str(rec.get("external_id") or "").strip()
        if ext:
            rows = self._conn.execute(
                "SELECT * FROM offenders WHERE external_id = ? LIMIT 5",
                (ext,),
            ).fetchall()
            if len(rows) == 1:
                existing = dict(rows[0])
                ok, _score, reasons = should_merge_records(
                    rec, existing, min_score=5, unique_name_candidate=True
                )
                # Same registry id: allow unless hard reject
                _, _, hard = score_identity_match(rec, existing)
                if not hard:
                    return int(existing["id"])
                return None
            if len(rows) > 1:
                # Prefer best multi-id score among same-ext rows
                best_id, best_sc = None, -1
                for r in rows:
                    existing = dict(r)
                    ok, sc, _ = should_merge_records(rec, existing, min_score=5)
                    _, _, hard = score_identity_match(rec, existing)
                    if hard:
                        continue
                    if sc > best_sc:
                        best_sc, best_id = sc, int(existing["id"])
                return best_id

        last = str(rec.get("last_name") or "").strip().casefold()
        first = str(rec.get("first_name") or "").strip().casefold()
        if not last or not first:
            return None
        key = f"{last}|{first.split()[0]}"
        candidates = name_index.get(key) or []
        if not candidates:
            return None

        placeholders = ",".join("?" * len(candidates[:80]))
        rows = self._conn.execute(
            f"SELECT * FROM offenders WHERE id IN ({placeholders})",
            candidates[:80],
        ).fetchall()
        unique_name = len(candidates) == 1
        best_id = None
        best_score = -1
        for r in rows:
            existing = dict(r)
            ok, sc, reasons = should_merge_records(
                rec,
                existing,
                min_score=6,
                unique_name_candidate=unique_name,
            )
            if not ok:
                continue
            if sc > best_score:
                best_score = sc
                best_id = int(existing["id"])
        return best_id

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

        # Guard: never merge when DOB/middle conflict (even if caller passed id)
        _ok, _sc, reasons = should_merge_records(incoming, existing, min_score=5)
        _s, _r, hard = score_identity_match(incoming, existing)
        if hard or (not _ok and "external_id" not in (_r or [])):
            # Allow same external_id path only when not hard-rejected
            ext_i = str(incoming.get("external_id") or "").strip()
            ext_e = str(existing.get("external_id") or "").strip()
            if hard or not (ext_i and ext_e and ext_i.casefold() == ext_e.casefold()):
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

    def import_csv(
        self,
        csv_path: str,
        state: Optional[str] = None,
        *,
        skip_existing_urls: bool = True,
        merge_sources: bool = True,
    ) -> Dict[str, int]:
        """
        Import records from a CSV file.

        Returns dict: {imported, skipped, merged, total_rows}.
        When skip_existing_urls is True, rows with a source_url already in the DB
        are skipped (or source-merged when merge_sources is True).
        """
        import csv as csv_module

        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_path}")

        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv_module.DictReader(f)
            raw_rows = [dict(row) for row in reader]

        st = state
        if not st:
            st = self._infer_csv_jurisdiction(path.stem, None) or None

        return self.import_records(
            raw_rows,
            state=st,
            skip_existing_urls=skip_existing_urls,
            source_hint=path.stem,
            merge_sources=merge_sources,
        )

    def import_csv_directory(
        self,
        directory: str,
        *,
        skip_existing_urls: bool = True,
        merge_sources: bool = True,
        pattern: str = "*.csv",
    ) -> Dict[str, Any]:
        """Import all CSVs in a directory (e.g. data/downloads)."""
        root = Path(directory)
        if not root.is_dir():
            raise NotADirectoryError(str(root))
        files = sorted(root.glob(pattern))
        summary = {
            "files": 0,
            "imported": 0,
            "skipped": 0,
            "merged": 0,
            "total_rows": 0,
            "errors": [],
        }
        for f in files:
            try:
                st = self._infer_csv_jurisdiction(f.stem, None) or None
                r = self.import_csv(
                    str(f),
                    state=st,
                    skip_existing_urls=skip_existing_urls,
                    merge_sources=merge_sources,
                )
                summary["files"] += 1
                summary["imported"] += r.get("imported", 0)
                summary["skipped"] += r.get("skipped", 0)
                summary["merged"] += r.get("merged", 0)
                summary["total_rows"] += r.get("total_rows", 0)
            except Exception as e:
                summary["errors"].append(f"{f.name}: {e}")
        return summary

    def backfill_sources(
        self,
        *,
        limit: Optional[int] = None,
        only_missing: bool = True,
        log: Optional[Any] = None,
    ) -> Dict[str, int]:
        """
        Tag existing offender rows with at least one sources_json entry.

        Infers origin from URL / flags / bulk race-code fingerprint so legacy
        imports (e.g. FL SOR letter races) are provenance-tagged.
        """
        from scraper.database.sources import (
            apply_sources_to_record,
            infer_source_type,
            parse_sources,
            source_from_record_snapshot,
            jurisdiction_from_url,
            attach_source_to_record,
            make_source,
        )

        def _log(msg: str) -> None:
            if log:
                log(msg)

        sql = "SELECT * FROM offenders"
        if only_missing:
            sql += (
                " WHERE sources_json IS NULL OR TRIM(sources_json) = '' "
                "OR sources_json = '[]' OR sources_json = 'null'"
            )
        sql += " ORDER BY id ASC"
        params: Tuple = ()
        if limit and int(limit) > 0:
            sql += " LIMIT ?"
            params = (int(limit),)

        rows = self._conn.execute(sql, params).fetchall()
        updated = 0
        multi = 0
        for row in rows:
            rec = dict(row)
            existing = parse_sources(rec.get("sources_json"))
            if only_missing and existing:
                continue

            stype, origin, html_status = infer_source_type(rec)
            race = str(rec.get("race") or "").strip()
            height = str(rec.get("height") or "").strip()
            letter_race = bool(re.fullmatch(r"[WBAIU]", race.upper()))
            bulk_height = bool(re.fullmatch(r"\d{3}", height))
            looks_like_fl_bulk = letter_race and bulk_height

            urls = [
                u.strip()
                for u in str(rec.get("source_url") or "").split(" | ")
                if u.strip()
            ]

            # Case: bulk demographics (FL letter race) later enriched with a
            # jurisdiction URL (e.g. CO). Keep two sources so race is not
            # falsely attributed to the CO HTML link.
            if looks_like_fl_bulk and urls:
                bulk_fields = {
                    k: rec.get(k)
                    for k in (
                        "race", "height", "weight", "eye_color", "hair_color",
                        "gender", "date_of_birth",
                    )
                    if rec.get(k) not in (None, "")
                }
                bulk = make_source(
                    source_type="csv_bulk",
                    jurisdiction="FL",
                    origin="fl_sor",
                    label="FL SOR CSV (inferred)",
                    external_id=str(rec.get("external_id") or ""),
                    fields=bulk_fields,
                    html_verified=False,
                    html_status="no_url",
                )
                attach_source_to_record(rec, bulk, prefer_new_fields=False, apply_display=False)

                for u in urls:
                    j = jurisdiction_from_url(u) or str(rec.get("state") or "")
                    if " | " in str(j):
                        j = str(j).split(" | ", 1)[0].strip()
                    # URL-side source: location/identity, not bulk letter race
                    url_fields = {
                        k: rec.get(k)
                        for k in (
                            "gender", "date_of_birth", "age", "city", "address",
                            "zip_code", "county", "state", "photo_url", "photo_path",
                            "report_html_path", "crime",
                        )
                        if rec.get(k) not in (None, "")
                    }
                    # Prefer full-word races already on the row if not letter-only
                    if race and not letter_race:
                        url_fields["race"] = race
                    url_src = make_source(
                        source_type="nsopw_report" if "nsopw" in str(rec.get("flags") or "").lower() else "report_html",
                        jurisdiction=j or "UNK",
                        origin="source_url",
                        source_url=u,
                        external_id=str(rec.get("external_id") or ""),
                        fields=url_fields,
                        html_path=str(rec.get("report_html_path") or "") or None,
                        html_verified=False,
                        html_status="pending",
                    )
                    attach_source_to_record(
                        rec, url_src, prefer_new_fields=False, apply_display=False
                    )
                apply_sources_to_record(rec)
            elif len(urls) > 1:
                for u in urls:
                    j = jurisdiction_from_url(u) or str(rec.get("state") or "")
                    src = source_from_record_snapshot(
                        {**rec, "source_url": u, "state": j, "source_state": j},
                        source_type=stype,
                        jurisdiction=j,
                        origin=origin,
                        html_verified=False,
                        html_status="pending",
                    )
                    attach_source_to_record(
                        rec, src, prefer_new_fields=False, apply_display=False
                    )
                apply_sources_to_record(rec)
            else:
                jur = (
                    str(rec.get("source_state") or "").strip()
                    or jurisdiction_from_url(str(rec.get("source_url") or ""))
                    or str(rec.get("state") or "").strip()
                )
                if " | " in jur:
                    jur = jur.split(" | ", 1)[0].strip()
                html_verified = html_status == "ok"
                src = source_from_record_snapshot(
                    rec,
                    source_type=stype,
                    jurisdiction=jur,
                    origin=origin,
                    html_verified=html_verified,
                    html_status=html_status if urls else "no_url",
                )
                if origin == "fl_sor_style" or looks_like_fl_bulk:
                    src["label"] = "FL SOR CSV (inferred)"
                    src["jurisdiction"] = "FL"
                    src["origin"] = "fl_sor"
                    src["type"] = "csv_bulk"
                elif origin == "ga_sor_style":
                    src["label"] = "GA SOR CSV (inferred)"
                    src["jurisdiction"] = src.get("jurisdiction") or "GA"
                    src["origin"] = "sor"
                    src["type"] = "csv_bulk"
                attach_source_to_record(rec, src, prefer_new_fields=False)

            patch = {
                "sources_json": rec.get("sources_json"),
                "race": rec.get("race"),
                "flags": rec.get("flags"),
            }
            if self.update_offender(int(rec["id"]), patch):
                updated += 1
                if "multi_source_race" in str(rec.get("flags") or ""):
                    multi += 1
            if updated and updated % 10000 == 0:
                _log(f"  backfill_sources: {updated} rows…")

        _log(f"backfill_sources: updated={updated} multi_source_race≈{multi}")
        return {"updated": updated, "scanned": len(rows), "multi_source_race": multi}

    def export_to_csv(
        self,
        output_path: str,
        filters: Optional[Dict[str, Any]] = None
    ) -> int:
        """Export records to CSV. Returns count exported."""
        import csv as csv_module

        query = "SELECT * FROM offenders"
        params: List[Any] = []

        if filters:
            conditions = []
            if filters.get("state") and str(filters["state"]).upper() != "ALL":
                conditions.append("UPPER(state) = UPPER(?)")
                params.append(filters["state"])
            if filters.get("race"):
                conditions.append("UPPER(race) = UPPER(?)")
                params.append(filters["race"])
            if filters.get("name"):
                conditions.append(
                    "(full_name LIKE ? ESCAPE '\\' OR first_name LIKE ? ESCAPE '\\' "
                    "OR last_name LIKE ? ESCAPE '\\')"
                )
                term = f"%{_escape_like(str(filters['name']))}%"
                params.extend([term, term, term])
            if conditions:
                query += " WHERE " + " AND ".join(conditions)

        rows = self._conn.execute(query, params).fetchall()
        if not rows:
            # Write header-only file from known columns so callers don't crash
            fieldnames = ["id", *_OFFENDER_INSERT_COLUMNS, "scraped_at"]
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv_module.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
            return 0

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv_module.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))

        return len(rows)

    def _normalize_record(self, record: Dict[str, Any]) -> None:
        """Normalize common column name variations (incl. FDLE FL SOR bulk)."""
        name_map = {
            "Name": "full_name",
            "Offender Name": "full_name",
            "First Name": "first_name",
            "FirstName": "first_name",
            "FIRST_NAME": "first_name",
            "Middle Name": "middle_name",
            "MiddleName": "middle_name",
            "MIDDLE_NAME": "middle_name",
            "Middle": "middle_name",
            "Last Name": "last_name",
            "LastName": "last_name",
            "LAST_NAME": "last_name",
            "Race": "race",
            "RACE": "race",
            "Ethnicity": "ethnicity",
            "Gender": "gender",
            "SEX": "gender",
            "Sex": "gender",
            "Age": "age",
            "DOB": "date_of_birth",
            "Date of Birth": "date_of_birth",
            "BIRTH_DATE": "date_of_birth",
            "Height": "height",
            "HEIGHT": "height",
            "Weight": "weight",
            "WEIGHT": "weight",
            "Eye Color": "eye_color",
            "EYE_COLOR": "eye_color",
            "EYECOLOR": "eye_color",
            "Hair Color": "hair_color",
            "HAIR": "hair_color",
            "HAIR_COLOR": "hair_color",
            "HAIRCOLOR": "hair_color",
            "State": "state",
            "County": "county",
            "City": "city",
            "Address": "address",
            "Zip Code": "zip_code",
            "Zip": "zip_code",
            "ZIP": "zip_code",
            "Risk Level": "risk_level",
            "Crime": "crime",
            "Offense": "crime",
            "Offense Type": "offense_type",
            "Offense Description": "offense_description",
            "Charge": "crime",
            "Charges": "crime",
            "Source URL": "source_url",
            "URL": "source_url",
            "Photo": "photo_url",
            "Image": "photo_url",
            "IMAGE_URL": "photo_url",
            "PERSON_NBR": "external_id",
            "PERSON_NUMBER": "external_id",
            # FL permanent address columns
            "PERM_ADDRESS_LINE_1": "address",
            "PERM_CITY": "city",
            "PERM_STATE": "state",
            "PERM_ZIP5": "zip_code",
            "PERM_COUNTY": "county",
        }

        new_record: Dict[str, Any] = {}
        for key, value in record.items():
            if key is None:
                continue
            key_str = str(key).strip()
            if not key_str:
                continue
            normalized_key = name_map.get(
                key_str, name_map.get(key_str.upper(), key_str.lower().replace(" ", "_"))
            )
            if value is None or (isinstance(value, str) and not value.strip()):
                # Don't overwrite a mapped field with empty later columns
                if normalized_key not in new_record:
                    new_record[normalized_key] = None
            else:
                # Prefer first non-empty for address-style maps
                if normalized_key in new_record and new_record[normalized_key]:
                    continue
                new_record[normalized_key] = str(value).strip()

        # Coerce age to int when possible
        if new_record.get("age") is not None:
            try:
                new_record["age"] = int(float(str(new_record["age"]).strip()))
            except (TypeError, ValueError):
                pass

        # Derive name parts from full_name when missing
        if not new_record.get("last_name") and new_record.get("full_name"):
            parts = str(new_record["full_name"]).replace(",", " ").split()
            if len(parts) >= 3:
                new_record.setdefault("first_name", parts[0])
                new_record.setdefault("middle_name", " ".join(parts[1:-1]))
                new_record.setdefault("last_name", parts[-1])
            elif len(parts) >= 2:
                new_record.setdefault("first_name", parts[0])
                new_record.setdefault("last_name", parts[-1])
            elif parts:
                new_record.setdefault("last_name", parts[0])

        # Split multi-token first_name into first + middle when middle empty
        first = str(new_record.get("first_name") or "").strip()
        mid = str(new_record.get("middle_name") or "").strip()
        if first and not mid:
            fparts = first.split()
            if len(fparts) >= 2:
                new_record["first_name"] = fparts[0]
                new_record["middle_name"] = " ".join(fparts[1:])

        # Derive full_name from first+middle+last when scrapers export split names only
        if not new_record.get("full_name"):
            parts = [
                str(p).strip()
                for p in (
                    new_record.get("first_name"),
                    new_record.get("middle_name"),
                    new_record.get("last_name"),
                )
                if p and str(p).strip()
            ]
            if parts:
                new_record["full_name"] = " ".join(parts)

        # Keep source_state in sync when only state is present
        if new_record.get("state") and not new_record.get("source_state"):
            new_record["source_state"] = new_record["state"]

        # Preserve already-attached sources_json if present on the dict
        if record.get("sources_json") and not new_record.get("sources_json"):
            new_record["sources_json"] = record.get("sources_json")

        record.clear()
        record.update(new_record)


