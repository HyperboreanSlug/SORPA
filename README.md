# US Public Sex Offender Registry Data Archiver

**A tool for archiving publicly available U.S. sex offender registry data from official government sources.**

This tool helps create local backups ("snapshots") of data that jurisdictions already publish publicly for community safety purposes (under laws like Megan's Law and the Sex Offender Registration and Notification Act - SORNA).

**It only accesses data that government agencies make available to the public by design.**

This tool provides a convenient way to download direct bulk data where available and a GUI for browsing, filtering, and exporting the data locally.

For the most current information, use the official public registry websites and the National Sex Offender Public Website (nsopw.gov).

Users are responsible for complying with all applicable laws, terms of service, and appropriate use of public records data.

## What This Tool Does

- Maintains a list of official public registry websites for US states and territories.
- Automatically downloads direct bulk data files (CSV) from jurisdictions that publish them as public files.
- Provides a GUI for browsing, searching, sorting, custom filtering, and exporting data.
- Creates timestamped local snapshots.
- Extensible via `sources.json`.

The tool focuses on direct public bulk downloads where available. For states without bulk files, it lists the official search sites.

See `sources.json` for current supported direct downloads.

## GUI Features
- Download direct bulk data.
- Browse loaded CSVs in a sortable table (click column headers).
- Custom keyword search and filtering.
- Export current view or filtered results to CSV.
- Snapshot support via CLI.

Run `python gui.py` for the interface.

## Installation

Requires Python 3.8+.

```bash
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

## Usage

CLI:

```bash
python archiver.py list
python archiver.py download --all-direct
python archiver.py --help
```

GUI:

```bash
python gui.py
```

Data is saved under an `archives/` directory with timestamped folders.

The GUI provides browsing, sorting (click headers), custom keyword filtering, and export options.

## Configuration

Edit `sources.json` to add/update/remove jurisdictions or direct download URLs.

Example entry:
```json
{
  "jurisdiction": "Arizona",
  "abbr": "AZ",
  "registry_url": "https://www.azdps.gov/services/public-services-center/sex-offender-compliance",
  "direct_downloads": [
    "https://icrimewatch.net/az_offenders.csv"
  ],
  "notes": "Published CSV for certain risk levels. Verify link and ToS before use."
}
```

For states without `direct_downloads`, only the registry search page is noted.

## Adding Support for More Sources

1. Find the official state registry page (use the list).
2. Look for "Download", "Export", "Data", "CSV", "Bulk", "Open Data" links or portals (data.gov, state open data sites).
3. Add the direct file URL(s) to `sources.json`.
4. Optionally contribute a small parser if the format needs normalization.

**Do not add scrapers that ignore robots.txt or require login/CAPTCHA bypass.**

## Technical Notes

- User-Agent: "Public-SOR-Archiver/1.0 (legitimate archival of public safety records; respectful low-rate access)"
- Default polite delay between requests.
- No parallel scraping by default (can add `--concurrency 1` max recommended).
- Handles basic CSV saving. Photos/images are usually per-offender and heavy to bulk; not downloaded by default.
- Data schemas differ wildly across jurisdictions. Normalization is left to the user (or future enhancement).

## Maintenance

Registry sites change. Re-run `list` periodically and update `sources.json`. Official direct links are the most reliable and lowest-risk.

For the absolute latest national view, prefer https://www.nsopw.gov/.

## Credits & Sources

- State registry URLs primarily from public compilations (e.g., news roundups and official links).
- Direct data examples discovered via official state sites and open data portals.
- Primary reference: Dru Sjodin National Sex Offender Public Website (NSOPW) administered by U.S. DOJ SMART Office.

This tool exists to make it easier to responsibly preserve and work with data that governments already choose to publish publicly for the protection of communities, including children.

Use it ethically and legally.

## GUI

A graphical interface is provided in `gui.py`.

```bash
python gui.py
```

Features:
- List of public sources with direct bulk download support highlighted
- Download direct bulk data
- Browse/search/sort loaded CSV data in table
- Custom keyword filtering
- Export results to CSV

### Building the Standalone .exe (Windows)

```bash
pip install pyinstaller
python build_exe.py
```

Output is in `dist/SOR-Public-Archiver/`.

**Critical for running the exe:**
- Always run `SOR-Public-Archiver.exe` from *inside* the `dist/SOR-Public-Archiver` folder.
- The `_internal` subfolder (containing python311.dll etc.) must be present.
- On target machines, install the Microsoft Visual C++ Redistributable 2015-2022 (x64) if you get DLL errors: https://aka.ms/vs/17/release/vc_redist.x64.exe

Do not move or run only the .exe file by itself.

2. Run the build script:
   ```powershell
   python build_exe.py
   ```

3. After it finishes, look in the `dist\SOR-Public-Archiver` folder.
   - The main file is `SOR-Public-Archiver.exe`
   - Copy the whole folder to any Windows machine and run the .exe

**Notes on the executable:**
- First launch may be slower (PyInstaller extracts files).
- The resulting program is larger (~30-50 MB) because it bundles Python + dependencies.
- To make a single `.exe` file instead of a folder, edit `build_exe.py` and add `--onefile` to the PyInstaller command, then re-run.

This is the easiest way to get a "standalone executable".

## Technical Notes

- User-Agent: "Public-SOR-Archiver/1.0 (legitimate archival of public safety records; respectful low-rate access)"
- Default polite delay between requests.
- No parallel scraping by default.
- Handles basic CSV saving. Photos/images are usually per-offender and heavy to bulk; not downloaded by default.
- Data schemas differ wildly across jurisdictions. Normalization is left to the user.

## Maintenance

Registry sites change. Re-run the list periodically and update `sources.json`. Official direct links are the most reliable and lowest-risk.

For the absolute latest national view, prefer https://www.nsopw.gov/.

## Disclaimer

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND. THE AUTHORS AND CONTRIBUTORS ARE NOT LIABLE FOR ANY DAMAGES, LEGAL CONSEQUENCES, OR MISUSE ARISING FROM USE OF THIS TOOL OR THE DATA IT RETRIEVES.

Always consult legal counsel for your specific situation if you plan to redistribute, build services on, or take action based on this data.
