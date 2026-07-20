from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from scraper.database.dedupe_keys import *  # noqa: F401,F403
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

class DedupeFindGroupsMixin:
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
                self._filter_url_buckets_by_identity(buckets),
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
                self._filter_ext_buckets_by_identity(buckets),
                limit_groups=limit_groups,
                include_unsafe=include_unsafe,
            )
        raise ValueError(f"Normalized grouping not defined for {strategy!r}")

    def _find_duplicate_groups_normalized_name_dob(
        self,
        strategy: str,
        *,
        limit_groups: Optional[int] = None,
        include_unsafe: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Group by first+last(+state)+DOB using normalize_dob.

        SQL ``LOWER(TRIM(date_of_birth))`` misses the same calendar day written
        as ``06/09/1987`` vs ``1987-06-09`` (common FL CSV vs NSOPW).
        Only full 8-digit DOBs are used so year-only rows do not over-merge.
        """
        from scraper.database.identity import normalize_dob

        s = (strategy or "").strip().lower()
        include_state = s == "name_state_dob"
        if s not in ("name_dob", "name_state_dob"):
            raise ValueError(f"Name/DOB normalized grouping not defined for {strategy!r}")

        rows = self._conn.execute(
            """
            SELECT * FROM offenders
            WHERE first_name IS NOT NULL AND TRIM(first_name) != ''
              AND last_name IS NOT NULL AND TRIM(last_name) != ''
              AND date_of_birth IS NOT NULL AND TRIM(date_of_birth) != ''
            """
        ).fetchall()
        buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            rec = dict(row)
            first = str(rec.get("first_name") or "").strip().casefold()
            last = str(rec.get("last_name") or "").strip().casefold()
            nd = normalize_dob(rec.get("date_of_birth"))
            if not first or not last or len(nd) != 8:
                continue
            if include_state:
                st = (
                    str(rec.get("state") or rec.get("source_state") or "")
                    .strip()
                    .upper()
                )
                if " | " in st:
                    st = st.split(" | ", 1)[0].strip()
                if not st:
                    continue
                key = f"{first}|{last}|{st}|{nd}"
            else:
                key = f"{first}|{last}|{nd}"
            buckets[key].append(rec)

        filtered = self._filter_ext_buckets_by_identity(buckets)

        label = (
            "name+state+dob (normalized)"
            if include_state
            else "name+dob (normalized)"
        )
        return self._groups_from_member_map(
            s,
            label,
            filtered,
            limit_groups=limit_groups,
            include_unsafe=include_unsafe,
        )

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
        ``name_dob`` / ``name_state_dob`` normalize DOB formats (US vs ISO).
        """
        s = (strategy or "source_url").strip().lower()
        if s in ("source_url", "external_id"):
            return self._find_duplicate_groups_normalized_url(
                s, limit_groups=limit_groups, include_unsafe=include_unsafe
            )
        if s in ("name_dob", "name_state_dob"):
            return self._find_duplicate_groups_normalized_name_dob(
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
            # name+state buckets need identity confirmation — common names must
            # not collapse different people.
            if s == "name_state":
                members = self._filter_name_state_strict_members(members)
                if len(members) < 2:
                    continue
            # Soft name+state: only merge when photo_url or address corroborates,
            # then drop any hard identity conflicts (DOB / middle / name).
            if s == "name_state_soft":
                members = self._filter_name_state_soft_members(members)
                members = self._filter_hard_identity_members(members)
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


