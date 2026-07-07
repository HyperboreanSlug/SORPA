#!/usr/bin/env python3
"""
Build a standalone Windows executable for the SOR Archiver GUI.

Usage:
    python build_exe.py

Requirements:
    pip install pyinstaller

This will produce a folder (and optionally a single .exe) in the 'dist' directory.
The resulting executable is self-contained.
"""

import subprocess
import sys
from pathlib import Path

def main():
    project_dir = Path(__file__).parent.resolve()

    print("=== Building standalone executable for Public SOR Data Archiver GUI ===")
    print(f"Project dir: {project_dir}")

    # Make sure PyInstaller is available
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("\nPyInstaller not found. Installing now...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # Build command - use onedir (folder) mode which is more reliable for tkinter apps
    # and avoids some python DLL extraction issues seen in onefile mode.
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",                      # folder mode (more reliable)
        "--windowed",                    # no console window (GUI only)
        "--name", "SOR-Public-Archiver",
        "--hidden-import=tkinter",
        "--hidden-import=tkinter.ttk",
        "--hidden-import=tkinter.scrolledtext",
        "--hidden-import=tkinter.filedialog",
        "--hidden-import=tkinter.messagebox",
        "--hidden-import=csv",
        "--hidden-import=pathlib",
        "--add-data", f"{project_dir / 'sources.json'};.",   # include sources
        "--add-data", f"{project_dir / 'README.md'};.",
        str(project_dir / "gui.py"),
    ]

    print("\nRunning PyInstaller with command:")
    print(" ".join(str(c) for c in cmd))
    print()

    try:
        subprocess.check_call(cmd, cwd=project_dir)
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    dist_dir = project_dir / "dist" / "SOR-Public-Archiver"
    exe_path = dist_dir / "SOR-Public-Archiver.exe"

    print("\n" + "=" * 60)
    print("BUILD COMPLETE")
    print("=" * 60)
    print(f"Executable folder: {dist_dir}")
    print(f"Main executable:   {exe_path}")
    print()
    print("IMPORTANT: To run the app, you MUST use the .exe from INSIDE the 'SOR-Public-Archiver' folder.")
    print("Do NOT copy or run just the .exe file alone - it needs the _internal folder with python311.dll etc.")
    print("Copy the ENTIRE 'SOR-Public-Archiver' folder to any Windows PC.")
    print("It does not require Python to be installed on the target machine.")
    print()
    print("If you see 'failed to load python DLL', ensure you are running from the full folder,")
    print("and that Microsoft Visual C++ Redistributable (2015-2022) is installed on the machine.")


if __name__ == "__main__":
    main()
