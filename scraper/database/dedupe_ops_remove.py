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

class DedupeOpsRemoveMixin:
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
            try:
                from scraper.db_publish_pending import add_pending_listings

                add_pending_listings(len(deleted_ids))
            except Exception:
                pass

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


