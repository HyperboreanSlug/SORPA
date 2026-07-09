#!/usr/bin/env python3
"""
Build a standalone Windows package for the SOR Archiver GUI.

Usage:
    python build_exe.py

Produces:
    dist/SOR-Public-Archiver/          # onedir folder (run the .exe from here)
    dist/SOR-Public-Archiver-Windows.zip

Requires: pip install pyinstaller
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def main() -> None:
    project_dir = Path(__file__).parent.resolve()

    print("=== Building standalone executable for Public SOR Data Archiver GUI ===")
    print(f"Project dir: {project_dir}")

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("\nPyInstaller not found. Installing now...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # Ensure runtime deps used by the frozen app
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-q",
            "-r",
            str(project_dir / "requirements.txt"),
        ]
    )

    ethnic_json = project_dir / "scraper" / "ethnic_names.json"
    sources = project_dir / "sources.json"
    readme = project_dir / "README.md"
    license_f = project_dir / "LICENSE"

    # Collect data files (PyInstaller Windows: path;dest)
    add_data = [
        f"{ethnic_json};scraper",
        f"{readme};.",
    ]
    if sources.is_file():
        add_data.append(f"{sources};.")
    if license_f.is_file():
        add_data.append(f"{license_f};.")

    hidden = [
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "customtkinter",
        "bs4",
        "requests",
        "curl_cffi",
        "curl_cffi.requests",
        "scraper",
        "scraper.config",
        "scraper.database",
        "scraper.app_settings",
        "scraper.searcher",
        "scraper.ethnic_names",
        "scraper.nsopw_client",
        "scraper.nsopw_builder",
        "scraper.report_fetcher",
        "scraper.cli",
        "scraper.scrapers",
        "scraper.scrapers.base",
        "scraper.scrapers.direct_download",
        "scraper.scrapers.api_scraper",
        "scraper.scrapers.html_scraper",
        "scraper.scrapers.hybrid_scraper",
        "scraper.scrapers.arcgis_scraper",
        "scraper.scrapers.normalize",
        "csv",
        "pathlib",
        "queue",
        "json",
        "html",
        "hashlib",
        "webbrowser",
        "threading",
        "sqlite3",
    ]

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        "SOR-Public-Archiver",
    ]
    for mod in hidden:
        cmd.append(f"--hidden-import={mod}")
    for item in add_data:
        cmd.extend(["--add-data", item])
    # Collect customtkinter package data (themes, assets)
    cmd.extend(["--collect-all", "customtkinter"])
    # curl_cffi may ship binary extensions
    cmd.extend(["--collect-all", "curl_cffi"])
    cmd.append(str(project_dir / "gui.py"))

    print("\nRunning PyInstaller:")
    print(" ".join(str(c) for c in cmd))
    print()

    try:
        subprocess.check_call(cmd, cwd=project_dir)
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    dist_dir = project_dir / "dist" / "SOR-Public-Archiver"
    exe_path = dist_dir / "SOR-Public-Archiver.exe"
    if not exe_path.is_file():
        print(f"ERROR: expected exe missing: {exe_path}")
        sys.exit(1)

    # Ship a short readme next to the exe
    runme = dist_dir / "HOW_TO_RUN.txt"
    runme.write_text(
        "\n".join(
            [
                "SOR Public Archiver — Windows package",
                "",
                "1. Extract the entire folder (keep SOR-Public-Archiver.exe next to _internal).",
                "2. Double-click SOR-Public-Archiver.exe",
                "3. Do NOT move only the .exe — the _internal folder is required.",
                "4. If startup fails, install Microsoft Visual C++ Redistributable 2015–2022 (x64).",
                "5. Registry data is written under a local data/ folder next to the exe when possible.",
                "",
                "Source: https://github.com/HyperboreanSlug/sor-public-archiver",
                "",
            ]
        ),
        encoding="utf-8",
    )

    zip_path = project_dir / "dist" / "SOR-Public-Archiver-Windows.zip"
    if zip_path.exists():
        zip_path.unlink()
    print(f"\nCreating {zip_path.name} …")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in dist_dir.rglob("*"):
            if path.is_file():
                arc = Path("SOR-Public-Archiver") / path.relative_to(dist_dir)
                zf.write(path, arcname=str(arc))

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print("\n" + "=" * 60)
    print("BUILD COMPLETE")
    print("=" * 60)
    print(f"Folder:  {dist_dir}")
    print(f"Exe:     {exe_path}")
    print(f"Zip:     {zip_path}  ({size_mb:.1f} MB)")
    print()
    print("Copy the entire SOR-Public-Archiver folder (or the zip) to any Windows PC.")
    print("Python is not required on the target machine.")


if __name__ == "__main__":
    main()
