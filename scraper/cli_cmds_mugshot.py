#!/usr/bin/env python3
"""
CLI tool for mass-downloading and searching US sex offender databases.

Usage:
    # Scrape all states
    python -m scraper.cli scrape --all

    # Scrape specific states
    python -m scraper.cli scrape --states FL,TX,CA,NY

    # Search by name
    python -m scraper.cli search --name "Garcia"

    # Filter by race
    python -m scraper.cli search --race "White"

    # Find misclassifications (Hispanic names marked as White)
    python -m scraper.cli misclassify --ethnicity hispanic

    # Mugshot verify (name + face) / gross scan (face only)
    python -m scraper.cli mugshot-verify --ethnicity indian --limit 200
    python -m scraper.cli mugshot-scan --min-conf 0.85 --limit 500

    # Export results to CSV
    python -m scraper.cli export --output results.csv
"""

import argparse
import csv
from pathlib import Path

def _ensure_mugshot_backend(backend: str) -> None:
    """Pre-warm only when DeepFace is explicitly requested.

    ``auto`` / ``fairface`` are handled by ``MugshotEthnicityScorer``
    (FairFace first, DeepFace only as fallback).
    """
    b = (backend or "auto").strip().lower()
    if b != "deepface":
        return
    from .mugshot_ethnicity import ensure_deepface

    print("  Ensuring DeepFace is installed (legacy backend)…", flush=True)
    ok = ensure_deepface(auto_install=True, warm=True, log=print)
    if not ok:
        print("  WARNING: DeepFace setup incomplete — scoring may fail.", flush=True)


