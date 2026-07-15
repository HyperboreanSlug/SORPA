"""Run a public DB publish from the app (publisher machine only)."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from scraper.db_publish_gate import is_publish_allowed
from scraper.db_publish_pending import clear_pending_listings
from scraper.paths import project_root


@dataclass
class PublishRunResult:
    ok: bool
    message: str
    returncode: int = 0


def run_database_publish(
    *,
    root: Optional[Path] = None,
    skip_photos: bool = True,
    full_base: bool = False,
    use_gh: bool = True,
    log: Optional[Callable[[str], None]] = None,
) -> PublishRunResult:
    """
    Package + upload public DB. Requires ``data/db_publish.allow``.

    Uses ``scripts/publish_database_release.py`` so CLI and GUI share one path.
    """
    root = Path(root) if root else project_root()

    def _log(m: str) -> None:
        if log:
            try:
                log(m)
            except Exception:
                pass

    if not is_publish_allowed(root):
        return PublishRunResult(
            False,
            "Publish disabled on this machine (missing data/db_publish.allow).",
            2,
        )

    script = root / "scripts" / "publish_database_release.py"
    if not script.is_file():
        return PublishRunResult(False, f"Missing {script}", 1)

    cmd = [sys.executable, str(script)]
    if use_gh:
        cmd.append("--use-gh")
    if skip_photos:
        cmd.append("--skip-photos")
    if full_base:
        cmd.append("--full-base")

    _log("Publishing public database to GitHub…")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600 * 6,
        )
    except subprocess.TimeoutExpired:
        return PublishRunResult(False, "Publish timed out", 1)
    except Exception as e:
        return PublishRunResult(False, f"Publish failed: {e}", 1)

    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    for line in out.splitlines():
        line = line.strip()
        if line:
            _log(line[:240])

    if proc.returncode != 0:
        tail = out.strip().splitlines()[-3:] if out.strip() else []
        detail = " | ".join(tail) if tail else f"exit {proc.returncode}"
        return PublishRunResult(False, f"Publish failed: {detail}", proc.returncode)

    clear_pending_listings(root)
    return PublishRunResult(True, "Published public database to GitHub", 0)
