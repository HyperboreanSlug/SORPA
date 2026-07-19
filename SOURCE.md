# SOR Public Archiver 2.0 — Source Document

**Purpose:** Find the right module quickly; prefer ≤200-line units; sleek SOR workbench.  
**Branch:** `sorpa-2.0`  
**Chassis:** mapa-style modular CustomTkinter (lazy tabs, split widgets, export cards)  
**Domain:** NSOPW harvest, jurisdiction reports, cookies/CAPTCHA, multi-source provenance, state scrapers

> **Rule:** Prefer production `.py` modules **≤200 lines**. Residual overs are almost always **one large method** (layout builders, `build()`, HTML export). Split the method body next — do not grow them.  
> **Ignore:** `_legacy_gui_v1/`, `*_monolith_backup.py`, `data/`, `build/`, `dist/`, `__pycache__/`.

---

## How to use

| Need | Open first |
|------|------------|
| GUI entry | `gui.py` → `gui_app/shell.py` |
| Shell lifecycle | `shell.py`, `shell_sync.py`, `shell_header.py`, `shell_ops.py`, `process_lifecycle.py` |
| Browse | `gui_app/tabs/browse/` packages |
| NSOPW UI | `gui_app/tabs/nsopw/` |
| DeepFace UI | `gui_app/tabs/deepface/` |
| Settings | `gui_app/tabs/settings/` |
| Export card | `gui_app/shared/export_card*.py` (premium layout: foil name, large Reported As + wide race banner, crime panel, large release No. footer) |
| Export race banner | `gui_app/shared/export_card_banner.py` — tall red banner; letter-spaced WHITE/race; large “Reported As” |
| Card export # | `gui_app/shared/export_card_release.py` — sequential No. per person (`offenders.export_number` + `data/card_release.json`); **mint only on deliberate Desktop/grid export** (`assign_number=True`); bare render/peek never burns a number; re-export reuses; Reports shows `export #N` |
| Export → confirm | `gui_app/shared/export_card_confirm.py` — exporting a card auto-marks **Confirmed incorrect** (`ethnicity_review` + report verdicts) |
| Deported flag | `gui_app/shared/deported.py` — when address/city/county marks deported, Reports + export banner shows bold **`LISTED WHITE - DEPORTED`** / **`WHITE - DEPORTED`** (list + grid + HTML + share card) |
| Offline listing | `scraper/online_listing.py` — Reports shows **`NOT AVAILABLE ONLINE`** when flags/sources mark dead URL (`blocked:http_404`, error404); Open becomes Offline (archive or message, no dead flyer) |
| Crime summary | `scraper/crime_summary*.py` (clause parse + docket strip) |
| Statute → charge | `scraper/statute_ref.py` + `statute_labels.json` — expand bare `Statute Number(s): 28-320.01` (etc.) to real offense names on Reports/export |
| Detail drawer | `gui_app/shared/detail_drawer/` |
| SQLite | `scraper/database/` · path helpers in `scraper/paths.py` |
| NSOPW pipeline | `scraper/nsopw/` (`builder_*.py`, `client_*.py`) |
| Report HTTP | `scraper/reports/fetcher_*.py` |
| Surname engine | `scraper/searcher_*.py`, `ethnic_names_*.py` (Indian/MENA = South Asian + Arabic lists) |
| Appearance signals | `scraper/searcher_appearance.py` — eye/hair (brown eyes + brown hair, etc.) for misclass conf |
| Display confidence | `scraper/confidence_display.py` — name-only or name+DeepFace combined (marked) |
| Misclassify confirm | `gui_app/shared/record_sidebar*.py`, `verdict_persist.py`, `tabs/browse/misclassify/` |
| CLI | `scraper/cli.py` → `cli_parser.py` + `cli_cmds_*.py` |

---

## Architecture

```
Entry
├── gui.py / Launch SOR Archiver.vbs / run_gui.bat
├── archiver.py → core.py + sources.json
└── python -m scraper → scraper.cli.main

gui_app/     # CustomTkinter (lazy tabs, composed mixins)
scraper/     # Domain packages (composed mixins)
tests/
data/        # offenders.db, report_pages, settings (runtime)
```

**Rule:** `scraper/` never imports `gui_app`.

---

## GUI map

### Shell
| Module | Role |
|--------|------|
| `shell.py` | Window, tabs, init |
| `shell_sync*.py` | GitHub DB sync/publish (header progress; auto-upload at threshold) |
| `db_publish_pending` / `db_publish_run` | Pending listing change counter + publisher upload runner |
| `db_publish_photos` | Stable ~50 MiB path-hash photo shards; skip unchanged parts |
| `shell_header.py` | Path + counts (async COUNT, never blocks UI) |
| `shell_ops.py` | Log, sash, close, tab change |
| `process_lifecycle.py` | Hard shutdown: cancel flags, quit Tk, force-exit leftover threads |
| `auto_update.py` | On open: git fetch origin; ff-only pull when behind; relaunch |
| `async_jobs.py` | `run_bg` + main-thread job queue (all DB work) |
| `shell_warm.py` | Idle-preload lazy tabs so first open is near-instant |
| `lazy_tabs.py` | `warm()` builds without focus steal / on_change |
| `widgets_flow.py` | `FlowRow` — wrap toolbars so top controls stay fully visible |
| `widgets_flow_measure.py` | Chip/leaf size for reflow (ignore CTk 200×200 defaults; HiDPI) |
| `tabs/browse/integrity/refresh.py` | Integrity stats via `run_bg` |
| `tabs/browse/search/run_query.py` | Browse search via `run_bg` |
| `tabs/browse/misclassify/run.py` | Surname analyze / export via `run_bg` |

