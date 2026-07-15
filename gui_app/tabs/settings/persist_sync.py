"""Settings: public DB sync toggle, threshold, Sync now (non-blocking)."""
from __future__ import annotations

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
            if hasattr(self, "settings_db_auto_publish"):
                raw["db_auto_publish_enabled"] = bool(
                    self.settings_db_auto_publish.get()
                )
            if hasattr(self, "settings_db_publish_threshold"):
                try:
                    raw["db_publish_change_threshold"] = int(
                        str(self.settings_db_publish_threshold.get()).strip() or "2500"
                    )
                except ValueError:
                    raw["db_publish_change_threshold"] = 2500
            save_settings(raw)
            self.app_settings = normalize_settings(raw)
        except Exception:
            pass

    def _settings_refresh_pending_publish(self) -> None:
        """Show pending listing changes vs upload threshold."""
        try:
            from scraper.db_publish_gate import is_publish_allowed
            from scraper.db_publish_pending import get_pending_listings
            from scraper.paths import project_root

            pending = get_pending_listings(project_root())
            thr = 2500
            if hasattr(self, "settings_db_publish_threshold"):
                try:
                    thr = int(
                        str(self.settings_db_publish_threshold.get()).strip() or "2500"
                    )
                except ValueError:
                    thr = int(
                        (getattr(self, "app_settings", {}) or {}).get(
                            "db_publish_change_threshold", 2500
                        )
                    )
            pub = is_publish_allowed(project_root())
            role = "publisher" if pub else "download-only"
            text = f"Pending changes: {pending:,} / {thr:,}  ({role})"
            if hasattr(self, "settings_db_pending_label"):
                self.settings_db_pending_label.configure(text=text)
        except Exception:
            pass

    def _settings_db_sync_now_click(self) -> None:
        """Sync now: upload on publisher machine, else download."""
        self._settings_persist_publish_prefs()
        if getattr(self, "_db_publish_bg_running", False) or getattr(
            self, "_db_sync_bg_running", False
        ):
            try:
                self.settings_db_sync_status.configure(
                    text="Sync already running…", text_color=C["muted"]
                )
            except Exception:
                pass
            return

        try:
            self.settings_db_sync_status.configure(
                text="Syncing in background…", text_color=C["muted"]
            )
        except Exception:
            pass

        def on_done(result, err) -> None:
            try:
                self._settings_refresh_pending_publish()
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
                ok = bool(getattr(result, "ok", False))
                msg = getattr(result, "message", str(result))
                self.settings_db_sync_status.configure(
                    text=msg[:120],
                    text_color=C["success"] if ok else C["danger"],
                )
            except Exception:
                pass

        try:
            from scraper.db_publish_gate import is_publish_allowed
            from scraper.paths import project_root

            if is_publish_allowed(project_root()):
                self._run_db_publish_background(
                    reason="sync-now", skip_photos=True, on_done=on_done
                )
                return
        except Exception:
            pass
        self._run_db_sync_background(
            force=True, reason="sync-now download", on_done=on_done
        )

    def _settings_persist_publish_prefs(self) -> None:
        try:
            from scraper.app_settings import load_settings, save_settings, normalize_settings

            raw = load_settings()
            if hasattr(self, "settings_db_auto_publish"):
                raw["db_auto_publish_enabled"] = bool(
                    self.settings_db_auto_publish.get()
                )
            if hasattr(self, "settings_db_publish_threshold"):
                try:
                    raw["db_publish_change_threshold"] = int(
                        str(self.settings_db_publish_threshold.get()).strip() or "2500"
                    )
                except ValueError:
                    raw["db_publish_change_threshold"] = 2500
            if hasattr(self, "settings_db_sync_repo"):
                raw["db_sync_repo"] = (
                    self.settings_db_sync_repo.get()
                    or raw.get("db_sync_repo")
                    or "HyperboreanSlug/SORPA"
                ).strip()
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

        self._settings_persist_publish_prefs()
        self._db_sync_running = True
        try:
            self.settings_db_sync_status.configure(
                text="Downloading in background…", text_color=C["muted"]
            )
        except Exception:
            pass

        def on_done(result, err) -> None:
            self._db_sync_running = False
            try:
                self._settings_refresh_pending_publish()
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
            force=True, reason="manual-download", on_done=on_done
        )
