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

class DedupeFindFilterMixin:
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

    def _filter_name_state_strict_members(
        self,
        members: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Keep richest row + members that pass the full merge gate against it.

        name_state buckets by name+state only; require real multi-identifier
        agreement (DOB / middle / registry id) so common names never collapse
        different people.
        """
        from scraper.database.identity import should_merge_records

        if len(members) < 2:
            return list(members)
        members = sorted(
            members,
            key=lambda r: (-self._row_richness(r), int(r.get("id") or 0)),
        )
        keep = members[0]
        kept = [keep]
        for m in members[1:]:
            ok, _sc, _reasons = should_merge_records(keep, m)
            if ok:
                kept.append(m)
        return kept

    def _filter_hard_identity_members(
        self,
        members: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Keep richest row + members that do not hard-reject against it."""
        from scraper.database.identity import score_identity_match

        if len(members) < 2:
            return list(members)
        members = sorted(
            members,
            key=lambda r: (-self._row_richness(r), int(r.get("id") or 0)),
        )
        keep = members[0]
        kept = [keep]
        for m in members[1:]:
            _sc, _reasons, hard = score_identity_match(keep, m)
            if hard:
                continue
            kept.append(m)
        return kept

    def _filter_url_buckets_by_identity(
        self,
        raw_buckets: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Real flyer URLs: drop hard identity conflicts (wrong PERSON_NBR join).
        Generic portal/CAPTCHA fan-out: keep full groups as unsafe clusters.
        """
        out: Dict[str, List[Dict[str, Any]]] = {}
        for key, members in raw_buckets.items():
            if len(members) < 2:
                continue
            sample_url = str(members[0].get("source_url") or key or "")
            if self.is_generic_source_url(sample_url, group_count=len(members)):
                out[key] = list(members)
                continue
            kept = self._filter_hard_identity_members(members)
            if len(kept) >= 2:
                out[key] = kept
        return out

    def _filter_ext_buckets_by_identity(
        self,
        raw_buckets: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Always identity-filter external_id groups."""
        out: Dict[str, List[Dict[str, Any]]] = {}
        for key, members in raw_buckets.items():
            if len(members) < 2:
                continue
            kept = self._filter_hard_identity_members(members)
            if len(kept) >= 2:
                out[key] = kept
        return out


