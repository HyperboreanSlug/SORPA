"""Publisher-side: auto-upload when pending listing changes hit threshold."""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional


class ShellSyncPublishMixin:
    """Upload public DB from this machine only (db_publish.allow)."""

    def _maybe_auto_publish_public_db(self) -> None:
        """If publisher + auto enabled + pending ≥ threshold, publish in background."""
        if getattr(self, "_closing", False):
            return
        if getattr(self, "_db_publish_bg_running", False):
            return
        if getattr(self, "_db_sync_bg_running", False):
            return
        try:
            from scraper.app_settings import load_settings, normalize_settings
            from scraper.db_publish_gate import is_publish_allowed
            from scraper.db_publish_pending import get_pending_listings, should_publish
            from scraper.paths import project_root

            if not is_publish_allowed(project_root()):
                return
            sett = normalize_settings(
                getattr(self, "app_settings", None) or load_settings()
            )
            if not sett.get("db_auto_publish_enabled", True):
                return
            thr = int(sett.get("db_publish_change_threshold") or 2500)
            pending = get_pending_listings(project_root())
            if not should_publish(thr, project_root()):
                return
            try:
                self.log_queue.put(
                    f"Auto-publish: {pending:,} listings changed "
                    f"(threshold {thr:,}) — uploading…"
                )
            except Exception:
                pass
            self._run_db_publish_background(
                reason="auto-threshold",
                skip_photos=True,
            )
        except Exception:
            pass

    def _run_db_publish_background(
        self,
        *,
        reason: str = "manual",
        skip_photos: bool = True,
        full_base: bool = False,
        on_done: Optional[Callable[[Any, Optional[str]], None]] = None,
    ) -> None:
        """Publish (upload) off the UI thread with header progress."""
        if getattr(self, "_db_publish_bg_running", False):
            if on_done:
                try:
                    on_done(None, "Publish already running")
                except Exception:
                    pass
            return
        if getattr(self, "_db_sync_bg_running", False):
            if on_done:
                try:
                    on_done(None, "Download sync in progress — try again shortly")
                except Exception:
                    pass
            return

        self._db_publish_bg_running = True
        try:
            self._db_sync_ui_show("Publishing database to GitHub…")
        except Exception:
            pass

        def worker() -> None:
            from scraper.db_publish_run import run_database_publish
            from scraper.paths import project_root

            def log(m: str) -> None:
                try:
                    self.log_queue.put(f"DB publish ({reason}): {m}")
                except Exception:
                    pass
                self._db_sync_ui_update(m)

            err = None
            result = None
            try:
                result = run_database_publish(
                    root=project_root(),
                    skip_photos=skip_photos,
                    full_base=full_base,
                    use_gh=True,
                    log=log,
                )
                if result is not None and not result.ok:
                    err = result.message
            except Exception as e:
                err = str(e)

            def done() -> None:
                self._db_publish_bg_running = False
                final = None
                if err:
                    final = f"DB publish error: {err}"
                    try:
                        self.log_queue.put(final)
                    except Exception:
                        pass
                    self._db_sync_ui_hide(final)
                elif result is not None:
                    final = result.message
                    try:
                        self.log_queue.put(f"DB publish: {final}")
                    except Exception:
                        pass
                    if result.ok:
                        self._db_sync_ui_complete_bar()
                    self._db_sync_ui_hide(final)
                else:
                    self._db_sync_ui_hide("Ready")

                if on_done is not None:
                    try:
                        on_done(result, err)
                    except Exception:
                        pass

            try:
                self.after(0, done)
            except Exception:
                self._db_publish_bg_running = False

        threading.Thread(target=worker, name="db-publish-bg", daemon=True).start()

    def _sync_now_public_db(self) -> None:
        """
        Sync now button: publisher machine uploads; others download if enabled.
        """
        try:
            from scraper.db_publish_gate import is_publish_allowed
            from scraper.paths import project_root

            if is_publish_allowed(project_root()):
                self._run_db_publish_background(reason="sync-now", skip_photos=True)
                return
        except Exception:
            pass
        # Clients: pull from GitHub
        self._run_db_sync_background(force=True, reason="sync-now download")