def cmd_mugshot_verify(args: argparse.Namespace) -> None:
    """Verify name-based misclass hits with mugshot ethnicity scores."""
    import csv
    import json
    from .searcher import SexOffenderSearcher
    from .mugshot_ethnicity import (
        BackendUnavailableError,
        MugshotEthnicityScorer,
        get_available_backends,
        verify_misclassifications,
    )

    print(f"\n{'='*60}")
    print("  Mugshot Verify (name + face)")
    print(f"{'='*60}")
    _ensure_mugshot_backend(args.backend)
    print(f"  Backends available: {get_available_backends()}")
    print(f"  Backend: {args.backend}")
    print(f"  Ethnicity filter: {args.ethnicity}")
    print(f"  Face min conf: {args.face_conf}  name min: {args.confidence}")
    print()

    db_path = args.database or "data/offenders.db"
    searcher = SexOffenderSearcher(db_path=db_path)
    try:
        eth = (args.ethnicity or "all").strip().lower()
        if eth == "all":
            mcs = searcher.analyze_ethnicities(
                min_confidence=args.confidence, limit=args.limit
            )
        else:
            mcs = searcher.analyze_ethnicities(
                min_confidence=args.confidence,
                limit=args.limit,
                ethnicity_filter=eth,
            )
        # Prefer rows that already have photos
        with_photo = [
            m for m in mcs
            if (m.record or {}).get("photo_path")
        ]
        print(f"  Name misclass candidates: {len(mcs)} ({len(with_photo)} with photo path)")
        try:
            scorer = MugshotEthnicityScorer(
                backend=args.backend, auto_install=True, log=print
            )
        except BackendUnavailableError as e:
            print(f"  ERROR: {e}")
            return
        print(f"  Using backend: {scorer.backend_name}")

        results = verify_misclassifications(
            with_photo if not args.include_no_photo else mcs,
            scorer=scorer,
            face_min_conf=args.face_conf,
            name_min_conf=args.confidence,
            combined_min_conf=args.combined_conf,
            only_with_photo=not args.include_no_photo,
            progress=lambda d, t: print(f"  … {d}/{t}", flush=True) if d % 50 == 0 else None,
        )
        confirmed = [r for r in results if r.confirms_misclass]
        disagree = [r for r in results if r.verdict == "disagree"]
        print(f"\n  Scored: {len(results)}")
        print(f"  Confirms misclass (name+face): {len(confirmed)}")
        print(f"  Face disagree: {len(disagree)}")
        print(f"\n  {'Name':<30} {'Race':<12} {'Name eth':<14} {'Face':<10} {'Comb':>6} {'Verdict'}")
        print(f"  {'-'*90}")
        for r in results[: args.max_display]:
            name = (
                f"{r.record.get('first_name') or ''} {r.record.get('last_name') or ''}"
            ).strip()
            face_s = (
                f"{r.face.top_label}@{r.face.top_confidence:.2f}"
                if r.face and r.face.ok
                else "—"
            )
            flag = "★" if r.confirms_misclass else " "
            print(
                f" {flag}{name:<29} {(r.recorded_race or '—')[:12]:<12} "
                f"{(r.name_ethnicity or '—')[:14]:<14} {face_s:<10} "
                f"{r.combined_confidence:>6.2f} {r.verdict}"
            )

        if args.export:
            path = Path(args.export)
            if path.suffix.lower() == ".json":
                path.write_text(
                    json.dumps([r.to_dict() for r in results], indent=2),
                    encoding="utf-8",
                )
            else:
                with open(path, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow([
                        "id", "name", "recorded_race", "name_ethnicity",
                        "name_confidence", "face_label", "face_confidence",
                        "verdict", "combined_confidence", "confirms_misclass",
                        "photo_path", "reasons",
                    ])
                    for r in results:
                        w.writerow([
                            r.record.get("id"),
                            f"{r.record.get('first_name') or ''} {r.record.get('last_name') or ''}".strip(),
                            r.recorded_race,
                            r.name_ethnicity,
                            f"{r.name_confidence:.4f}",
                            r.face.top_label if r.face else "",
                            f"{r.face.top_confidence:.4f}" if r.face else "",
                            r.verdict,
                            f"{r.combined_confidence:.4f}",
                            r.confirms_misclass,
                            r.face.photo_path if r.face else r.record.get("photo_path"),
                            "; ".join(r.reasons),
                        ])
            print(f"\n  Exported {len(results)} → {path}")
        print(f"\n{'='*60}\n")
    finally:
        searcher.close()


def cmd_mugshot_scan(args: argparse.Namespace) -> None:
    """Scan mugshots for gross face-vs-race mismatches (no name filter)."""
    import csv
    import json
    from .mugshot_ethnicity import (
        BackendUnavailableError,
        MugshotEthnicityScorer,
        get_available_backends,
        scan_gross_misclassifications,
    )

    print(f"\n{'='*60}")
    print("  Mugshot Gross Misclass Scan")
    print(f"{'='*60}")
    _ensure_mugshot_backend(args.backend)
    print(f"  Backends available: {get_available_backends()}")
    print(f"  Backend: {args.backend}  min face conf: {args.min_conf}")
    print(f"  Recorded races: {args.recorded_race}")
    print(f"  Face labels: {args.face_labels}")
    print()

    try:
        scorer = MugshotEthnicityScorer(
            backend=args.backend, auto_install=True, log=print
        )
    except BackendUnavailableError as e:
        print(f"  ERROR: {e}")
        return
    print(f"  Using backend: {scorer.backend_name}")

    recorded = [x.strip() for x in (args.recorded_race or "WHITE").split(",") if x.strip()]
    faces = [x.strip() for x in (args.face_labels or "black,indian,asian").split(",") if x.strip()]

    hits = scan_gross_misclassifications(
        db_path=args.database or "data/offenders.db",
        scorer=scorer,
        recorded_races=recorded,
        face_labels=faces,
        min_confidence=args.min_conf,
        limit=args.limit,
        state=args.state,
        log=print,
        progress=lambda d, t: print(f"  … scored {d}/{t}", flush=True) if d % 50 == 0 else None,
    )
    print(f"\n  Hits: {len(hits)}")
    print(f"  {'Name':<30} {'Race':<12} {'Face':<10} {'Conf':>6} {'State'}")
    print(f"  {'-'*70}")
    for h in hits[: args.max_display]:
        name = (
            f"{h.record.get('first_name') or ''} {h.record.get('last_name') or ''}"
        ).strip()
        print(
            f"  {name:<30} {(h.recorded_race or '—')[:12]:<12} "
            f"{h.predicted_label:<10} {h.confidence:>6.2f} {h.record.get('state') or '—'}"
        )

    if args.export:
        path = Path(args.export)
        if path.suffix.lower() == ".json":
            path.write_text(
                json.dumps([h.to_dict() for h in hits], indent=2),
                encoding="utf-8",
            )
        else:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow([
                    "id", "name", "state", "recorded_race", "predicted_label",
                    "confidence", "severity", "reason", "photo_path",
                ])
                for h in hits:
                    w.writerow([
                        h.record.get("id"),
                        f"{h.record.get('first_name') or ''} {h.record.get('last_name') or ''}".strip(),
                        h.record.get("state"),
                        h.recorded_race,
                        h.predicted_label,
                        f"{h.confidence:.4f}",
                        h.severity,
                        h.reason,
                        h.face.photo_path,
                    ])
        print(f"\n  Exported {len(hits)} → {path}")
    print(f"\n{'='*60}\n")


def cmd_mugshot_setup(args: argparse.Namespace) -> None:
    """Install FairFace (primary) + DeepFace fallback; warm race weights."""
    from .mugshot_ethnicity import (
        ensure_deepface,
        ensure_fairface,
        get_available_backends,
    )

    print(f"\n{'='*60}")
    print("  Vision auto-setup (FairFace primary, DeepFace fallback)")
    print(f"{'='*60}")
    print(f"  Interpreter: {__import__('sys').executable}")
    warm = not getattr(args, "no_warm", False)
    ff_ok = ensure_fairface(auto_install=True, warm=warm, log=print)
    df_ok = ensure_deepface(
        auto_install=True,
        warm=warm,
        log=print,
        force_reinstall=False,
    )
    ok = bool(ff_ok or df_ok)
    print(f"  FairFace: {'OK' if ff_ok else 'FAILED'}")
    print(f"  DeepFace: {'OK' if df_ok else 'FAILED'}")
    print(f"  Backends: {get_available_backends()}")
    print(f"  Result: {'OK' if ok else 'FAILED'}")
    print(f"{'='*60}\n")
    if not ok:
        raise SystemExit(1)


