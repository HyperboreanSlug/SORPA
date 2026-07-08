# Public SOR Data Archiver

Tools for archiving and searching **publicly available** U.S. sex offender registry data.

> **Legal / ethical note:** This project only targets data that jurisdictions already publish for public safety. Respect each site’s terms of use, rate limits, and robots rules. Do not mass-scrape NSOPW or other services that prohibit automated access. Scraped/exported files can contain sensitive personal data — keep them private and never commit them to git.

## Features

- **Direct bulk downloads** for jurisdictions that publish files (AZ, DC, GA)
- **Best-effort HTML scrapers** for other states (often return little without site-specific work)
- **SQLite search** by name, race, and state
- **Surname / race mismatch flagging** using configurable ethnic surname lists
- **GUI** (`python gui.py`) and **CLI** (`python -m scraper` / `python archiver.py`)

## Requirements

- Python 3.10+
- Dependencies in `requirements.txt`

```bash
pip install -r requirements.txt
```

## Quick start

### Simple bulk archiver (known direct URLs only)

```bash
python archiver.py list
python archiver.py download --all-direct
python archiver.py download --states AZ,DC,GA
```

Files land under `archives/YYYY-MM-DD/`.

### Full scraper + database CLI

```bash
# Bulk-download states only
python -m scraper scrape --direct-only

# Import CSVs into SQLite
python -m scraper import --input data/downloads

# Search / stats
python -m scraper search
python -m scraper search --name "Garcia"
python -m scraper search --state FL --race WHITE

# Surname/race mismatch analysis
python -m scraper misclassify --ethnicity hispanic --confidence 0.5

# Export
python -m scraper export --output results.csv --state FL
```

### GUI

```bash
python gui.py
```

### Standalone Windows EXE

```bash
pip install pyinstaller
python build_exe.py
```

Copy the entire `dist/SOR-Public-Archiver/` folder (not just the `.exe`).

## Project layout

```
├── archiver.py           # Direct-download CLI
├── core.py               # Shared download helpers
├── gui.py                # Tkinter UI
├── build_exe.py          # PyInstaller helper
├── sources.json          # Registry URLs + bulk links
├── requirements.txt
├── LICENSE
├── scraper/
│   ├── cli.py
│   ├── config.py
│   ├── database.py
│   ├── searcher.py
│   ├── ethnic_names.py
│   ├── ethnic_names.json
│   └── scrapers/         # direct | api | html | hybrid
└── tests/
    └── test_smoke.py
```

## Scrape support (verified)

| Abbr | Method | Status |
|------|--------|--------|
| **GA** | Direct CSV | **Working** (~25k records) |
| **DC** | ArcGIS FeatureServer (+ CSV fallback) | **Working** (~1k records) |
| AZ | Direct CSV (iCrimewatch) | URL published; **HTTP 403** bot block — use browser |
| FL | Hybrid / download page | CAPTCHA + email form — **manual only** |
| All others | Interactive search sites | No public bulk API — not automatable |

```bash
# Show per-state support matrix
python -m scraper status -v

# Scrape verified bulk sources only
python -m scraper scrape --direct-only

# Live smoke check
python scripts/verify_scrapers.py
```

Most SOR websites are JavaScript search apps (disclaimer → CAPTCHA → query). Landing-page HTML does **not** contain the offender database.

## NSOPW ethnic name search (GUI preferred)

**Prefer the GUI:** run `python gui.py` → **NSOPW** tab. All settings (ethnicity,
surname count, first-name mode, jurisdictions, max searches/reports, delays,
HTML archive path, database path) are available there.

### Partial first-name matching

NSOPW accepts **partial first names**. Example: first=`M`, last=`Singh` returns
offenders whose given names expand around that prefix (e.g. Mandhir, Manjit,
Mitchell). Default mode uses **A–Z initials** so each surname needs at most 26
queries instead of dozens of full first names.

### CLI (optional)

```bash
# Default: A–Z initials + Hispanic surnames
python -m scraper nsopw --ethnicity hispanic --surnames 8 --max-searches 40

# Full first-name list mode
python -m scraper nsopw --first-mode full --ethnicity asian --max-searches 30

# Custom prefixes only
python -m scraper nsopw --first-names "M,J,R,S" --ethnicity hispanic
```

### What is stored

| Field | Content |
|--------|---------|
| `source_url` | Click-through jurisdiction report link from NSOPW |
| `report_html_path` | Local HTML snapshot of that report (for offline validation) |
| demographics | Best-effort race/ethnicity/height/etc. from the report page |
| `likely_ethnicity` | From the ethnic surname list used in the search |

Rate limits: search/report delays default to **2.0s** (floor **1.5s**). Double-click
a result row in the GUI to open the saved HTML or live report URL.

Respect [NSOPW Conditions of Use](https://www.nsopw.gov/).

## Tests

```bash
python -m unittest discover -s tests -v
```

## Configuration

- Registry list: `sources.json` and `scraper/config.py`
- Surname lists: `scraper/ethnic_names.json` (loaded at runtime)

## License

MIT — see [LICENSE](LICENSE).
