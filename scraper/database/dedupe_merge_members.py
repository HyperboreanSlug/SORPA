from __future__ import annotations

import json

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

class DedupeMergeMembersMixin:
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

        # Drop losers that hard-reject against the keeper (over-broad groups
        # must not poison the survivor with another person's data).
        from scraper.database.identity import score_identity_match

        safe_losers: List[Dict[str, Any]] = []
        for m in losers:
            _sc, _reasons, hard = score_identity_match(keep, m)
            if not hard:
                safe_losers.append(m)
        losers = safe_losers
        if not losers:
            return {}

        updates: Dict[str, Any] = {}
        all_rows = [keep] + list(losers)

        # 0) Merge multi-source provenance (must run before race rewrite)
        try:
            from scraper.database.sources import (
                apply_sources_to_record,
                dumps_sources,
                merge_sources_lists,
            )

            merged_sources = merge_sources_lists(
                *(r.get("sources_json") for r in all_rows)
            )
            if merged_sources:
                temp = dict(keep)
                temp["sources_json"] = dumps_sources(merged_sources)
                apply_sources_to_record(temp)
                if temp.get("sources_json") != keep.get("sources_json"):
                    updates["sources_json"] = temp["sources_json"]
                if temp.get("race") and temp.get("race") != keep.get("race"):
                    updates["race"] = temp["race"]
                if temp.get("flags") and temp.get("flags") != keep.get("flags"):
                    updates["flags"] = temp["flags"]
        except Exception:
            pass

        # 1) Union multi-value / multi-listing fields
        # Race/ethnicity are driven by sources_json consensus (or left as the
        # keeper's value for legacy rows) — never string-union letter codes
        # onto the multi-source display (e.g. "Black [FL·html✓] | B").
        skip_union = {"race", "ethnicity"}
        for col in _MERGE_UNION_FIELDS:
            if col in skip_union:
                continue
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
            if col == "sources_json":
                # Already merged above
                continue
            if col == "race" and "race" in updates:
                # Multi-source race already set
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


