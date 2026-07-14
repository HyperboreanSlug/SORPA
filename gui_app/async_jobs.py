"""Background jobs with main-thread result delivery (never freeze the UI).

Tk is not thread-safe. Workers must not call ``widget.after`` / configure.
They put results on a queue; the app polls it on the main thread.
"""
from __future__ import annotations

import queue
import threading
import traceback
from typing import Any, Callable, Optional

# work() -> result
WorkFn = Callable[[], Any]
# done(result=..., error=...)  error is Exception | None
DoneFn = Callable[..., None]


class AsyncJobsMixin:
    """Mixin: ``run_bg(work, done)`` + drain loop on the Tk main thread."""

    def _init_async_jobs(self) -> None:
        self._bg_queue: queue.Queue = queue.Queue()
        self._bg_seq = 0
        try:
            self.after(40, self._drain_bg_jobs)
        except Exception:
            pass

    def run_bg(
        self,
        work: WorkFn,
        done: DoneFn,
        *,
        name: str = "bg-job",
    ) -> int:
        """Run *work* in a daemon thread; call *done* on the UI thread.

        ``done`` is invoked as ``done(result=..., error=...)``.
        Returns a job id (monotonic) for optional cancellation checks.
        Refuses new work while the app is closing.
        """
        if getattr(self, "_closing", False):
            return -1
        if not hasattr(self, "_bg_queue"):
            self._init_async_jobs()
        self._bg_seq = int(getattr(self, "_bg_seq", 0) or 0) + 1
        job_id = self._bg_seq

        def worker() -> None:
            if getattr(self, "_closing", False):
                return
            result: Any = None
            err: Optional[BaseException] = None
            try:
                result = work()
            except BaseException as e:  # noqa: BLE001 — deliver to UI
                err = e
                try:
                    traceback.print_exc()
                except Exception:
                    pass
            if getattr(self, "_closing", False):
                return
            try:
                self._bg_queue.put((job_id, done, result, err))
            except Exception:
                pass

        threading.Thread(target=worker, name=name, daemon=True).start()
        return job_id

    def _drain_bg_jobs(self) -> None:
        if getattr(self, "_closing", False):
            return
        try:
            while True:
                job_id, done, result, err = self._bg_queue.get_nowait()
                try:
                    done(result=result, error=err)
                except TypeError:
                    # Allow simple done(result) callbacks
                    try:
                        if err is not None:
                            done(None)
                        else:
                            done(result)
                    except Exception:
                        pass
                except Exception:
                    try:
                        traceback.print_exc()
                    except Exception:
                        pass
        except queue.Empty:
            pass
        try:
            self.after(40, self._drain_bg_jobs)
        except Exception:
            pass
