"""Duplicate detection, merge, and removal."""
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


class DedupeMixin:
    # ---- Duplicate detection / removal ----

    @staticmethod
    def normalize_identity_url(url: Optional[str]) -> str:
        """
        Canonical URL for dedupe.

        Strips session/uid/token query params so the same offender page with
        different NSOPW ``uid`` values groups together. Keeps stable ids
        (``Id``, ``ImageId``, path segments).

        Florida FDLE: preserve camelCase ``personId=`` (all-lowercase
        ``personid=`` opens an empty/invalid flyer in the browser).
        """
        raw = (url or "").strip()
        if not raw:
            return ""
        # Multi-jurisdiction merges: normalize each segment separately
        if " | " in raw or (raw.count("http") > 1 and "|" in raw):
            try:
                from scraper.public_links import split_source_urls, resolve_public_source_url

                parts = split_source_urls(raw)
                if parts:
                    # Keep a single canonical public link for identity (prefer FDLE fix)
                    return resolve_public_source_url(raw).lower().replace(
                        "personid=", "personId="
                    )
            except Exception:
                pass
        try:
            p = urlparse(raw)
        except Exception:
            return raw.rstrip("/").lower()
        # Relative paths / non-http: still normalize query if present
        kept = [
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if k and k.lower() not in _VOLATILE_URL_PARAMS
        ]
        # FDLE: force camelCase personId key (JSF is case-sensitive)
        host_l = (p.netloc or "").lower()
        if "fdle.state.fl.us" in host_l:
            fixed_kept = []
            for k, v in kept:
                if k.lower() == "personid":
                    fixed_kept.append(("personId", v))
                else:
                    fixed_kept.append((k, v))
            kept = fixed_kept
        kept.sort(key=lambda kv: (kv[0].lower(), kv[1]))
        host = (p.netloc or "").lower()
        path = (p.path or "").rstrip("/") or "/"
        scheme = (p.scheme or "https").lower()
        if not host and not p.query and not p.path:
            return raw.rstrip("/").lower()
        # urlencode will emit personId as personId; do NOT lowercase query keys for FDLE
        query = urlencode(kept)
        out = urlunparse((scheme, host, path, "", query, ""))
        if "fdle.state.fl.us" in host:
            # Lowercase scheme/host/path only — keep personId casing
            out = f"{scheme}://{host}{path}" + (f"?{query}" if query else "")
            # Safety: rewrite any personid= that slipped through
            out = re.sub(r"(?i)([?&])personid=", r"\1personId=", out)
            return out
        return out.lower()

    @classmethod
    def stable_external_key(
        cls,
        record: Dict[str, Any],
        *,
        state_hint: Optional[str] = None,
    ) -> str:
        """
        Stable person/listing key for external_id strategy.

        Prefers explicit registry Id query params (e.g. GA ``Id=50604``), then
        normalized URL, then raw external_id text.
        """
        ext = str(record.get("external_id") or "").strip()
        url = str(record.get("source_url") or "").strip()
        state = (
            state_hint
            or record.get("state")
            or record.get("source_state")
            or ""
        )
        state_u = str(state).strip().upper()

        def _id_from(s: str) -> str:
            if not s:
                return ""
            try:
                qs = dict(parse_qsl(urlparse(s).query, keep_blank_values=True))
            except Exception:
                return ""
            for key in (
                "Id", "ID", "id", "OffenderId", "offenderId", "offender_id",
                "personId", "personid", "PersonId",
            ):
                # parse_qsl is case-sensitive; also scan case-insensitively
                if key in qs and str(qs[key]).strip():
                    return str(qs[key]).strip()
            for k, v in qs.items():
                if k.lower() == "personid" and str(v).strip():
                    return str(v).strip()
            # path tail numeric id: /offenders/12345
            try:
                path = urlparse(s).path or ""
            except Exception:
                path = ""
            m = re.search(r"/(\d{3,})/?$", path)
            if m:
                return m.group(1)
            return ""

        for candidate in (ext, url):
            oid = _id_from(candidate)
            if oid:
                return f"{state_u}|reg:{oid}".lower()

        norm = cls.normalize_identity_url(ext or url)
        if norm:
            return f"{state_u}|url:{norm}".lower()
        if ext:
            return f"{state_u}|raw:{ext.casefold()}"
        return ""

    # Shared CAPTCHA / search / portal URLs must not collapse many people into one.
    _GENERIC_URL_MARKERS = (
        "captcha",
        "login",
        "signin",
        "sign-in",
        "challenge",
        "cloudflare",
        "just a moment",
        "cf-browser",
        "accessdenied",
        "access-denied",
        "botdetect",
        "search-public",
        "publicregistrantsearch",
        "sor_public",
        "sort_public",
        "coveredoffender",  # Hawaii landing (often non-unique)
    )

    @classmethod
    def _url_has_stable_offender_id(cls, url: str) -> bool:
        """True if URL carries a person-specific Id (not a bare portal landing)."""
        raw = (url or "").strip()
        if not raw:
            return False
        try:
            p = urlparse(raw)
            qs = {k.lower(): v for k, v in parse_qsl(p.query, keep_blank_values=True)}
        except Exception:
            return False
        for key in (
            "id", "offenderid", "offender_id", "offenderid", "personid",
            "registrantid", "subjectid",
        ):
            val = (qs.get(key) or "").strip()
            if val and val.lower() not in ("0", "null", "none", "undefined"):
                return True
        # path …/offenders/12345
        path = (p.path or "").strip("/")
        if re.search(r"(?:^|/)(\d{3,})(?:/|$)", path):
            return True
        return False

    @classmethod
    def is_generic_source_url(cls, url: str, *, group_count: int = 1) -> bool:
        """
        True when *url* is likely a shared portal/CAPTCHA page, not a unique
        offender report. High fan-out groups are treated as generic too.

        Portal path markers (e.g. ``sort_public``) alone do **not** mark a URL
        generic when it includes a stable offender ``Id=`` query — those are
        real person pages that may only differ by session ``uid``.
        """
        u = (url or "").strip().lower()
        if not u:
            return True
        # Person-specific Id wins over portal path markers
        if cls._url_has_stable_offender_id(url):
            # Still unsafe if absurd fan-out (shared Id mis-scrape)
            if group_count >= 25:
                return True
            return False
        compact = re.sub(r"[\s_\-]+", "", u)
        for m in cls._GENERIC_URL_MARKERS:
            if m.replace("-", "").replace("_", "").replace(" ", "") in compact:
                return True
            if m in u:
                return True
        # Bare search home pages (no query / id segment)
        if group_count >= 8:
            return True
        # Extremely short path after host → landing page
        try:
            path = (urlparse(u).path or "").strip("/")
            if path.count("/") == 0 and len(path) < 12 and group_count > 2:
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _row_richness(row: Dict[str, Any]) -> int:
        """How complete a record is — higher is better when choosing a survivor."""
        score = 0
        for col, weight in (
            ("race", 3),
            ("crime", 2),
            ("offense_description", 2),
            ("offense_type", 1),
            ("photo_path", 3),
            ("report_html_path", 2),
            ("source_url", 2),
            ("photo_url", 1),
            ("ethnicity", 1),
            ("date_of_birth", 1),
            ("address", 1),
            ("county", 1),
            ("city", 1),
            ("gender", 1),
            ("risk_level", 1),
            ("state", 1),
        ):
            val = row.get(col)
            if val is not None and str(val).strip():
                score += weight
                # Slight boost for already-merged multi-value fields
                if _MERGE_SEP in str(val):
                    score += 1
        return score

    @staticmethod
    def _normalize_dup_key_part(value: Any) -> str:
        return " ".join(str(value or "").strip().lower().split())

    @staticmethod
    def _split_merged_values(value: Any) -> List[str]:
        """Split a field that may already contain ' | ' unions into distinct parts."""
        raw = str(value or "").strip()
        if not raw:
            return []
        parts: List[str] = []
        seen: set = set()
        for chunk in raw.split(_MERGE_SEP):
            # Also accept semicolon / newline lists from older scrapes
            for piece in re.split(r"[;\n]+", chunk):
                p = " ".join(piece.strip().split())
                if not p:
                    continue
                key = p.casefold()
                if key in seen:
                    continue
                seen.add(key)
                parts.append(p)
        return parts

    @classmethod
    def _union_field_values(cls, *values: Any) -> str:
        """Union distinct non-empty values, preserving first-seen order."""
        parts: List[str] = []
        seen: set = set()
        for v in values:
            for p in cls._split_merged_values(v):
                key = p.casefold()
                if key in seen:
                    continue
                seen.add(key)
                parts.append(p)
        return _MERGE_SEP.join(parts)

    @classmethod
    def merge_duplicate_members(
        cls,
        keep: Dict[str, Any],
        losers: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Build field updates that merge *losers* into *keep*.

        - Union multi-listing fields (states, crimes, addresses, URLs, …)
        - Fill blanks on identity/physical fields from any loser
        - Annotate flags with merged source row ids when useful

        Returns only columns that should change on the keeper.
        """
        if not losers:
            return {}

        updates: Dict[str, Any] = {}
        all_rows = [keep] + list(losers)

        # 1) Union multi-value / multi-listing fields
        for col in _MERGE_UNION_FIELDS:
            merged = cls._union_field_values(*(r.get(col) for r in all_rows))
            cur = str(keep.get(col) or "").strip()
            if merged and merged != cur:
                updates[col] = merged

        # 2) Fill blanks (prefer non-empty) for remaining insert columns
        for col in _OFFENDER_INSERT_COLUMNS:
            if col in _MERGE_UNION_FIELDS:
                continue
            if col == "flags":
                continue  # handled below
            if col == "raw_data_json":
                # Prefer non-empty JSON; do not concatenate
                cur = keep.get(col)
                if cur is not None and str(cur).strip():
                    continue
                for r in losers:
                    alt = r.get(col)
                    if alt is not None and str(alt).strip():
                        updates[col] = alt
                        break
                continue
            if col in ("photo_path", "report_html_path"):
                # Keep existing file path; only fill if blank
                cur = keep.get(col)
                if cur is not None and str(cur).strip():
                    continue
                for r in losers:
                    alt = r.get(col)
                    if alt is not None and str(alt).strip():
                        updates[col] = alt
                        break
                continue
            # Scalar: fill blank only
            cur = keep.get(col)
            if cur is not None and str(cur).strip():
                continue
            for r in losers:
                alt = r.get(col)
                if alt is not None and str(alt).strip():
                    updates[col] = alt
                    break

        # 3) flags: merge JSON lists/dicts + record merged ids
        flag_objs: List[Any] = []
        for r in all_rows:
            raw = r.get("flags")
            if raw is None or str(raw).strip() == "":
                continue
            if isinstance(raw, (list, dict)):
                flag_objs.append(raw)
                continue
            try:
                flag_objs.append(json.loads(str(raw)))
            except Exception:
                flag_objs.append(str(raw).strip())

        merged_ids = []
        for r in losers:
            try:
                merged_ids.append(int(r["id"]))
            except (KeyError, TypeError, ValueError):
                pass

        flag_out: Any = None
        if flag_objs:
            # Prefer a dict payload so we can attach metadata
            base: Dict[str, Any] = {}
            list_flags: List[str] = []
            for fo in flag_objs:
                if isinstance(fo, dict):
                    for k, v in fo.items():
                        if k in ("merged_from_ids", "merged_listings"):
                            continue
                        if k not in base:
                            base[k] = v
                        elif isinstance(base[k], list) and isinstance(v, list):
                            for item in v:
                                if item not in base[k]:
                                    base[k].append(item)
                elif isinstance(fo, list):
                    for item in fo:
                        s = str(item)
                        if s not in list_flags:
                            list_flags.append(s)
                else:
                    s = str(fo)
                    if s not in list_flags:
                        list_flags.append(s)
            if list_flags:
                base.setdefault("tags", list_flags)
            flag_out = base
        else:
            flag_out = {}

        if merged_ids:
            prev = flag_out.get("merged_from_ids") if isinstance(flag_out, dict) else None
            ids: List[int] = []
            if isinstance(prev, list):
                for x in prev:
                    try:
                        ids.append(int(x))
                    except (TypeError, ValueError):
                        pass
            for i in merged_ids:
                if i not in ids:
                    ids.append(i)
            flag_out["merged_from_ids"] = ids
            # Compact multi-state / multi-listing summary for UI
            states = cls._split_merged_values(
                updates.get("state", keep.get("state"))
            )
            crimes = cls._split_merged_values(
                updates.get("crime", keep.get("crime"))
            )
            urls = cls._split_merged_values(
                updates.get("source_url", keep.get("source_url"))
            )
            flag_out["merged_listings"] = {
                "states": states,
                "crimes": crimes[:20],
                "source_urls": urls[:20],
                "count": 1 + len(merged_ids),
            }

        if flag_out:
            try:
                new_flags = json.dumps(flag_out, ensure_ascii=False, sort_keys=True)
            except Exception:
                new_flags = str(flag_out)
            cur_flags = str(keep.get("flags") or "").strip()
            if new_flags != cur_flags:
                updates["flags"] = new_flags

        return updates

    @staticmethod
    def _state_match_sql(column_expr: str = "state") -> str:
        """
        SQL fragment: column matches a state code even when multi-state
        merged values use ' | ' separators (e.g. 'FL | TX').
        """
        # Normalize spaces around | then test token membership
        return (
            f"("
            f"UPPER(TRIM(COALESCE({column_expr}, ''))) = UPPER(?) "
            f"OR ('|' || REPLACE(REPLACE(UPPER(COALESCE({column_expr}, '')), ' ', ''), "
            f"'{_MERGE_SEP.strip()}', '|') || '|') "
            f"LIKE '%|' || UPPER(?) || '|%'"
            f")"
        )

    def _append_state_filter(self, query: str, params: List[Any], state: str) -> str:
        """Append OR of state / source_state match (supports merged multi-state)."""
        st = (state or "").strip()
        if not st or st.upper() == "ALL":
            return query
        frag_state = self._state_match_sql("state")
        frag_src = self._state_match_sql("source_state")
        query += f" AND ({frag_state} OR {frag_src})"
        # each fragment uses ? twice
        params.extend([st, st, st, st])
        return query

    def _duplicate_group_sql(self, strategy: str) -> Tuple[str, str]:
        """
        Return (select_sql, key_label) for a strategy.

        select_sql must yield rows with columns: dup_key, cnt, id_list
        (id_list = comma-separated ids).
        """
        s = (strategy or "source_url").strip().lower()
        if s == "source_url":
            sql = """
                SELECT TRIM(source_url) AS dup_key,
                       COUNT(*) AS cnt,
                       GROUP_CONCAT(id) AS id_list
                FROM offenders
                WHERE source_url IS NOT NULL AND TRIM(source_url) != ''
                GROUP BY TRIM(source_url)
                HAVING COUNT(*) > 1
                ORDER BY cnt DESC
            """
            return sql, "source_url"
        if s == "external_id":
            sql = """
                SELECT LOWER(TRIM(external_id)) || '|' ||
                       UPPER(COALESCE(NULLIF(TRIM(state), ''),
                                      NULLIF(TRIM(source_state), ''), '')) AS dup_key,
                       COUNT(*) AS cnt,
                       GROUP_CONCAT(id) AS id_list
                FROM offenders
                WHERE external_id IS NOT NULL AND TRIM(external_id) != ''
                GROUP BY LOWER(TRIM(external_id)),
                         UPPER(COALESCE(NULLIF(TRIM(state), ''),
                                        NULLIF(TRIM(source_state), ''), ''))
                HAVING COUNT(*) > 1
                ORDER BY cnt DESC
            """
            return sql, "external_id+state"
        if s == "name_state_dob":
            sql = """
                SELECT LOWER(TRIM(COALESCE(first_name, ''))) || '|' ||
                       LOWER(TRIM(COALESCE(last_name, ''))) || '|' ||
                       UPPER(COALESCE(NULLIF(TRIM(state), ''),
                                      NULLIF(TRIM(source_state), ''), '')) || '|' ||
                       LOWER(TRIM(date_of_birth)) AS dup_key,
                       COUNT(*) AS cnt,
                       GROUP_CONCAT(id) AS id_list
                FROM offenders
                WHERE last_name IS NOT NULL AND TRIM(last_name) != ''
                  AND date_of_birth IS NOT NULL AND TRIM(date_of_birth) != ''
                GROUP BY LOWER(TRIM(COALESCE(first_name, ''))),
                         LOWER(TRIM(COALESCE(last_name, ''))),
                         UPPER(COALESCE(NULLIF(TRIM(state), ''),
                                        NULLIF(TRIM(source_state), ''), '')),
                         LOWER(TRIM(date_of_birth))
                HAVING COUNT(*) > 1
                ORDER BY cnt DESC
            """
            return sql, "name+state+dob"
        if s == "name_dob":
            # Cross-state: same person registered in multiple states
            sql = """
                SELECT LOWER(TRIM(COALESCE(first_name, ''))) || '|' ||
                       LOWER(TRIM(COALESCE(last_name, ''))) || '|' ||
                       LOWER(TRIM(date_of_birth)) AS dup_key,
                       COUNT(*) AS cnt,
                       GROUP_CONCAT(id) AS id_list
                FROM offenders
                WHERE first_name IS NOT NULL AND TRIM(first_name) != ''
                  AND last_name IS NOT NULL AND TRIM(last_name) != ''
                  AND date_of_birth IS NOT NULL AND TRIM(date_of_birth) != ''
                GROUP BY LOWER(TRIM(COALESCE(first_name, ''))),
                         LOWER(TRIM(COALESCE(last_name, ''))),
                         LOWER(TRIM(date_of_birth))
                HAVING COUNT(*) > 1
                ORDER BY cnt DESC
            """
            return sql, "name+dob (multi-state)"
        if s in ("name_state", "name_state_soft"):
            sql = """
                SELECT LOWER(TRIM(COALESCE(first_name, ''))) || '|' ||
                       LOWER(TRIM(COALESCE(last_name, ''))) || '|' ||
                       UPPER(COALESCE(NULLIF(TRIM(state), ''),
                                      NULLIF(TRIM(source_state), ''), '')) AS dup_key,
                       COUNT(*) AS cnt,
                       GROUP_CONCAT(id) AS id_list
                FROM offenders
                WHERE last_name IS NOT NULL AND TRIM(last_name) != ''
                  AND (
                    (state IS NOT NULL AND TRIM(state) != '')
                    OR (source_state IS NOT NULL AND TRIM(source_state) != '')
                  )
                GROUP BY LOWER(TRIM(COALESCE(first_name, ''))),
                         LOWER(TRIM(COALESCE(last_name, ''))),
                         UPPER(COALESCE(NULLIF(TRIM(state), ''),
                                        NULLIF(TRIM(source_state), ''), ''))
                HAVING COUNT(*) > 1
                ORDER BY cnt DESC
            """
            label = (
                "name+state (photo/address corroborated)"
                if s == "name_state_soft"
                else "name+state"
            )
            return sql, label
        raise ValueError(
            f"Unknown duplicate strategy {strategy!r}; "
            f"choose one of {', '.join(DUPLICATE_STRATEGIES)}"
        )

    def _groups_from_member_map(
        self,
        strategy: str,
        key_label: str,
        buckets: Dict[str, List[Dict[str, Any]]],
        *,
        limit_groups: Optional[int] = None,
        include_unsafe: bool = True,
    ) -> List[Dict[str, Any]]:
        """Build sorted duplicate group dicts from pre-bucketed member rows."""
        groups: List[Dict[str, Any]] = []
        # Largest groups first (same as SQL ORDER BY cnt DESC)
        items = sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        for key, members in items:
            if len(members) < 2 or not key:
                continue
            members = list(members)
            members.sort(
                key=lambda r: (-self._row_richness(r), int(r.get("id") or 0))
            )
            keep = members[0]
            remove_ids = [int(m["id"]) for m in members[1:] if m.get("id") is not None]
            if not remove_ids:
                continue
            keep_name = (
                f"{keep.get('first_name') or ''} {keep.get('last_name') or ''}"
            ).strip() or (keep.get("full_name") or "—")
            safe = True
            if (strategy or "").lower() == "source_url":
                # Use a sample raw URL for portal/CAPTCHA detection
                sample_url = str(
                    keep.get("source_url") or members[0].get("source_url") or key
                )
                safe = not self.is_generic_source_url(
                    sample_url, group_count=len(members)
                )
            if not include_unsafe and not safe:
                continue
            groups.append({
                "strategy": strategy,
                "key_label": key_label,
                "key": key,
                "count": len(members),
                "ids": [int(m["id"]) for m in members if m.get("id") is not None],
                "keep_id": int(keep["id"]),
                "remove_ids": remove_ids,
                "keep_preview": keep_name,
                "richness": self._row_richness(keep),
                "safe": safe,
                "members": members,
            })
            if limit_groups is not None and len(groups) >= int(limit_groups):
                break
        return groups

    def _find_duplicate_groups_normalized_url(
        self,
        strategy: str,
        *,
        limit_groups: Optional[int] = None,
        include_unsafe: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Group by normalized source_url / stable external key in Python.

        Required because NSOPW and some state portals append session ``uid``
        tokens that make raw URL strings unique for the same person.
        """
        s = (strategy or "").strip().lower()
        buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        if s == "source_url":
            rows = self._conn.execute(
                "SELECT * FROM offenders "
                "WHERE source_url IS NOT NULL AND TRIM(source_url) != ''"
            ).fetchall()
            for row in rows:
                rec = dict(row)
                key = self.normalize_identity_url(rec.get("source_url"))
                if key:
                    buckets[key].append(rec)
            return self._groups_from_member_map(
                "source_url",
                "source_url (normalized)",
                buckets,
                limit_groups=limit_groups,
                include_unsafe=include_unsafe,
            )
        if s == "external_id":
            rows = self._conn.execute(
                "SELECT * FROM offenders WHERE "
                "(external_id IS NOT NULL AND TRIM(external_id) != '') "
                "OR (source_url IS NOT NULL AND TRIM(source_url) != '')"
            ).fetchall()
            for row in rows:
                rec = dict(row)
                key = self.stable_external_key(rec)
                if key:
                    buckets[key].append(rec)
            return self._groups_from_member_map(
                "external_id",
                "external_id (stable)",
                buckets,
                limit_groups=limit_groups,
                include_unsafe=include_unsafe,
            )
        raise ValueError(f"Normalized grouping not defined for {strategy!r}")

    def find_duplicate_groups(
        self,
        strategy: str = "source_url",
        *,
        limit_groups: Optional[int] = None,
        include_unsafe: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Find groups of duplicate offender rows for *strategy*.

        Each group: {
          strategy, key, count, ids, keep_id, remove_ids, keep_preview,
          richness, safe (False for shared CAPTCHA/portal URL clusters)
        }

        ``source_url`` / ``external_id`` use normalized identity keys so
        session tokens (e.g. ``uid=``) do not split the same person.
        """
        s = (strategy or "source_url").strip().lower()
        if s in ("source_url", "external_id"):
            return self._find_duplicate_groups_normalized_url(
                s, limit_groups=limit_groups, include_unsafe=include_unsafe
            )

        sql, key_label = self._duplicate_group_sql(strategy)
        rows = self._conn.execute(sql).fetchall()
        groups: List[Dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            id_list = str(d.get("id_list") or "")
            ids = [int(x) for x in id_list.split(",") if x.strip().isdigit()]
            if len(ids) < 2:
                continue
            members = []
            for rid in ids:
                rec = self.get_offender_by_id(rid)
                if rec:
                    members.append(rec)
            if len(members) < 2:
                continue
            # Soft name+state: only merge when photo_url or address corroborates
            if s == "name_state_soft":
                members = self._filter_name_state_soft_members(members)
                if len(members) < 2:
                    continue
            # Prefer richest row; break ties with lowest id (stable survivor)
            members.sort(
                key=lambda r: (-self._row_richness(r), int(r.get("id") or 0))
            )
            keep = members[0]
            remove_ids = [int(m["id"]) for m in members[1:]]
            keep_name = (
                f"{keep.get('first_name') or ''} {keep.get('last_name') or ''}"
            ).strip() or (keep.get("full_name") or "—")
            key = d.get("dup_key") or ""
            safe = True
            if not include_unsafe and not safe:
                continue
            groups.append({
                "strategy": strategy,
                "key_label": key_label,
                "key": key,
                "count": len(members),
                "ids": [int(m["id"]) for m in members],
                "keep_id": int(keep["id"]),
                "remove_ids": remove_ids,
                "keep_preview": keep_name,
                "richness": self._row_richness(keep),
                "safe": safe,
                "members": members,
            })
            if limit_groups is not None and len(groups) >= int(limit_groups):
                break
        return groups

    @classmethod
    def _corroboration_token(cls, record: Dict[str, Any]) -> str:
        """Shared address or photo identity used to soft-confirm name+state dups."""
        photo = cls.normalize_identity_url(record.get("photo_url") or "")
        if photo:
            return f"photo:{photo}"
        addr = " ".join(
            str(record.get("address") or "").strip().lower().split()
        )
        city = " ".join(str(record.get("city") or "").strip().lower().split())
        if addr and len(addr) >= 6:
            return f"addr:{addr}|{city}"
        return ""

    @classmethod
    def _filter_name_state_soft_members(
        cls, members: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Keep the largest subset that shares a photo_url or address token.

        Prevents collapsing different people who only share a common name+state.
        """
        buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for m in members:
            tok = cls._corroboration_token(m)
            if tok:
                buckets[tok].append(m)
        if not buckets:
            return []
        best = max(buckets.values(), key=len)
        return best if len(best) >= 2 else []

    def count_duplicates(
        self,
        strategies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Summary of duplicate groups/rows per strategy.

        Returns {
          total_offenders,
          by_strategy: {name: {groups, extra_rows, safe_groups, safe_extra_rows, unsafe_groups}},
          total_extra_rows, total_safe_extra_rows
        }
        """
        strats = list(strategies) if strategies else list(DEFAULT_DEDUPE_STRATEGIES)
        total = self.get_total_count()
        by: Dict[str, Dict[str, int]] = {}
        total_extra = 0
        total_safe_extra = 0
        for s in strats:
            try:
                groups = self.find_duplicate_groups(s, include_unsafe=True)
            except ValueError:
                continue
            groups_n = len(groups)
            extra = sum(max(0, g["count"] - 1) for g in groups)
            safe_groups = [g for g in groups if g.get("safe", True)]
            unsafe_groups = groups_n - len(safe_groups)
            safe_extra = sum(max(0, g["count"] - 1) for g in safe_groups)
            by[s] = {
                "groups": groups_n,
                "extra_rows": extra,
                "safe_groups": len(safe_groups),
                "safe_extra_rows": safe_extra,
                "unsafe_groups": unsafe_groups,
            }
            total_extra += extra
            total_safe_extra += safe_extra
        return {
            "total_offenders": total,
            "by_strategy": by,
            "total_extra_rows": total_extra,
            "total_safe_extra_rows": total_safe_extra,
        }

    def remove_duplicates(
        self,
        strategy: str = "source_url",
        *,
        dry_run: bool = False,
        merge_fields: bool = True,
        limit_groups: Optional[int] = None,
        safe_only: bool = True,
    ) -> Dict[str, Any]:
        """
        Remove duplicate rows for *strategy*, keeping the richest record per group.

        When *merge_fields* is True, non-empty fields from deleted rows fill blanks
        on the kept row before deletion.

        *safe_only* (default True): skip shared CAPTCHA/portal URL clusters so
        many different offenders are not collapsed into one row.

        Returns {
          strategy, dry_run, groups, kept, deleted, deleted_ids, merged_fields,
          skipped_unsafe
        }
        """
        groups = self.find_duplicate_groups(
            strategy, limit_groups=limit_groups, include_unsafe=True
        )
        deleted_ids: List[int] = []
        kept = 0
        merged_n = 0
        skipped_unsafe = 0
        acted_groups = 0

        for g in groups:
            if safe_only and not g.get("safe", True):
                skipped_unsafe += 1
                continue
            keep_id = int(g["keep_id"])
            remove_ids = list(g["remove_ids"])
            if not remove_ids:
                continue
            keep_row = self.get_offender_by_id(keep_id)
            if not keep_row:
                continue
            kept += 1
            acted_groups += 1

            if merge_fields:
                losers = []
                for rid in remove_ids:
                    loser = self.get_offender_by_id(rid)
                    if loser:
                        losers.append(loser)
                updates = self.merge_duplicate_members(keep_row, losers)
                if updates and not dry_run:
                    self.update_offender(keep_id, updates)
                    # Keep in-memory row current if later strategies re-read it
                    keep_row.update(updates)
                    merged_n += len(updates)
                elif updates and dry_run:
                    merged_n += len(updates)

            if not dry_run and remove_ids:
                placeholders = ",".join("?" for _ in remove_ids)
                self._conn.execute(
                    f"DELETE FROM offenders WHERE id IN ({placeholders})",
                    remove_ids,
                )
            deleted_ids.extend(remove_ids)

        if not dry_run and deleted_ids:
            self._conn.commit()

        return {
            "strategy": strategy,
            "dry_run": dry_run,
            "groups": acted_groups,
            "kept": kept,
            "deleted": len(deleted_ids),
            "deleted_ids": deleted_ids,
            "merged_fields": merged_n,
            "skipped_unsafe": skipped_unsafe,
        }

    def remove_duplicates_all(
        self,
        strategies: Optional[List[str]] = None,
        *,
        dry_run: bool = False,
        merge_fields: bool = True,
        safe_only: bool = True,
    ) -> Dict[str, Any]:
        """
        Run remove_duplicates for each strategy in order (strongest first).

        Default order: source_url → external_id → name_state_dob → name_dob
        (name_dob merges multi-state registrations; name_state is weaker and
        not included unless requested).
        """
        strats = list(strategies) if strategies else list(DEFAULT_DEDUPE_STRATEGIES)
        results = []
        total_deleted = 0
        total_skipped_unsafe = 0
        total_merged_fields = 0
        for s in strats:
            r = self.remove_duplicates(
                s,
                dry_run=dry_run,
                merge_fields=merge_fields,
                safe_only=safe_only,
            )
            results.append(r)
            total_deleted += int(r.get("deleted") or 0)
            total_skipped_unsafe += int(r.get("skipped_unsafe") or 0)
            total_merged_fields += int(r.get("merged_fields") or 0)
        return {
            "dry_run": dry_run,
            "strategies": results,
            "total_deleted": total_deleted,
            "total_skipped_unsafe": total_skipped_unsafe,
            "total_merged_fields": total_merged_fields,
            "total_offenders": self.get_total_count(),
        }

    def find_misclassifications(
        self,
        expected_race: str,
        likely_ethnicities: Optional[List[str]] = None,
        min_confidence: float = 0.5,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Find offenders whose stored likely_ethnicity differs from recorded race.

        Note: most analysis uses SexOffenderSearcher.analyze_ethnicities() which
        classifies names at query time. This method only queries pre-computed columns.
        """
        params: List[Any] = [min_confidence]
        if likely_ethnicities is None:
            query = """
                SELECT * FROM offenders
                WHERE likely_ethnicity IS NOT NULL
                    AND name_confidence >= ?
                    AND (race IS NULL OR UPPER(likely_ethnicity) != UPPER(race))
                ORDER BY name_confidence DESC
                LIMIT ?
            """
            params.append(limit)
        else:
            placeholders = ",".join(["?"] * len(likely_ethnicities))
            query = f"""
                SELECT * FROM offenders
                WHERE likely_ethnicity IN ({placeholders})
                    AND name_confidence >= ?
                ORDER BY name_confidence DESC
                LIMIT ?
            """
            params = list(likely_ethnicities) + [min_confidence, limit]
        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

