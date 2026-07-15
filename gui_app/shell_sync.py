"""GitHub public-database sync helpers for ArchiverApp."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from tkinter import messagebox


class ShellSyncMixin:
    """First-run prompt + background DB sync from GitHub Releases."""

    def _maybe_prompt_or_sync_database(self) -> None:
        """Ask once about GitHub DB sync; if enabled, refresh on every open."""
        try:
            from scraper.app_settings import (
                load_settings,
                save_settings,
                normalize_settings,
            )
            from scraper.db_sync import should_prompt_first_run
        except Exception:
            return

        try:
            sett = normalize_settings(self.app_settings or load_settings())
        except Exception:
            sett = dict(self.app_settings or {})

        try:
            from scraper.paths import resolve_under_root

            db_path = resolve_under_root(self.db_path)
        except Exception:
            db_path = Path(self.db_path)
            if not db_path.is_absolute():
                db_path = (Path.cwd() / db_path).resolve()

        if should_prompt_first_run(sett, db_path):
            try:
                yes = messagebox.askyesno(
                    "Public database",
                    "Download the shared public offender database from GitHub?\n\n"
                    "• Contains publicly published registry fields only\n"
                    "• Includes archived mugshot photos (several GB — may take a while)\n"
                    "• Paths are project-relative (no local user folders)\n"
                    "• If you choose Yes, the app will check for updates on every open\n\n"
                    "You can change this later under Settings → Public database.",
                )
            except Exception:
                yes = False
            sett["db_sync_prompted"] = True
            sett["db_sync_enabled"] = bool(yes)
            if yes:
                sett["db_sync_on_startup"] = True
            try:
                save_settings(sett)
                self.app_settings = normalize_settings(sett)
            except Exception:
                self.app_settings = sett
            if yes:
                self._run_db_sync_background(force=True, reason="first-run download")
            return

        # When sync is enabled, always check on every open (delta-friendly).
        if sett.get("db_sync_enabled"):
            if not sett.get("db_sync_on_startup", True):
                sett["db_sync_on_startup"] = True
                try:
                    from scraper.app_settings import save_settings, normalize_settings

                    save_settings(sett)
                    self.app_settings = normalize_settings(sett)
                except Exception:
                    self.app_settings = sett
            self._run_db_sync_background(force=False, reason="startup update check")

    def _run_db_sync_background(self, *, force: bool = False, reason: str = "") -> None:
        """Download/update public DB off the UI thread."""
        if getattr(self, "_db_sync_bg_running", False):
            return
        self._db_sync_bg_running = True
        sett = getattr(self, "app_settings", {}) or {}
        repo = str(sett.get("db_sync_repo") or "HyperboreanSlug/SORPA")
        tag = str(sett.get("db_sync_tag") or "database-latest")
        try:
            from scraper.paths import resolve_under_root

            db_path = resolve_under_root(self.db_path)
        except Exception:
            db_path = Path(self.db_path)

        def worker() -> None:
            from scraper.db_sync import download_and_install_db

            def log(m: str) -> None:
                try:
                    self.log_queue.put(f"DB sync ({reason or 'manual'}): {m}")
                except Exception:
                    pass

            err = None
            result = None
            try:
                result = download_and_install_db(
                    db_path, repo=repo, tag=tag, force=force, log=log
                )
            except Exception as e:
                err = str(e)

            def done() -> None:
                self._db_sync_bg_running = False
                if err:
                    try:
                        self.log_queue.put(f"DB sync error: {err}")
                    except Exception:
                        pass
                    return
                if result is None:
                    return
                try:
                    self.log_queue.put(f"DB sync: {result.message}")
                except Exception:
                    pass
                if result.ok and result.action in ("downloaded", "updated"):
                    try:
                        if hasattr(self, "_after_db_data_changed"):
                            self._after_db_data_changed()
                        else:
                            self._refresh_header_db_path()
                    except Exception:
                        pass
                    try:
                        if hasattr(self, "stats_label"):
                            self.stats_label.configure(text=result.message[:80])
                    except Exception:
                        pass

            try:
                self.after(0, done)
            except Exception:
                self._db_sync_bg_running = False

        threading.Thread(target=worker, name="db-sync-startup", daemon=True).start()
