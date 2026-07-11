#!/usr/bin/env python3
"""Verify Hispanic misclassification rate and race breakdown on offenders.db."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from scraper.searcher import (
    SexOffenderSearcher,
    _canonical_race_key,
    _ethnicity_family,
    _first_name_from_record,
    _is_compatible,
    _last_name_from_record,
    _middle_name_from_record,
)

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "offenders.db"


def main() -> None:
    s = SexOffenderSearcher(db_path=str(DB))
    total = s.get_total_count()
    print(f"DB total: {total:,}")
    print()

    print("--- analyze_ethnicities (app formula) ---")
    for min_conf in (0.5, 0.6, 0.7):
        for lim in (0, 5000, 20000):
            mis, base = s.analyze_ethnicities(
                min_confidence=min_conf,
                limit=lim,
                ethnicity_filter="hispanic",
                return_base_count=True,
            )
            rate = (len(mis) / base * 100.0) if base else 0.0
            lim_s = "ALL" if not lim else str(lim)
            print(
                f"  min_conf={min_conf} limit={lim_s:>5}: "
                f"base={base:,}  mis={len(mis):,}  rate={rate:.1f}%"
            )

    print()
    print("=== FULL SCAN detail (min_conf=0.5) ===")
    race_all: Counter = Counter()
    race_mis: Counter = Counter()
    race_ok: Counter = Counter()
    examples_white: list = []
    examples_hisp: list = []
    n_match = 0

    for rec in s.db.iter_offenders(limit=None, newest_first=False):
        ln = _last_name_from_record(rec)
        if not ln:
            continue
        eth, conf, names = s.ethnic_db.classify_by_name(
            ln,
            first_name=_first_name_from_record(rec) or None,
            middle_name=_middle_name_from_record(rec) or None,
        )
        if conf < 0.5 or eth == "Unknown":
            continue
        if _ethnicity_family(eth) != "hispanic":
            continue
        n_match += 1
        race_raw = (rec.get("race") or "").strip() or "(empty)"
        key = _canonical_race_key(race_raw)
        race_all[key] += 1
        rec_eth = (rec.get("ethnicity") or "").strip() or None
        if _is_compatible(eth, race_raw, recorded_ethnicity=rec_eth):
            race_ok[key] += 1
            if key == "HISPANIC" and len(examples_hisp) < 5:
                examples_hisp.append(
                    (
                        rec.get("first_name"),
                        rec.get("last_name"),
                        race_raw,
                        conf,
                        names[:3],
                    )
                )
        else:
            race_mis[key] += 1
            if key == "WHITE" and len(examples_white) < 8:
                examples_white.append(
                    (
                        rec.get("first_name"),
                        rec.get("last_name"),
                        race_raw,
                        conf,
                        names[:3],
                        rec.get("state") or rec.get("source_state"),
                    )
                )

    print(f"Hispanic name matches (base): {n_match:,}")
    print(
        f"Incompatible (current rules): {sum(race_mis.values()):,}  "
        f"({sum(race_mis.values()) / n_match * 100:.2f}%)"
        if n_match
        else "n/a"
    )
    print(
        f"Compatible (current rules):   {sum(race_ok.values()):,}  "
        f"({sum(race_ok.values()) / n_match * 100:.2f}%)"
        if n_match
        else "n/a"
    )
    print()
    print("Recorded race among Hispanic-name matches:")
    for k, c in race_all.most_common(30):
        print(f"  {k:40s} {c:6,}  {c / n_match * 100:5.1f}%")

    print()
    print("Counted as MISCLASS (incompatible):")
    for k, c in race_mis.most_common(20):
        print(f"  {k:40s} {c:6,}  {c / max(1, sum(race_mis.values())) * 100:5.1f}% of mis")

    print()
    print("Counted as OK (compatible):")
    for k, c in race_ok.most_common(20):
        print(f"  {k:40s} {c:6,}")

    # Counterfactual: treat WHITE as compatible for Hispanic (common registry practice)
    white_ok_keys = {
        "HISPANIC",
        "LATINO",
        "LATINA",
        "LATINX",
        "H",
        "WHITE HISPANIC",
        "WHITE",
    }
    mis_if_white = sum(c for k, c in race_all.items() if k not in white_ok_keys and "HISPANIC" not in k)
    print()
    print("=== Counterfactual rules ===")
    print(
        f"If WHITE also compatible: mis={mis_if_white:,} / {n_match:,} = "
        f"{mis_if_white / n_match * 100:.1f}%"
        if n_match
        else "n/a"
    )
    w = race_all.get("WHITE", 0)
    h = race_all.get("HISPANIC", 0) + race_all.get("WHITE HISPANIC", 0)
    print(f"  WHITE share of Hispanic-name matches: {w:,} ({w / n_match * 100:.1f}%)")
    print(f"  HISPANIC / WHITE HISPANIC share:      {h:,} ({h / n_match * 100:.1f}%)")

    print()
    print("Sample WHITE-recorded Hispanic surnames (currently 'misclass'):")
    for row in examples_white:
        print(f"  {row}")
    print("Sample HISPANIC-recorded (compatible):")
    for row in examples_hisp:
        print(f"  {row}")

    # Check ethnicity field if present
    eth_field = Counter()
    for rec in s.db.iter_offenders(limit=None, newest_first=False):
        ln = _last_name_from_record(rec)
        if not ln:
            continue
        eth, conf, _ = s.ethnic_db.classify_by_name(
            ln,
            first_name=_first_name_from_record(rec) or None,
            middle_name=_middle_name_from_record(rec) or None,
        )
        if conf < 0.5 or _ethnicity_family(eth) != "hispanic":
            continue
        ef = (rec.get("ethnicity") or "").strip() or "(empty)"
        eth_field[ef.upper() if ef != "(empty)" else ef] += 1
    print()
    print("ethnicity field among Hispanic-name matches (top 15):")
    for k, c in eth_field.most_common(15):
        print(f"  {k:40s} {c:6,}  {c / n_match * 100:5.1f}%")

    s.close()


if __name__ == "__main__":
    main()
