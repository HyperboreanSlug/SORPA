#!/usr/bin/env python3
"""Compat shim — use scripts/scrub_db_for_release.py."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).with_name("scrub_db_for_release.py")), run_name="__main__")
