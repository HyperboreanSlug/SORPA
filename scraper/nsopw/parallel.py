"""Parallel, per-jurisdiction report fetching for the NSOPW builder.

The NSOPW *search* API (nsopw-api.ojp.gov) is a single Cloudflare-protected
host and stays serialized in the builder. The heavy, parallelizable work is
fetching each offender's **report page from their state's registry website**
(iCrimeWatch, OffenderWatch, state DPS sites, …).

``JurisdictionReportPool`` runs those report fetches on a configurable number
of worker threads while guaranteeing a hard invariant:

    **No two worker threads ever hit the same state website at the same time.**

This is enforced by keying work on jurisdiction: a jurisdiction is marked
"active" while any worker is fetching one of its URLs, and no other worker may
claim a job for an active jurisdiction. Each jurisdiction also has its own
minimum-interval limiter so per-state pacing (the report delay) is respected.

Workers only perform network + file I/O. They never touch the sqlite database
(that stays single-threaded on the caller): each worker owns its own HTTP
session (``make_fetcher``) and returns the enriched job to the caller for
insertion.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ReportJob:
    """One offender report to fetch, tagged with its owning jurisdiction.

    ``jurisdiction`` is the sharding key: the pool guarantees at most one
    worker processes jobs for a given jurisdiction at any instant.
    """

    jurisdiction: str
    url: str
    record: Dict[str, Any]
    hit: Any
    is_eth_match: bool
    save_html: bool = True
    names_label: str = ""
    # Filled in by the worker:
    demo: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    fetched: bool = False


class _IntervalLimiter:
    """Minimum interval between operation starts (per jurisdiction).

    Self-contained (no dependency on the builder module) to avoid an import
    cycle. Sleeps in short slices so cancellation is felt quickly.
    """

    _POLL_S = 0.05

    def __init__(self, min_interval: float):
        self.min_interval = max(0.0, float(min_interval))
        self._last = 0.0
        self._lock = threading.Lock()

    def set_interval(self, min_interval: float) -> None:
        self.min_interval = max(0.0, float(min_interval))

    def wait(self, cancel_check: Optional[Callable[[], bool]] = None) -> bool:
        """Block until min_interval since the last start. True if cancelled."""
        # Only one worker is ever in here per jurisdiction (pool invariant),
        # but guard _last anyway for safety.
        with self._lock:
            if cancel_check and cancel_check():
                return True
            if self.min_interval <= 0:
                self._last = time.monotonic()
                return bool(cancel_check and cancel_check())
            now = time.monotonic()
            remaining = self.min_interval - (now - self._last)
            if remaining <= 0:
                self._last = time.monotonic()
                return bool(cancel_check and cancel_check())
            end = now + remaining
            poll = max(0.02, float(self._POLL_S))
            while True:
                if cancel_check and cancel_check():
                    return True
                now = time.monotonic()
                left = end - now
                if left <= 0:
                    break
                time.sleep(left if left < poll else poll)
            self._last = time.monotonic()
            return bool(cancel_check and cancel_check())


class JurisdictionReportPool:
    """Thread pool for report fetches with per-jurisdiction concurrency limits.

    Usage::

        pool = JurisdictionReportPool(
            num_threads=4,
            make_fetcher=builder._make_report_fetcher,
            worker_fn=builder._worker_fetch,   # worker_fn(job, fetcher)
            report_delay=0.75,
            cancel_check=builder.cancel_check,
            log=log,
        )
        for job in jobs:
            pool.submit(job)
        for done in pool.collect(len(jobs)):
            ...  # finalize on the calling thread (DB insert etc.)
        pool.close()

    Default invariant (``max_per_jurisdiction=1``): at most one worker is active
    per ``job.jurisdiction`` at a time. Raise *max_per_jurisdiction* for
    single-state requeue/enrich so multiple threads may hit the same host while
    still sharing that jurisdiction's interval limiter.
    """

    def __init__(
        self,
        *,
        num_threads: int,
        make_fetcher: Callable[[], Any],
        worker_fn: Callable[[ReportJob, Any], Any],
        report_delay: float = 0.0,
        cancel_check: Optional[Callable[[], bool]] = None,
        log: Optional[Callable[[str], None]] = None,
        max_per_jurisdiction: int = 1,
    ):
        self.num_threads = max(1, int(num_threads))
        self._make_fetcher = make_fetcher
        self._worker_fn = worker_fn
        self._report_delay = max(0.0, float(report_delay))
        self._cancel_check = cancel_check or (lambda: False)
        self._log = log or (lambda _m: None)
        try:
            self._max_per_jur = max(1, int(max_per_jurisdiction))
        except (TypeError, ValueError):
            self._max_per_jur = 1

        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._pending: "Dict[str, deque]" = {}
        # Active in-flight count per jurisdiction (was a set when max=1).
        self._active: Dict[str, int] = {}
        self._limiters: Dict[str, _IntervalLimiter] = {}
        self._results: "queue.Queue[ReportJob]" = queue.Queue()
        self._closing = False
        self._submitted = 0

        self._threads: List[threading.Thread] = []
        for i in range(self.num_threads):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"nsopw-report-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)

    # -- configuration -------------------------------------------------------
    def set_report_delay(self, delay: float) -> None:
        """Update the per-jurisdiction min interval (live delay changes)."""
        d = max(0.0, float(delay))
        with self._lock:
            self._report_delay = d
            for lim in self._limiters.values():
                lim.set_interval(d)

    # -- submission / collection --------------------------------------------
    def submit(self, job: ReportJob) -> None:
        jur = (job.jurisdiction or "UNK").upper() or "UNK"
        job.jurisdiction = jur
        with self._cv:
            self._pending.setdefault(jur, deque()).append(job)
            self._submitted += 1
            self._cv.notify_all()

    def collect(self, n: int):
        """Yield ``n`` completed jobs (blocking). Cancel-aware via the queue."""
        got = 0
        while got < n:
            # Short timeout so cancel/close can unblock the collector.
            try:
                job = self._results.get(timeout=0.25)
            except queue.Empty:
                if self._closing or self._cancel_check():
                    # Drain whatever finished; abandon the rest.
                    while got < n:
                        try:
                            job = self._results.get_nowait()
                        except queue.Empty:
                            return
                        got += 1
                        yield job
                    return
                continue
            got += 1
            yield job

    def close(self) -> None:
        with self._cv:
            self._closing = True
            # Flush unclaimed pending as cancelled so collectors are not starved.
            for jur, dq in list(self._pending.items()):
                while dq:
                    job = dq.popleft()
                    job.fetched = False
                    job.error = job.error or "cancelled"
                    self._results.put(job)
            self._pending.clear()
            self._cv.notify_all()
        for t in self._threads:
            t.join(timeout=3.0)

    # -- internals -----------------------------------------------------------
    def _claim_job(self) -> Optional[ReportJob]:
        """Return a job under its per-jurisdiction concurrency cap.

        Must be called while holding ``self._lock``. Returns None when every
        pending jurisdiction is at ``max_per_jurisdiction`` capacity.
        """
        for jur, dq in self._pending.items():
            if not dq:
                continue
            active = int(self._active.get(jur, 0))
            if active >= self._max_per_jur:
                continue
            job = dq.popleft()
            self._active[jur] = active + 1
            if jur not in self._limiters:
                self._limiters[jur] = _IntervalLimiter(self._report_delay)
            return job
        return None

    def _worker_loop(self) -> None:
        fetcher = None
        try:
            fetcher = self._make_fetcher()
        except Exception as e:  # pragma: no cover - defensive
            self._log(f"  Report worker could not create HTTP session: {e}")
            fetcher = None
        try:
            while True:
                with self._cv:
                    job = None
                    while True:
                        if self._closing:
                            return
                        job = self._claim_job()
                        if job is not None:
                            break
                        self._cv.wait(timeout=0.2)
                    limiter = self._limiters[job.jurisdiction]

                # Process outside the lock so other jobs (other jurisdictions,
                # or extra slots of the same one) run in parallel.
                try:
                    if self._cancel_check() or self._closing:
                        job.fetched = False
                    else:
                        limiter.wait(self._cancel_check)
                        if self._cancel_check() or self._closing:
                            job.fetched = False
                        elif fetcher is None:
                            job.error = "no HTTP session for report worker"
                        else:
                            self._worker_fn(job, fetcher)
                            job.fetched = True
                except Exception as e:
                    job.error = f"report worker error [{job.jurisdiction}]: {e}"
                finally:
                    with self._cv:
                        left = int(self._active.get(job.jurisdiction, 1)) - 1
                        if left <= 0:
                            self._active.pop(job.jurisdiction, None)
                        else:
                            self._active[job.jurisdiction] = left
                        self._cv.notify_all()
                    self._results.put(job)
        finally:
            if fetcher is not None:
                try:
                    fetcher.close()
                except Exception:
                    pass
