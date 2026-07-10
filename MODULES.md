# SOR Public Archiver — Module Map (Master Document)

**Purpose:** Review map so agents and humans load only the code relevant to a task.  
**Status:** Full modular redesign **implemented** (lazy GUI tabs + backend packages).  
**Goal:** Feature-sized files + lazy-loaded tabs so a “Search UI bug” review never loads NSOPW/Scrape/Settings code.

> **How to use this file when reviewing**  
> 1. Find your task under [Review routing](#review-routing-token-efficient).  
> 2. Open only the listed paths (plus this file if needed).  
> 3. Do **not** open `gui_monolith_backup.py` or `*_monolith_backup.py` — those are pre-split archives.  
> 4. Ignore `data/`, `build/`, `dist/`, `__pycache__/`, and diagnostic `scripts/_diag_*` unless the task names them.

---

## 1. Current architecture (post-redesign)

```
Entry points
├── gui.py                 # Thin bootstrap + main() → gui_app.shell.ArchiverApp
├── archiver.py            # Direct bulk downloads via core.py
├── build_exe.py           # PyInstaller (includes gui_app + new packages)
└── python -m scraper      # Full CLI

gui_app/                   # Desktop UI (CustomTkinter), lazy tabs
├── theme.py               # Colors, fonts, treeview style
├── widgets.py             # Cards, trees, charts, scroll helpers
├── lazy_tabs.py           # LazyTabHost — build on first click
├── paths.py               # ROOT
├── shell.py               # ArchiverApp: header, tab host, log, lifecycle
├── shared/
│   └── detail_drawer.py   # Photo + field drawer (Search / others)
└── tabs/
    ├── browse/            # Nested lazy sub-tabs
    │   ├── __init__.py    # Browse shell
    │   ├── search.py
    │   ├── integrity.py
    │   ├── misclassify.py
    │   ├── statistics.py
    │   └── reports.py
    ├── nsopw.py
    ├── scrape.py
    └── settings.py

scraper/
├── database/              # SQLite package (was database.py)
│   ├── constants.py
│   ├── schema.py          # SchemaMixin + connection
│   ├── inserts.py
│   ├── queries.py
│   ├── dedupe.py
│   ├── csv_io.py
│   ├── backup.py
│   └── __init__.py        # class Database(...mixins)
├── nsopw/                 # NSOPW package
│   ├── client.py          # HTTP client
│   ├── search_plan.py     # Compact plan / digraphs / modes
│   ├── builder.py         # Build / requeue / enrich
│   └── __init__.py
├── reports/               # Report fetch package (was report_fetcher.py)
│   ├── util.py            # Labels, URL helpers
│   ├── fetcher.py         # ReportFetcher
│   ├── photos.py          # Review map pointer
│   ├── parse_html.py
│   └── archive_html.py
├── nsopw_client.py        # Shim → scraper.nsopw.client
├── nsopw_builder.py       # Shim → scraper.nsopw.builder
├── report_fetcher.py      # Shim → scraper.reports
├── searcher.py, ethnic_names.py, config.py, cookie_jar.py, app_settings.py, cli.py
└── scrapers/              # Strategy scrapers (unchanged)

core.py, sources.json, tests/, scripts/
```

### Lazy loading (runtime)

| Event | Behavior |
|-------|----------|
| App start | Shell + **Browse → Search** only |
| Click NSOPW / Scrape / Settings | That main tab builds once |
| Browse sub-tabs | Search / Integrity / Misclassify / Statistics / Reports each build once on first open |
| Activity log pane | Still shown only on NSOPW / Scrape (unchanged) |

---

## 2. Dependency graph

```
gui.py → gui_app.shell → tab mixins (lazy)
                │
                ▼
         scraper.*  (database, searcher, nsopw, reports, …)

archiver.py → core.py → sources.json
python -m scraper → cli → same domain packages
```

**Rule:** Domain packages never import `gui_app`. GUI may import domain.

---

## 3. Module catalog

### GUI

| Module | Function |
|--------|----------|
| `gui.py` | Path bootstrap, dep install, `main()` |
| `gui_app/theme.py` | Dark theme palette + fonts + ttk style |
| `gui_app/widgets.py` | Shared widgets/charts/tree helpers |
| `gui_app/lazy_tabs.py` | `LazyTabHost` first-click builders |
| `gui_app/shell.py` | `ArchiverApp` shell, log, sources, close |
| `gui_app/shared/detail_drawer.py` | Detail photo/fields drawer |
| `gui_app/tabs/browse/*` | Browse sub-features |
| `gui_app/tabs/nsopw.py` | NSOPW harvest UI |
| `gui_app/tabs/scrape.py` | State scrape + CSV import UI |
| `gui_app/tabs/settings.py` | DB path, backups, cookies, captcha |

### Domain

| Module | Function |
|--------|----------|
| `scraper/database/*` | Schema, insert, query, dedupe, CSV, backup |
| `scraper/searcher.py` | Search + misclassification analysis |
| `scraper/ethnic_names.py` | Surname → ethnicity |
| `scraper/nsopw/client.py` | NSOPW HTTP search |
| `scraper/nsopw/search_plan.py` | Compact query planning |
| `scraper/nsopw/builder.py` | Build / requeue / enrich orchestration |
| `scraper/reports/fetcher.py` | Jurisdiction report HTML + photos |
| `scraper/reports/util.py` | Parse/normalize helpers for reports |
| `scraper/scrapers/*` | Per-strategy state ingestion |
| `scraper/cli.py` | argparse commands |
| `core.py` / `archiver.py` | Simple direct downloads |

### Compatibility shims

Old import paths still work:

```python
from scraper.database import Database
from scraper.nsopw_client import NSOPWClient
from scraper.nsopw_builder import NSOPWEthnicDatabaseBuilder
from scraper.report_fetcher import ReportFetcher
```

Prefer new paths for new code:

```python
from scraper.nsopw import NSOPWClient, NSOPWEthnicDatabaseBuilder
from scraper.reports import ReportFetcher
```

---

## 4. Review routing (token-efficient)

| Task | Load | Skip |
|------|------|------|
| Theme / tree styling | `gui_app/theme.py`, `gui_app/widgets.py` | All tabs |
| App shell / close / log | `gui_app/shell.py`, `gui_app/lazy_tabs.py` | Tab bodies |
| Browse Search UI | `gui_app/tabs/browse/search.py`, `shared/detail_drawer.py`, `scraper/searcher.py` | NSOPW, Scrape |
| Integrity / dedupe UI | `gui_app/tabs/browse/integrity.py`, `scraper/database/dedupe.py`, `queries.py` | builder |
| Misclassify UI | `gui_app/tabs/browse/misclassify.py`, `searcher.py`, `ethnic_names.py` | reports |
| Statistics | `gui_app/tabs/browse/statistics.py`, `widgets.py` (charts) | — |
| Reports / verdicts | `gui_app/tabs/browse/reports.py` | scrape |
| NSOPW UI | `gui_app/tabs/nsopw.py` | database dedupe |
| NSOPW pipeline | `scraper/nsopw/builder.py`, `search_plan.py`, `client.py` | gui except progress hooks |
| Report HTML/photo | `scraper/reports/fetcher.py`, `util.py`, `cookie_jar.py` | gui |
| Scrape / CSV import | `gui_app/tabs/scrape.py`, `scrapers/*`, `database/csv_io.py` | NSOPW |
| Direct download only | `archiver.py`, `core.py`, `sources.json` | entire gui_app |
| Settings | `gui_app/tabs/settings.py`, `app_settings.py`, `cookie_jar.py`, `database/backup.py` | — |
| CLI command X | `scraper/cli.py` `cmd_X` + domain imports | gui_app |

### Agent prompt prefix

```
Read MODULES.md section "Review routing". Task: <one sentence>.
Load only the files listed for that task. Never open *_monolith_backup.py.
```

---

## 5. Implementation notes

- **GUI pattern:** mixin classes on `ArchiverApp` (shared `self` state) + `LazyTabHost` for build-on-click.
- **Database pattern:** `Database(SchemaMixin, InsertMixin, QueryMixin, DedupeMixin, CsvMixin)`.
- **Lazy scrape tree:** `_load_sources` stores configs; tree fills when Scrape tab builds (`_populate_scrape_tree`).
- **Backups of pre-split code:** `gui_monolith_backup.py`, `scraper/database_monolith_backup.py`, `scraper/nsopw_*_monolith_backup.py`, `scraper/report_fetcher_monolith_backup.py` (delete when no longer needed).
- **Tests:** `python -m unittest discover -s tests` (65/66 at last run; one surname digraph assertion may be data-sensitive).

---

## 6. Quick reference — “what does X do?”

| Name | One-liner |
|------|-----------|
| **Browse** | Local DB: search, integrity, misclass, stats, report review |
| **NSOPW** | Live NSOPW ethnic-name harvest + HTML/photo archive |
| **Scrape** | State bulk/API scrape + CSV import |
| **Settings** | DB path, backups, cookies, captcha queue |
| **Database** | Persistence and queries |
| **Searcher** | Search + surname/race mismatch engine |
| **NSOPW client/builder** | HTTP + multi-query orchestration |
| **Reports** | Jurisdiction page fetch/parse/photo |

---

*Keep this file accurate when module paths change.*
