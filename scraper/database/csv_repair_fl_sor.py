"""Repair incomplete FL SOR bulk rows from fl_sor.csv."""
from __future__ import annotations

import csv
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from scraper.database.constants import _MERGE_SEP, _OFFENDER_INSERT_COLUMNS


def _cf(s: Any) -> str:
    return str(s or "").strip().casefold()


def _tok_ext(ext: Any) -> List[str]:
    if not ext:
        return []
    return [p.strip() for p in re.split(r"\s*\|\s*", str(ext)) if p.strip()]


def _union_field(cur: Any, new: Any) -> Any:
    """Append *new* to multi-value field if not already present."""
    n = str(new or "").strip()
    if not n:
        return cur
    c = str(cur or "").strip()
    if not c:
        return n
    parts = [p.strip() for p in re.split(r"\s*\|\s*", c) if p.strip()]
    if any(p.casefold() == n.casefold() for p in parts):
        return c
    return f"{c}{_MERGE_SEP}{n}"


class RepairFlSorCsvMixin:
    def repair_fl_sor_from_csv(
        self,
        csv_path: str,
        *,
        log: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, int]:
        """
        Re-apply FDLE fl_sor.csv so every person has PERSON_NBR, FL source_state,
        flyer URL, photo_url, and a proper csv_bulk source tag.

        Matches existing stubs (name+race+height or external_id) then inserts
        anyone still missing. Safe to re-run.
        """
        from scraper.database.sources import (
            apply_sources_to_record,
            dumps_sources,
            fl_person_url,
            make_source,
            merge_sources_lists,
            parse_sources,
        )

        def _log(msg: str) -> None:
            if log:
                log(msg)
            else:
                print(msg, flush=True)

        path = Path(csv_path)
        if not path.is_file():
            raise FileNotFoundError(f"FL SOR CSV not found: {csv_path}")

        t0 = time.time()
        _log(f"FL SOR repair: reading {path}…")
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            raw_rows = list(csv.DictReader(f))
        _log(f"  CSV rows: {len(raw_rows):,}")

        # --- indexes of existing offenders ---
        _log("  Indexing existing offenders…")
        by_ext: Dict[str, int] = {}
        by_phys: Dict[str, List[int]] = {}
        all_rows = self._conn.execute(
            "SELECT id, first_name, last_name, race, height, external_id "
            "FROM offenders"
        ).fetchall()
        for row in all_rows:
            rid = int(row[0])
            for tok in _tok_ext(row[5]):
                # Prefer first seen; later updates still work via id
                by_ext.setdefault(tok, rid)
            ln, fn = _cf(row[2]), _cf(row[1])
            race = str(row[3] or "").strip().upper()
            # Primary letter only for multi-source race displays
            race_l = race.split("|")[0].strip()
            race_l = re.sub(r"\[.*?\]", "", race_l).strip().upper()
            if race_l in ("WHITE",):
                race_l = "W"
            elif race_l in ("BLACK",):
                race_l = "B"
            h = str(row[4] or "").strip()
            if ln and fn and race_l and h:
                key = f"{ln}|{fn.split()[0]}|{race_l}|{h}"
                by_phys.setdefault(key, []).append(rid)
        _log(f"  Indexed {len(all_rows):,} rows · {len(by_ext):,} ext ids")

        stats = {
            "csv_rows": len(raw_rows),
            "updated": 0,
            "inserted": 0,
            "skipped_no_id": 0,
            "ambiguous": 0,
            "errors": 0,
        }
        pending = 0
        to_insert: List[Dict[str, Any]] = []

        for i, raw in enumerate(raw_rows, 1):
            try:
                rec = dict(raw)
                self._normalize_record(rec)
                ext = str(rec.get("external_id") or "").strip()
                if not ext:
                    stats["skipped_no_id"] += 1
                    continue

                # Registry jurisdiction is always FL for this file
                residential = str(rec.get("state") or "").strip().upper()
                rec["source_state"] = "FL"
                if not residential:
                    rec["state"] = "FL"
                # else keep PERM_STATE as residential state

                url = fl_person_url(ext)
                if url and not rec.get("source_url"):
                    rec["source_url"] = url

                self._tag_record_source(
                    rec,
                    source_hint="fl_sor",
                    jurisdiction="FL",
                    source_type="csv_bulk",
                )

                # Resolve existing row
                hit: Optional[int] = by_ext.get(ext)
                if hit is None:
                    ln, fn = _cf(rec.get("last_name")), _cf(rec.get("first_name"))
                    race_l = str(rec.get("race") or "").strip().upper()
                    h = str(rec.get("height") or "").strip()
                    if ln and fn and race_l and h:
                        cands = by_phys.get(f"{ln}|{fn.split()[0]}|{race_l}|{h}") or []
                        if len(cands) == 1:
                            hit = cands[0]
                        elif len(cands) > 1:
                            # Prefer stub with empty external_id
                            stub_ids = []
                            for cid in cands:
                                row = self._conn.execute(
                                    "SELECT external_id FROM offenders WHERE id=?",
                                    (cid,),
                                ).fetchone()
                                if row and not (row[0] and str(row[0]).strip()):
                                    stub_ids.append(cid)
                            if len(stub_ids) == 1:
                                hit = stub_ids[0]
                            else:
                                stats["ambiguous"] += 1
                                hit = None

                if hit is not None:
                    ok = self._repair_fl_apply_to_row(hit, rec)
                    if ok:
                        stats["updated"] += 1
                        by_ext[ext] = hit
                        pending += 1
                    else:
                        stats["errors"] += 1
                else:
                    to_insert.append(rec)
                    by_ext[ext] = -1  # reserve; real id after insert

                if pending >= 400:
                    self._conn.commit()
                    pending = 0

                if i % 10000 == 0 or i == len(raw_rows):
                    _log(
                        f"  progress {i:,}/{len(raw_rows):,} "
                        f"upd={stats['updated']:,} ins_q={len(to_insert):,} "
                        f"amb={stats['ambiguous']} ({time.time()-t0:.0f}s)"
                    )
            except Exception as e:
                stats["errors"] += 1
                if stats["errors"] <= 5:
                    _log(f"  row error: {e}")

        if pending:
            self._conn.commit()

        if to_insert:
            _log(f"  Inserting {len(to_insert):,} new FL SOR people…")
            chunk = 500
            for off in range(0, len(to_insert), chunk):
                batch = to_insert[off : off + chunk]
                n = self.insert_offenders_batch(batch)
                # sqlite executemany rowcount is often -1
                stats["inserted"] += (
                    int(n) if n is not None and int(n) >= 0 else len(batch)
                )
                if (off // chunk) % 20 == 0:
                    _log(f"    inserted {stats['inserted']:,}/{len(to_insert):,}")

        # Drop leftover demographics-only stubs (no id/url) once full CSV
        # people exist — prevents ghost rows and double-counts.
        cur = self._conn.execute(
            """
            DELETE FROM offenders
            WHERE sources_json LIKE '%fl_sor%'
              AND (external_id IS NULL OR TRIM(external_id) = '')
              AND (source_url IS NULL OR TRIM(source_url) = '')
            """
        )
        stats["stubs_removed"] = int(cur.rowcount or 0)
        self._conn.commit()
        if stats["stubs_removed"]:
            _log(f"  Removed {stats['stubs_removed']:,} empty FL bulk stubs")

        _log(
            f"FL SOR repair done in {time.time()-t0:.1f}s: "
            f"updated={stats['updated']:,} inserted={stats['inserted']:,} "
            f"stubs_removed={stats.get('stubs_removed', 0):,} "
            f"ambiguous={stats['ambiguous']} skipped_no_id={stats['skipped_no_id']} "
            f"errors={stats['errors']}"
        )
        return stats

    def _repair_fl_apply_to_row(self, row_id: int, incoming: Dict[str, Any]) -> bool:
        """Patch one existing offender with FL CSV fields + source tag."""
        from scraper.database.sources import (
            apply_sources_to_record,
            dumps_sources,
            merge_sources_lists,
        )

        row = self._conn.execute(
            "SELECT * FROM offenders WHERE id = ?", (int(row_id),)
        ).fetchone()
        if not row:
            return False
        existing = dict(row)
        patch: Dict[str, Any] = {}

        ext = str(incoming.get("external_id") or "").strip()
        if ext:
            patch["external_id"] = _union_field(existing.get("external_id"), ext)

        for col in (
            "source_url",
            "photo_url",
            "first_name",
            "middle_name",
            "last_name",
            "full_name",
            "date_of_birth",
            "gender",
            "height",
            "weight",
            "eye_color",
            "hair_color",
            "address",
            "city",
            "county",
            "zip_code",
        ):
            inc = incoming.get(col)
            cur = existing.get(col)
            if inc not in (None, "") and cur in (None, ""):
                patch[col] = inc

        # Race: fill if empty; keep multi-source if already enriched
        if incoming.get("race") and existing.get("race") in (None, ""):
            patch["race"] = incoming["race"]

        # source_state always includes FL
        patch["source_state"] = _union_field(existing.get("source_state"), "FL")
        # Residential state from CSV if we have none
        if incoming.get("state") and existing.get("state") in (None, ""):
            patch["state"] = incoming["state"]
        elif existing.get("state") in (None, "") and not incoming.get("state"):
            patch["state"] = "FL"

        # Merge sources_json (proper FL bulk over inferred stub)
        merged = merge_sources_lists(
            existing.get("sources_json"),
            incoming.get("sources_json"),
        )
        temp = dict(existing)
        temp.update(patch)
        temp["sources_json"] = dumps_sources(merged)
        apply_sources_to_record(temp)
        patch["sources_json"] = temp.get("sources_json")
        if temp.get("race") and temp.get("race") != existing.get("race"):
            patch["race"] = temp["race"]
        if temp.get("flags"):
            patch["flags"] = temp["flags"]

        allowed = set(_OFFENDER_INSERT_COLUMNS) | {"scraped_at"}
        cols = [k for k in patch if k in allowed and k != "id"]
        if not cols:
            return True
        sets = ", ".join(f"{c} = ?" for c in cols)
        vals = [patch[c] for c in cols] + [int(row_id)]
        self._conn.execute(f"UPDATE offenders SET {sets} WHERE id = ?", vals)
        return True
