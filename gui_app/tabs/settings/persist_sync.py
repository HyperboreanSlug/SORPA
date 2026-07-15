"""Settings: public DB sync toggle + manual refresh (non-blocking)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from tkinter import messagebox

from gui_app.theme import C


class SettingsDbSyncMixin:
    def _settings_on_db_sync_toggle(self) -> None:
        """Persist enable flag immediately when checkbox changes."""
        try:
            from scraper.app_settings import load_settings, save_settings, normalize_settings

            raw = load_settings()
            raw["db_sync_enabled"] = bool(self.settings_db_sync_enabled.get())
            raw["db_sync_on_startup"] = bool(raw["db_sync_enabled"])
            if raw["db_sync_enabled"]:
                raw["db_sync_prompted"] = True
            save_settings(raw)
            self.app_settings = normalize_settings(raw)
        except Exception:
            pass

    def _settings_db_sync_now(self) -> None:
        """Manual refresh from GitHub Releases (header progress; UI stays usable)."""
        if getattr(self, "_db_sync_bg_running", False) or getattr(
            self, "_db_sync_running", False
        ):
            try:
                self.settings_db_sync_status.configure(
                    text="Sync already running…", text_color=C["muted"]
                )
            except Exception:
                pass
            return

        # Persist repo before sync
        try:
            from scraper.app_settings import load_settings, save_settings, normalize_settings

            raw = load_settings()
            repo = (
                (
                    self.settings_db_sync_repo.get()
                    if hasattr(self, "settings_db_sync_repo")
                    else ""
                )
                or raw.get("db_sync_repo")
                or "HyperboreanSlug/SORPA"
            ).strip()
            raw["db_sync_repo"] = repo
            save_settings(raw)
            self.app_settings = normalize_settings(raw)
        except Exception:
            pass

        self._db_sync_running = True
        try:
            self.settings_db_sync_status.configure(
                text="Syncing in background…", text_color=C["muted"]
            )
        except Exception:
            pass

        def on_done(result, err) -> None:
            self._db_sync_running = False
            try:
                if err:
                    self.settings_db_sync_status.configure(
                        text=str(err)[:120], text_color=C["danger"]
                    )
                    return
                if result is None:
                    self.settings_db_sync_status.configure(
                        text="No result", text_color=C["muted"]
                    )
                    return
                col = C["success"] if result.ok else C["danger"]
                self.settings_db_sync_status.configure(
                    text=result.message, text_color=col
                )
                if result.ok:
                    try:
                        from scraper.app_settings import (
                            load_settings,
                            save_settings,
                            normalize_settings,
                        )

                        raw = load_settings()
                        raw["db_sync_enabled"] = True
                        raw["db_sync_prompted"] = True
                        raw["db_sync_on_startup"] = True
                        if hasattr(self, "settings_db_sync_repo"):
                            raw["db_sync_repo"] = (
                                self.settings_db_sync_repo.get()
                                or raw.get("db_sync_repo")
                                or "HyperboreanSlug/SORPA"
                            ).strip()
                        save_settings(raw)
                        self.app_settings = normalize_settings(raw)
                        if hasattr(self, "settings_db_sync_enabled"):
                            self.settings_db_sync_enabled.set(True)
                    except Exception:
                        pass
                else:
                    try:
                        messagebox.showerror("Database refresh", result.message)
                    except Exception:
                        pass
            except Exception:
                pass

        self._run_db_sync_background(
            force=True, reason="manual", on_done=on_done
        )
