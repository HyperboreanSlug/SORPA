"""Search and integrity query operations."""
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


class QueryMixin:
    # ---- Query operations ----

    def search_by_name(
        self,
        name: str,
        state: Optional[str] = None,
        race: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Search offenders by name (partial match on first and last)."""
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        query = "SELECT * FROM offenders WHERE 1=1"
        params: List[Any] = []

        # Escape LIKE metacharacters so user input is literal (not wildcards)
        escaped = _escape_like(name or "")
        search_term = f"%{escaped}%"
        query += (
            " AND (full_name LIKE ? ESCAPE '\\' OR first_name LIKE ? ESCAPE '\\' "
            "OR last_name LIKE ? ESCAPE '\\')"
        )
        params.extend([search_term, search_term, search_term])

        if state and state.upper() != "ALL":
            query = self._append_state_filter(query, params, state)

        if race:
            # Multi-source race may look like ``W [FL·csv] | Asian [CO·html]``
            query += (
                " AND (UPPER(COALESCE(race, '')) = UPPER(?) "
                "OR UPPER(COALESCE(race, '')) LIKE ? ESCAPE '\\' "
                "OR UPPER(COALESCE(sources_json, '')) LIKE ? ESCAPE '\\')"
            )
            like = f"%{_escape_like((race or '').upper())}%"
            params.extend([race, like, like])

        query += " ORDER BY last_name ASC, first_name ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def search_by_race(
        self,
        race: str,
        state: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Search offenders by race (case-insensitive).

        Special token ``INDIAN`` matches race/ethnicity/likely_ethnicity fields
        that look South Asian / Indian (not only exact race = INDIAN).

        Multi-source rows (``W [FL·csv] | Asian [CO·html]``) match via LIKE on
        race and sources_json.
        """
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        race_key = (race or "").strip().upper()

        if race_key in ("INDIAN", "ASIAN INDIAN", "SOUTH ASIAN", "ASIAN/INDIAN"):
            # Broad match: registry race codes + stored name-ethnicity tags
            query = """
                SELECT * FROM offenders WHERE (
                    UPPER(COALESCE(race, '')) LIKE '%INDIAN%'
                    OR UPPER(COALESCE(race, '')) LIKE '%SOUTH ASIAN%'
                    OR UPPER(COALESCE(ethnicity, '')) LIKE '%INDIAN%'
                    OR UPPER(COALESCE(ethnicity, '')) LIKE '%SOUTH ASIAN%'
                    OR UPPER(COALESCE(likely_ethnicity, '')) LIKE '%INDIAN%'
                    OR UPPER(COALESCE(sources_json, '')) LIKE '%INDIAN%'
                )
            """
            params: List[Any] = []
        else:
            like = f"%{_escape_like(race_key)}%"
            query = """
                SELECT * FROM offenders WHERE (
                    UPPER(COALESCE(race, '')) = UPPER(?)
                    OR UPPER(COALESCE(race, '')) LIKE ? ESCAPE '\\'
                    OR UPPER(COALESCE(sources_json, '')) LIKE ? ESCAPE '\\'
                )
            """
            params = [race, like, like]

        if state and state.upper() != "ALL":
            query = self._append_state_filter(query, params, state)

        query += " ORDER BY last_name ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def search_by_surname_list(
        self,
        surnames: List[str],
        state: Optional[str] = None,
        limit: int = 0,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Return offenders whose last_name (or full_name tail) is in *surnames*.

        *limit* ``0`` or negative means no row cap (all matches).
        Large surname lists are queried in chunks to stay under SQLite bind limits.
        """
        limit = int(limit) if limit is not None else 0
        offset = max(0, int(offset))
        cleaned = sorted({
            (s or "").strip() for s in (surnames or []) if (s or "").strip()
        }, key=str.lower)
        if not cleaned:
            return []

        # Chunk IN lists (last_name + full_name doubles placeholders per name)
        chunk_size = 400
        by_id: Dict[int, Dict[str, Any]] = {}
        for i in range(0, len(cleaned), chunk_size):
            chunk = cleaned[i : i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            lowers = [s.lower() for s in chunk]
            query = f"""
                SELECT * FROM offenders WHERE (
                    LOWER(COALESCE(last_name, '')) IN ({placeholders})
                    OR LOWER(TRIM(COALESCE(full_name, ''))) IN ({placeholders})
                )
            """
            params: List[Any] = list(lowers) + list(lowers)
            if state and state.upper() != "ALL":
                query = self._append_state_filter(query, params, state)
            query += " ORDER BY last_name ASC"
            for row in self._conn.execute(query, params).fetchall():
                d = dict(row)
                rid = d.get("id")
                if rid is not None:
                    by_id[int(rid)] = d
                else:
                    by_id[id(d)] = d

        rows = sorted(
            by_id.values(),
            key=lambda r: (
                (r.get("last_name") or "").lower(),
                (r.get("first_name") or "").lower(),
                int(r.get("id") or 0),
            ),
        )
        if offset:
            rows = rows[offset:]
        if limit > 0:
            rows = rows[:limit]
        return rows

    def search_by_state(
        self,
        state: str,
        limit: int = 1000,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Search offenders by state. Use state='ALL' (or empty) to return any state.

        Matches either ``state`` or ``source_state`` so imports/scrapes that only
        set one column still appear in filters. Also matches multi-state merged
        values (e.g. ``FL | TX`` when filtering for FL).
        """
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        if not state or state.upper() == "ALL":
            # Named rows first — NULL last_name sorts first in plain ASC and
            # made the default Browse view look empty (dashes only).
            query = (
                "SELECT * FROM offenders "
                "ORDER BY CASE WHEN last_name IS NULL "
                "OR TRIM(COALESCE(last_name, '')) = '' THEN 1 ELSE 0 END, "
                "last_name ASC LIMIT ? OFFSET ?"
            )
            rows = self._conn.execute(query, (limit, offset)).fetchall()
        else:
            params: List[Any] = []
            query = "SELECT * FROM offenders WHERE 1=1"
            query = self._append_state_filter(query, params, state)
            query += " ORDER BY last_name ASC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_race_distribution(self) -> List[Dict[str, Any]]:
        """Get count of offenders by race."""
        query = """
            SELECT race, COUNT(*) as count
            FROM offenders
            GROUP BY race
            ORDER BY count DESC
        """
        rows = self._conn.execute(query).fetchall()
        return [dict(row) for row in rows]

    def get_state_distribution(self) -> List[Dict[str, Any]]:
        """Get count of offenders by state."""
        query = """
            SELECT state, COUNT(*) as count
            FROM offenders
            GROUP BY state
            ORDER BY count DESC
        """
        rows = self._conn.execute(query).fetchall()
        return [dict(row) for row in rows]

    def get_total_count(self) -> int:
        """Get total number of offender records."""
        result = self._conn.execute("SELECT COUNT(*) FROM offenders").fetchone()
        return result[0] if result else 0

    def get_offender_by_id(self, row_id: int) -> Optional[Dict[str, Any]]:
        """Return one offender row by primary key."""
        row = self._conn.execute(
            "SELECT * FROM offenders WHERE id = ?", (int(row_id),)
        ).fetchone()
        return dict(row) if row else None

    def update_offender(self, row_id: int, fields: Dict[str, Any]) -> bool:
        """Update selected columns on an offender row. Returns True if a row changed.

        Retries automatically on SQLite ``database is locked`` / busy errors
        (common during long NSOPW enrich while the GUI also holds the DB).
        """
        if not fields:
            return False
        # Only allow known columns
        allowed = set(_OFFENDER_INSERT_COLUMNS) | {"scraped_at"}
        cols = [k for k in fields if k in allowed and k != "id"]
        if not cols:
            return False
        sets = ", ".join(f"{c} = ?" for c in cols)
        vals = [fields[c] for c in cols]
        vals.append(int(row_id))

        def _once() -> bool:
            cur = self._conn.execute(
                f"UPDATE offenders SET {sets} WHERE id = ?",
                vals,
            )
            self._conn.commit()
            return (cur.rowcount or 0) > 0

        try:
            from scraper.database.db_retry import retry_on_db_lock

            changed = retry_on_db_lock(
                _once,
                what=f"update_offender id={row_id}",
            )
        except Exception:
            # Preserve prior behavior for non-lock errors: let caller handle
            raise
        if changed:
            try:
                from scraper.db_publish_pending import add_pending_listings

                add_pending_listings(1)
            except Exception:
                pass
        return changed

    def get_integrity_report(self) -> Dict[str, Any]:
        """
        Coverage stats for archive quality: race, crime, photo, HTML by state.

        Returns:
          {
            total, with_race, with_crime, with_photo, with_html, with_url,
            by_state: [{state, total, with_race, with_crime, with_photo, with_html, ...pct}],
          }
        """
        def _pct(n: int, d: int) -> float:
            return round(100.0 * n / d, 1) if d else 0.0

        total = self.get_total_count()
        row = self._conn.execute(
            """
            SELECT
              SUM(CASE WHEN race IS NOT NULL AND TRIM(race) != '' THEN 1 ELSE 0 END) AS with_race,
              SUM(CASE WHEN
                    (crime IS NOT NULL AND TRIM(crime) != '')
                    OR (offense_description IS NOT NULL AND TRIM(offense_description) != '')
                    OR (offense_type IS NOT NULL AND TRIM(offense_type) != '')
                  THEN 1 ELSE 0 END) AS with_crime,
              SUM(CASE WHEN photo_path IS NOT NULL AND TRIM(photo_path) != '' THEN 1 ELSE 0 END) AS with_photo,
              SUM(CASE WHEN report_html_path IS NOT NULL AND TRIM(report_html_path) != '' THEN 1 ELSE 0 END) AS with_html,
              SUM(CASE WHEN source_url IS NOT NULL AND TRIM(source_url) != '' THEN 1 ELSE 0 END) AS with_url,
              SUM(CASE WHEN
                    race IS NOT NULL AND TRIM(race) != ''
                    AND (
                      (crime IS NOT NULL AND TRIM(crime) != '')
                      OR (offense_description IS NOT NULL AND TRIM(offense_description) != '')
                      OR (offense_type IS NOT NULL AND TRIM(offense_type) != '')
                    )
                    AND photo_path IS NOT NULL AND TRIM(photo_path) != ''
                    AND report_html_path IS NOT NULL AND TRIM(report_html_path) != ''
                  THEN 1 ELSE 0 END) AS with_everything
            FROM offenders
            """
        ).fetchone()
        overall = {
            "total": total,
            "with_race": int(row["with_race"] or 0) if row else 0,
            "with_crime": int(row["with_crime"] or 0) if row else 0,
            "with_photo": int(row["with_photo"] or 0) if row else 0,
            "with_html": int(row["with_html"] or 0) if row else 0,
            "with_url": int(row["with_url"] or 0) if row else 0,
            "with_everything": int(row["with_everything"] or 0) if row else 0,
        }
        for key in ("with_race", "with_crime", "with_photo", "with_html", "with_url", "with_everything"):
            overall[f"pct_{key[5:]}"] = _pct(overall[key], total)

        by_state_rows = self._conn.execute(
            """
            SELECT
              COALESCE(NULLIF(TRIM(UPPER(state)), ''), NULLIF(TRIM(UPPER(source_state)), ''), 'UNK') AS st,
              COUNT(*) AS total,
              SUM(CASE WHEN race IS NOT NULL AND TRIM(race) != '' THEN 1 ELSE 0 END) AS with_race,
              SUM(CASE WHEN
                    (crime IS NOT NULL AND TRIM(crime) != '')
                    OR (offense_description IS NOT NULL AND TRIM(offense_description) != '')
                    OR (offense_type IS NOT NULL AND TRIM(offense_type) != '')
                  THEN 1 ELSE 0 END) AS with_crime,
              SUM(CASE WHEN photo_path IS NOT NULL AND TRIM(photo_path) != '' THEN 1 ELSE 0 END) AS with_photo,
              SUM(CASE WHEN report_html_path IS NOT NULL AND TRIM(report_html_path) != '' THEN 1 ELSE 0 END) AS with_html,
              SUM(CASE WHEN source_url IS NOT NULL AND TRIM(source_url) != '' THEN 1 ELSE 0 END) AS with_url
            FROM offenders
            GROUP BY st
            ORDER BY total DESC
            """
        ).fetchall()
        by_state = []
        for r in by_state_rows:
            d = dict(r)
            t = int(d.get("total") or 0)
            entry = {
                "state": d.get("st") or "UNK",
                "total": t,
                "with_race": int(d.get("with_race") or 0),
                "with_crime": int(d.get("with_crime") or 0),
                "with_photo": int(d.get("with_photo") or 0),
                "with_html": int(d.get("with_html") or 0),
                "with_url": int(d.get("with_url") or 0),
            }
            entry["pct_race"] = _pct(entry["with_race"], t)
            entry["pct_crime"] = _pct(entry["with_crime"], t)
            entry["pct_photo"] = _pct(entry["with_photo"], t)
            entry["pct_html"] = _pct(entry["with_html"], t)
            by_state.append(entry)

        return {"overall": overall, "by_state": by_state}

    def _incomplete_report_filters(
        self,
        *,
        need_race: bool = True,
        need_crime: bool = True,
        need_photo: bool = True,
        need_html: bool = False,
        require_url: bool = True,
        state: Optional[str] = None,
    ) -> Tuple[str, List[Any]]:
        """Return (where_sql_with_leading_WHERE, params) for incomplete rows."""
        clauses = ["1=1"]
        params: List[Any] = []
        if require_url:
            clauses.append("source_url IS NOT NULL AND TRIM(source_url) != ''")
        missing = []
        if need_race:
            missing.append("(race IS NULL OR TRIM(race) = '')")
        if need_crime:
            missing.append(
                "("
                "(crime IS NULL OR TRIM(crime) = '') AND "
                "(offense_description IS NULL OR TRIM(offense_description) = '') AND "
                "(offense_type IS NULL OR TRIM(offense_type) = '')"
                ")"
            )
        if need_photo:
            missing.append("(photo_path IS NULL OR TRIM(photo_path) = '')")
        if need_html:
            missing.append("(report_html_path IS NULL OR TRIM(report_html_path) = '')")
        if missing:
            clauses.append("(" + " OR ".join(missing) + ")")
        where = "WHERE " + " AND ".join(clauses)
        if state and str(state).upper() != "ALL":
            # _append_state_filter appends AND (…) and extends params
            where = self._append_state_filter(where, params, state)
        return where, params

    def count_incomplete_reports(
        self,
        *,
        need_race: bool = True,
        need_crime: bool = True,
        need_photo: bool = True,
        need_html: bool = False,
        require_url: bool = True,
        state: Optional[str] = None,
    ) -> int:
        """Count rows that would be returned by ``find_incomplete_reports``."""
        where, params = self._incomplete_report_filters(
            need_race=need_race,
            need_crime=need_crime,
            need_photo=need_photo,
            need_html=need_html,
            require_url=require_url,
            state=state,
        )
        row = self._conn.execute(
            f"SELECT COUNT(*) FROM offenders {where}", params
        ).fetchone()
        return int(row[0] if row else 0)

    def find_incomplete_reports(
        self,
        *,
        need_race: bool = True,
        need_crime: bool = True,
        need_photo: bool = True,
        need_html: bool = False,
        require_url: bool = True,
        limit: int = 500,
        state: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Records that have a report URL but are missing selected enrichments.
        Used for failed-report requeue / enrich.

        *limit*:
          - ``> 0`` — return at most that many rows
          - ``<= 0`` — no row cap (process / list all matching rows)
        """
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = 500
        where, params = self._incomplete_report_filters(
            need_race=need_race,
            need_crime=need_crime,
            need_photo=need_photo,
            need_html=need_html,
            require_url=require_url,
            state=state,
        )
        # When scoped to a state, prefer registry hosts that match that state
        # (e.g. FL → fdle.state.fl.us) over cross-jurisdiction shells / captcha
        # pages attached to residents. Avoid bare id DESC (newest-first thrash).
        order_params: List[Any] = list(params)
        st_code = (str(state).strip().upper() if state else "")
        if st_code and st_code != "ALL" and len(st_code) <= 3:
            needle = st_code.lower()
            sql = (
                f"SELECT * FROM offenders {where} ORDER BY "
                f"CASE "
                f"WHEN lower(source_url) LIKE ? THEN 0 "
                f"WHEN lower(source_url) LIKE '%captcha%' THEN 2 "
                f"ELSE 1 END, "
                f"(photo_path IS NOT NULL AND TRIM(photo_path) != '') ASC, "
                f"id ASC"
            )
            order_params = list(params) + [f"%{needle}%"]
        else:
            sql = f"SELECT * FROM offenders {where} ORDER BY id ASC"
        if lim > 0:
            sql += " LIMIT ?"
            order_params = list(order_params) + [lim]
        rows = self._conn.execute(sql, order_params).fetchall()
        return [dict(r) for r in rows]

