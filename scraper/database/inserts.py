"""Insert / identity normalization operations."""
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


class InsertMixin:
    # ---- Insert operations ----

    def normalize_record_identity(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Write-time identity cleanup: strip session tokens from URLs and prefer
        stable registry Ids in external_id (prevents NSOPW uid= duplicates).
        Also fixes NSOPW junk state codes like ``YY`` using source_state.
        """
        out = dict(record or {})
        url = str(out.get("source_url") or "").strip()
        ext = str(out.get("external_id") or "").strip()
        if url:
            norm = self.normalize_identity_url(url)
            if norm:
                # Prefer original scheme/host casing from norm (already lowercased)
                out["source_url"] = norm
        # Stable external id from URL Id= when possible
        key = self.stable_external_key(out)
        if key and "|reg:" in key:
            out["external_id"] = key.split("|reg:", 1)[1]
        elif ext:
            norm_ext = self.normalize_identity_url(ext)
            if norm_ext:
                out["external_id"] = norm_ext
        elif url:
            norm = self.normalize_identity_url(url)
            if norm:
                out["external_id"] = norm
        # Drop junk NSOPW location.state codes (YY seen on many FL rows)
        try:
            from scraper.nsopw_client import normalize_jurisdiction_code

            fixed = normalize_jurisdiction_code(out.get("state"), out.get("source_state"))
            if fixed:
                out["state"] = fixed
                if not out.get("source_state") or str(out.get("source_state")).upper() in (
                    "YY", "XX", "ZZ", "US",
                ):
                    out["source_state"] = fixed
            elif str(out.get("state") or "").strip().upper() in ("YY", "XX", "ZZ"):
                # Prefer URL host inference for FDLE etc.
                u = str(out.get("source_url") or "").lower()
                if "fdle.state.fl" in u or "florida" in u:
                    out["state"] = "FL"
                    out["source_state"] = out.get("source_state") or "FL"
                else:
                    out["state"] = None
        except Exception:
            st = str(out.get("state") or "").strip().upper()
            if st in ("YY", "XX", "ZZ"):
                src = str(out.get("source_state") or "").strip().upper()
                if src and src not in ("YY", "XX", "ZZ", "US"):
                    out["state"] = src
        return out

    @staticmethod
    def extract_middle_name_parts(rec: Dict[str, Any]) -> Dict[str, str]:
        """
        Derive first/middle/last patches from full_name, multi-token first_name,
        or NSOPW raw_data_json middleName. Returns only fields that should change.
        """
        patch: Dict[str, str] = {}
        cur_mid = str(rec.get("middle_name") or "").strip()
        first = str(rec.get("first_name") or "").strip()
        last = str(rec.get("last_name") or "").strip()
        full = str(rec.get("full_name") or "").strip()

        # 1) NSOPW / API payload
        raw_mid = ""
        raw_blob = rec.get("raw_data_json")
        if raw_blob:
            try:
                raw = json.loads(raw_blob) if isinstance(raw_blob, str) else (raw_blob or {})
                if isinstance(raw, dict):
                    name_obj = raw.get("name") if isinstance(raw.get("name"), dict) else {}
                    raw_mid = str(
                        name_obj.get("middleName")
                        or raw.get("middleName")
                        or raw.get("middle_name")
                        or ""
                    ).strip()
                    if not first:
                        g = str(name_obj.get("givenName") or "").strip()
                        if g:
                            patch["first_name"] = g
                            first = g
                    if not last:
                        s = str(name_obj.get("surName") or "").strip()
                        if s:
                            patch["last_name"] = s
                            last = s
            except Exception:
                raw_mid = ""

        if raw_mid and not cur_mid:
            patch["middle_name"] = raw_mid
            cur_mid = raw_mid

        # 2) Multi-token first_name → first + middle
        if first and not cur_mid:
            fparts = first.split()
            if len(fparts) >= 2:
                patch["first_name"] = fparts[0]
                patch["middle_name"] = " ".join(fparts[1:])
                first = fparts[0]
                cur_mid = patch["middle_name"]

        # 3) full_name — support "FIRST MIDDLE LAST" and "LAST, FIRST MIDDLE"
        if full and not cur_mid:
            if "," in full:
                # LAST, FIRST [MIDDLE…]
                left, right = full.split(",", 1)
                left_t = left.strip()
                right_parts = right.strip().split()
                if left_t and right_parts:
                    if not last:
                        patch["last_name"] = left_t
                        last = left_t
                    if not first:
                        patch["first_name"] = right_parts[0]
                        first = right_parts[0]
                    if len(right_parts) >= 2:
                        mid = " ".join(right_parts[1:]).strip()
                        if mid:
                            patch["middle_name"] = mid
                            cur_mid = mid
            if not cur_mid:
                parts = full.replace(",", " ").split()
                if len(parts) >= 3:
                    # Align with known last/first when present
                    if last and parts[-1].casefold() == last.casefold():
                        if not first or parts[0].casefold() == first.split()[0].casefold():
                            mid = " ".join(parts[1:-1]).strip()
                            if mid:
                                patch["middle_name"] = mid
                                cur_mid = mid
                            if not first:
                                patch["first_name"] = parts[0]
                        elif first:
                            try:
                                fi = next(
                                    i for i, t in enumerate(parts[:-1])
                                    if t.casefold() == first.split()[0].casefold()
                                )
                                mid = " ".join(parts[fi + 1 : -1]).strip()
                                if mid:
                                    patch["middle_name"] = mid
                                    cur_mid = mid
                            except StopIteration:
                                pass
                    elif not last:
                        patch.setdefault("first_name", parts[0])
                        patch["middle_name"] = " ".join(parts[1:-1])
                        patch["last_name"] = parts[-1]
                        cur_mid = patch["middle_name"]

        # Rebuild full_name if we gained middle and full lacks it
        if cur_mid:
            f = patch.get("first_name") or first
            l = patch.get("last_name") or last
            if f and l:
                rebuilt = f"{f} {cur_mid} {l}".strip()
                if not full or cur_mid.casefold() not in full.casefold():
                    patch["full_name"] = rebuilt

        return patch

    def backfill_middle_names(self, *, limit: Optional[int] = None) -> Dict[str, int]:
        """
        Populate middle_name (and split multi-token first names) from existing
        full_name / first_name / raw_data_json. Returns counts.

        Default cap is 5_000 rows per call so a GUI refresh cannot load the
        whole table (often 100k+ candidates + raw_data_json) into RAM.
        Pass ``limit=0`` for an uncapped CLI repair pass.
        """
        # 0 = explicit unlimited; None = safe default; >0 = hard cap
        if limit is None:
            cap: Optional[int] = 5000
        elif int(limit) <= 0:
            cap = None
        else:
            cap = int(limit)
        sql = (
            "SELECT id, first_name, middle_name, last_name, full_name, raw_data_json "
            "FROM offenders "
            "WHERE middle_name IS NULL OR TRIM(middle_name) = '' "
            "   OR (first_name IS NOT NULL AND instr(trim(first_name), ' ') > 0)"
        )
        if cap is not None:
            sql += f" LIMIT {cap}"
        # Iterate cursor — do not fetchall() 100k+ rows with raw_data_json
        updated = 0
        scanned = 0
        cur = self._conn.execute(sql)
        while True:
            row = cur.fetchone()
            if row is None:
                break
            scanned += 1
            rec = dict(row)
            patch = self.extract_middle_name_parts(rec)
            if not patch:
                continue
            cols = []
            vals: List[Any] = []
            for k, v in patch.items():
                old = str(rec.get(k) or "").strip()
                new = str(v or "").strip()
                if new and new != old:
                    cols.append(f"{k} = ?")
                    vals.append(new)
            if not cols:
                continue
            vals.append(int(rec["id"]))
            self._conn.execute(
                f"UPDATE offenders SET {', '.join(cols)} WHERE id = ?",
                vals,
            )
            updated += 1
            if updated % 500 == 0:
                self._conn.commit()
        if updated:
            self._conn.commit()
        return {"scanned": scanned, "updated": updated}

    def repair_bogus_states(self) -> int:
        """
        Fix existing rows where state is YY/XX/ZZ but source_state (or URL) is real.

        Returns number of rows updated.
        """
        try:
            from scraper.nsopw_client import normalize_jurisdiction_code
        except Exception:
            normalize_jurisdiction_code = None  # type: ignore

        rows = self._conn.execute(
            """
            SELECT id, state, source_state, source_url FROM offenders
            WHERE UPPER(TRIM(COALESCE(state, ''))) IN ('YY', 'XX', 'ZZ', 'NA', 'UN')
               OR (
                 (state IS NULL OR TRIM(state) = '')
                 AND source_state IS NOT NULL AND TRIM(source_state) != ''
               )
            """
        ).fetchall()
        n = 0
        for row in rows:
            d = dict(row)
            fixed = None
            if normalize_jurisdiction_code:
                fixed = normalize_jurisdiction_code(d.get("state"), d.get("source_state"))
            if not fixed:
                src = str(d.get("source_state") or "").strip().upper()
                if src and src not in ("YY", "XX", "ZZ", "US", "NA"):
                    fixed = src
            if not fixed:
                u = str(d.get("source_url") or "").lower()
                if "fdle.state.fl" in u:
                    fixed = "FL"
            if not fixed:
                continue
            self._conn.execute(
                "UPDATE offenders SET state = ?, source_state = COALESCE(NULLIF(TRIM(source_state), ''), ?) "
                "WHERE id = ?",
                (fixed, fixed, int(d["id"])),
            )
            n += 1
        if n:
            self._conn.commit()
        return n

    def insert_offender(self, record: Dict[str, Any]) -> int:
        """Insert a single offender record. Returns the row id."""
        record = self.normalize_record_identity(record)
        cursor = self._conn.cursor()
        cursor.execute(_OFFENDER_INSERT_SQL, _record_to_insert_tuple(record))
        self._conn.commit()
        return cursor.lastrowid

    def insert_offenders_batch(self, records: List[Dict[str, Any]]) -> int:
        """Insert multiple offender records. Returns count inserted."""
        if not records:
            return 0
        cleaned = [self.normalize_record_identity(r) for r in records]
        cursor = self._conn.cursor()
        cursor.executemany(
            _OFFENDER_INSERT_SQL,
            [_record_to_insert_tuple(r) for r in cleaned],
        )
        self._conn.commit()
        return cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else len(cleaned)

