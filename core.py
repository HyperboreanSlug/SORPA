"""
Core functionality for the Public SOR Data Archiver.

Shared between CLI and GUI.
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

import requests

# Polite, identifiable User-Agent
USER_AGENT = (
    "Public-SOR-Archiver/1.0 "
    "(legitimate archival of publicly published U.S. sex offender safety data; "
    "respectful low-rate access)"
)

DEFAULT_DELAY = 2.0
SOURCES_FILE = "sources.json"


def get_bundle_dir() -> Path:
    """Return the directory where bundled data files live.

    Works both for normal Python runs and PyInstaller onefile / onedir bundles.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller sets sys._MEIPASS to the temp extraction folder
        return Path(sys._MEIPASS)
    else:
        # Normal run: look next to this file (or current dir as fallback)
        return Path(__file__).parent if "__file__" in globals() else Path.cwd()


def load_sources() -> List[Dict[str, Any]]:
    """Load the jurisdictions list from sources.json."""
    base_dir = get_bundle_dir()
    path = base_dir / SOURCES_FILE
    if not path.exists():
        # Fallback: try current working directory (for flexibility)
        path = Path.cwd() / SOURCES_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"{SOURCES_FILE} not found. Make sure it is next to the executable "
            "or in the current folder."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_direct_sources(sources: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Return only entries that have direct_downloads."""
    if sources is None:
        sources = load_sources()
    return [s for s in sources if s.get("direct_downloads")]


def save_metadata(dest_dir: Path, meta: Dict[str, Any]) -> None:
    meta_path = dest_dir / "download_metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def download_file(
    url: str,
    dest_path: Path,
    delay: float = DEFAULT_DELAY,
    progress_callback: Optional[Callable[[int, int], None]] = None
) -> Dict[str, Any]:
    """
    Download a single file politely.

    progress_callback(current_bytes, total_bytes) if provided.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/csv,application/octet-stream,application/json,*/*;q=0.8",
    }

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    start = time.time()
    try:
        resp = requests.get(url, headers=headers, stream=True, timeout=120)
        resp.raise_for_status()

        total_size = int(resp.headers.get("Content-Length", 0))
        downloaded = 0

        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size > 0:
                        progress_callback(downloaded, total_size)

        elapsed = time.time() - start

        meta = {
            "url": url,
            "saved_to": str(dest_path),
            "status_code": resp.status_code,
            "content_type": resp.headers.get("Content-Type", ""),
            "bytes_written": dest_path.stat().st_size if dest_path.exists() else 0,
            "elapsed_seconds": round(elapsed, 2),
            "downloaded_at": datetime.utcnow().isoformat() + "Z",
        }

        time.sleep(delay)
        return meta

    except requests.RequestException as e:
        time.sleep(delay)
        return {
            "url": url,
            "error": str(e),
            "downloaded_at": datetime.utcnow().isoformat() + "Z",
        }


def get_snapshot_dir(base: Path, date_str: Optional[str] = None) -> Path:
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    d = base / date_str
    d.mkdir(parents=True, exist_ok=True)
    return d


def perform_downloads(
    selected_sources: List[Dict[str, Any]],
    output_base: Path,
    delay: float = DEFAULT_DELAY,
    log_callback: Optional[Callable[[str], None]] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> List[Dict[str, Any]]:
    """
    High-level download orchestrator used by both CLI and GUI.

    selected_sources: list of source dicts (must have 'direct_downloads')
    log_callback: called with status strings
    progress_callback: called with (current_index, total, message)
    """
    if not selected_sources:
        if log_callback:
            log_callback("No sources selected.")
        return []

    snapshot_dir = get_snapshot_dir(output_base)
    if log_callback:
        log_callback(f"Saving snapshot to: {snapshot_dir}")

    results = []
    total = sum(len(s.get("direct_downloads", [])) for s in selected_sources)
    current = 0

    for src in selected_sources:
        juris = src["jurisdiction"]
        abbr = src["abbr"]
        urls = src.get("direct_downloads", [])

        for url in urls:
            current += 1
            safe_name = Path(url).name or f"{abbr.lower()}_data.csv"
            if not any(safe_name.lower().endswith(ext) for ext in (".csv", ".txt", ".json", ".zip")):
                safe_name = f"{abbr.lower()}_data.csv"

            dest = snapshot_dir / abbr / safe_name

            msg = f"[{abbr}] Downloading {url}"
            if log_callback:
                log_callback(msg)
            if progress_callback:
                progress_callback(current, total, msg)

            def file_progress(done, total_size):
                if progress_callback:
                    progress_callback(current, total, f"[{abbr}] {done}/{total_size} bytes")

            meta = download_file(url, dest, delay=delay, progress_callback=file_progress)
            meta["jurisdiction"] = juris
            meta["abbr"] = abbr
            results.append(meta)

            if "error" in meta:
                if log_callback:
                    log_callback(f"  ERROR: {meta['error']}")
            else:
                size = meta.get("bytes_written", 0)
                if log_callback:
                    log_callback(f"  Saved {size} bytes to {dest}")

    # Save overall metadata
    save_metadata(snapshot_dir, {
        "snapshot_date": snapshot_dir.name,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "tool": "Public-SOR-Archiver (GUI/CLI)",
        "downloads": results,
    })

    if log_callback:
        log_callback("\nDownload complete. See download_metadata.json for details.")
    return results