### Tabs (packages of mixins)
| Package | Pieces |
|---------|--------|
| `tabs/browse/search/` | build, run_query, run_tree, select |
| `tabs/browse/integrity/` | build, refresh, enrich_*, requeue |
| `tabs/browse/misclassify/` | build (Likely ethnicity, Listed as, photo filter), filters, run |
| `reports/race_value.py` | Reject alias/address junk in parsed race fields |
| `tabs/browse/statistics/` | build, update |
| `tabs/browse/reports/` | Independent full-DB Analyze & build (`_report_analyze_results`); Listed/Actual/Show/Photos filters; verdicts; cards; export |
| `shared/export_card_grid.py` | Watermarked 1×2 / 2×2 card collages (mapa seal + @DoDeportations) |
| `tabs/browse/deepface_reports/` | build, data_*, photo_*, actions_*, review_* |
| `tabs/nsopw/` | build (Search/Enrich tabs), enrich_*, options_* (state on Enrich), state_stats, progress_*, tree_*, run_* |
| `tabs/deepface/` | scan_*, setup_* |
| `tabs/settings/` | build, captcha, cookies_*, paths, persist_* |
| `tabs/scrape/` | build, select, run, import_csv, dedupe (under Settings → Scrape) |
| `tabs/settings/shell.py` | Nested General + Scrape tab host |

### Shared
`detail_drawer/`, `export_card*`, `record_sidebar*`, `widgets_*`, `theme`, `lazy_tabs`, `resize_perf`

---

## Domain map

| Package | Composition |
|---------|-------------|
| `database/` | schema, inserts, queries, `db_retry` (SQLite locked/busy multi-retry), `dedupe_*` (name+DOB uses normalize_dob; URL dedupe respects identity hard-reject), `csv_*` (incl. `csv_repair_fl_sor`), sources + `sources_merge` / `sources_race_verify` (HTML consensus = listed race; scrub wrong PERSON_NBR bulk race), deepface_scans + `deepface_scan_hits` (recompute is_hit after race edits) |
| `nsopw/` | `client_*`, `builder_*` (requeue_inc + requeue_work), search_plan, parallel (same-jurisdiction concurrency) |
| `reports/` | `fetcher_*`, `identity_gate` + `identity_audit` (NUCLEAR: name+DOB+photo+link verification; never wrong person), `pdf_fields`, util, photos; `public_links` (FDLE + MA + MI + TX + **CO apps.colorado.gov** — uppercase case-sensitive `id=`, cdps→dps host). CLI: `scripts/verify_identity_full.py`. Crime summary: `crime_summary*.py` (never ship FL `23-CF` dockets). NV multi-col crime + Start Date county repair: `scripts/repair_nv_crime_county.py`. VA vspsor card-header offenses: `fetcher_crime_va.py` + `scripts/repair_va_crime_from_html.py`. MI URL rewrite: `scripts/repair_mi_mspsor_urls.py`. TX URL rewrite: `scripts/repair_tx_dps_urls.py`. SMT-as-crime + Jr suffix repair: `fetcher_crime_smt.py` + `scripts/repair_smt_crime_and_suffix.py` |
| `scrapers/` | State bulk paths: `direct_download`, `arcgis`, `api`, `hybrid`, `html`; **VA** = `va_scraper` + `va_client` + `va_parse*` (vspsor.com); **TX** = `tx_scraper` + `tx_client` (`data/downloads/TX.csv` + `GetRapsheetXml?sid=`; live host `sor.dps.texas.gov`) |
| `searcher_*` | race helpers + core/analyze/export + appearance (eye/hair) |
| `ethnic_names_*` | load, signals, classify, confidence |
| `cli_cmds_*` + `cli_parser` | CLI commands |
| `paths.py` + `app_settings.py` | Install-root path resolution; portable `db_path` across machines |
| `mugshot_ethnicity/` | face ethnicity scan; **primary = FairFace** via `face-race` (`backends_fairface` + `setup_fairface` auto-install/weights); DeepFace/CLIP fallback; `photo_quality` + `photo_resolve` |
| `db_sync*` | GitHub public DB sync (base + deltas + selective photos; overall % progress) |
| `db_sync_progress` | Byte-weighted overall progress for sync/publish UI |
| `db_publish_*` | Publisher-only: gate, row index, delta package, selective upload (this machine) |

---

## Product tabs

Browse · NSOPW · DeepFace · Settings (General · Scrape)

---

## Residual files still >200 LOC

Mostly **single large methods** (not unstructured piles):

- `nsopw/builder_build.py` — `build()` harvest loop  
- Layout UIs: `nsopw/build.py`, `settings/build.py`, `deepface/scan_build.py`, …  
- `reports/export_html.py`, `fetcher_parse.py`, …  
- `mugshot_ethnicity/setup.py`, `scanner.py`, …  
- `cli_parser.py` (`main` argparse tree)

**Next maintenance:** extract method phases into helpers; keep public mixins thin.

---

## Verification

```text
python -c "from gui_app.shell import ArchiverApp; from scraper.database import Database; print('ok')"
python -m unittest tests.test_smoke -q
python -m scraper --help
python gui.py
```

Smoke tests: **25/25 OK** (as of 2.0 modularization checkpoint).

---

## Maintenance

1. New feature → new ≤200-line module; compose via mixin/`__init__.py`.  
2. Never grow residual giants — split first.  
3. Update this file when packages change.  
4. Private constants (`_MERGE_SEP`, etc.) need **explicit** imports (star import skips `_` names).
