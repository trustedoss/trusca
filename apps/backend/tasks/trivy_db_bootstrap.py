"""
Trivy DB worker-boot bootstrap hook — W6-#44.

Registers a Celery ``worker_ready`` handler that fires ``trivy --download-db-only``
on a background thread the first time a worker process becomes healthy. Without
this, a fresh worker would have no DB cached and the first scan to call
``trivy sbom`` would synchronously pull the ~500 MiB Trivy DB inside the scan
task — bloating the user-visible scan duration by 1-3 minutes and risking a
soft-timeout on slow links.

Why ``worker_ready`` (not ``worker_init`` or the Dockerfile ENTRYPOINT):
  - ``worker_init`` fires before the worker has finished registering with the
    broker, so any logging here is racing with Celery's own start-up plumbing
    and the task queue is not yet drained.
  - An entrypoint-script ``trivy --download-db-only`` would block Celery
    startup for the whole 1-3 minute first-download window, making the
    docker-compose ``service_healthy`` gate flake-prone (the worker is up
    but its healthcheck — ``celery inspect ping`` — fails because Celery
    is still in the dpkg install path).
  - ``worker_ready`` is the documented "worker is now consuming the queue"
    hook (Celery docs §"Signal reference"), which is exactly the moment we
    want to start the download — the worker is healthy and can serve mock /
    cached scans WHILE the download runs in the background.

Background thread (not a Celery task):
  - The download is the only side effect this hook owns. Spawning a
    Celery sub-task here would deadlock the worker on its own queue (a
    single-concurrency worker would consume the task on its only slot,
    blocking scan tasks until the download finished). A daemon
    ``threading.Thread`` runs alongside the worker's main loop and lets
    scan tasks land normally.
  - Trivy itself takes a file lock on ``cache_dir/db/`` for the duration
    of the download, so a scan that races the bootstrap will block on
    the lock (typically <60s) and then read the freshly-swapped manifest.
    The lock window is acceptable; the alternative (no bootstrap) is
    every fresh worker paying that 1-3 minute cost on its first scan.

Graceful degradation:
  - Air-gapped or offline at boot? The download fails (timeout / 502 /
    DNS), the function logs a WARNING, and the prior DB cache (if any)
    stays intact. Scan tasks keep matching against whatever DB is on disk
    — no panic, no auto-restart loop. The W6-#43e admin panel surfaces
    the staleness so the operator can act.
  - First-ever boot with no network? The bootstrap fails, ``trivy sbom``
    on the first scan also fails, the scan task reports the failure
    structurally and the user sees a clean error in the UI. The worker
    keeps running.

Idempotency:
  - ``worker_ready`` fires once per worker PROCESS, but a multi-process
    deployment (e.g. ``celery worker --concurrency=N --pool=prefork``)
    forks workers that EACH fire the signal independently. Two parallel
    ``trivy --download-db-only`` calls on the same cache dir are safe:
    Trivy serialises on its own file lock and the second call no-ops on
    an already-current manifest.
  - Worker restart cycles re-trigger the bootstrap — that is intentional
    so a worker pod that had its volume re-attached (or recycled by k8s)
    re-populates the cache automatically.

CLAUDE.md compliance:
  - Core rule #3: the only Trivy invocation here is the boot-time download;
    actual scans still run inside Celery tasks (``scan_source`` /
    ``scan_container``).
  - Core rule #11: ``trivy_db_bootstrap_on_start`` and
    ``trivy_db_bootstrap_timeout_seconds`` are read at signal-handler call
    time, never at module level.
"""

from __future__ import annotations

import threading
from typing import Any

import structlog
from celery.signals import worker_ready

from core.config import (
    trivy_db_bootstrap_on_start,
    trivy_db_bootstrap_timeout_seconds,
)
from integrations.trivy import (
    TrivyDbDownloadResult,
    download_db_only,
)

log = structlog.get_logger("tasks.trivy_db_bootstrap")

# Module-level state for tests + observability. Two values:
#   ``last_result`` — most recent download outcome (TrivyDbDownloadResult or
#     None on a fresh process). The admin panel exposes status via the
#     on-disk metadata.json route; this field is for test assertions and
#     ad-hoc debugging via the Celery inspect API.
#   ``_thread``    — the running download thread, exposed so a test can
#     join() it without sleeping. The bootstrap function returns the thread
#     so callers can also receive it directly.
last_result: TrivyDbDownloadResult | None = None
_thread_lock = threading.Lock()
_thread: threading.Thread | None = None


def _bootstrap_runner(timeout_seconds: int) -> None:
    """Body of the background bootstrap thread.

    A standalone helper (not a closure) so unit tests can drive it
    synchronously without going through ``threading.Thread.start()``.
    Stores the outcome in :data:`last_result` for inspection.
    """
    global last_result
    log.info("trivy_db_bootstrap_started", timeout_seconds=timeout_seconds)
    try:
        result = download_db_only(timeout_seconds=timeout_seconds)
    except Exception as exc:  # noqa: BLE001 — thread must not propagate
        # download_db_only itself never raises (designed to return a
        # failed-result), but defensive: a future refactor or a structlog
        # backend failure must not leak a stack into the worker logs.
        log.warning(
            "trivy_db_bootstrap_unexpected_error",
            error=str(exc)[:300],
            error_type=type(exc).__name__,
        )
        last_result = TrivyDbDownloadResult(
            status="failed",
            duration_seconds=0.0,
            error=f"{type(exc).__name__}: {str(exc)[:300]}",
        )
        return
    last_result = result
    if result.status == "downloaded":
        log.info("trivy_db_bootstrap_complete", duration_seconds=result.duration_seconds)
    elif result.status == "skipped":
        log.info(
            "trivy_db_bootstrap_skipped",
            duration_seconds=result.duration_seconds,
        )
    else:
        # timeout / failed — DEGRADED, not fatal. Prior DB stays.
        log.warning(
            "trivy_db_bootstrap_degraded",
            status=result.status,
            duration_seconds=result.duration_seconds,
            error=result.error,
        )


def run_bootstrap(*, blocking: bool = False) -> threading.Thread | None:
    """Kick off the Trivy DB bootstrap (background thread by default).

    Two call sites:
      - The Celery ``worker_ready`` signal handler below — runs with
        ``blocking=False`` so the worker can start consuming the queue
        immediately.
      - Unit tests — pass ``blocking=True`` for deterministic assertions.
        The download runner is a regular function under test (we
        monkeypatch :func:`integrations.trivy.download_db_only`), so the
        thread machinery is exercised but the subprocess call is not.

    Returns the thread when ``blocking=False`` (so a test can join() it),
    or ``None`` when the bootstrap is disabled by config or when called
    in blocking mode (the work is already done).
    """
    global _thread
    if not trivy_db_bootstrap_on_start():
        log.info("trivy_db_bootstrap_disabled_by_config")
        return None

    timeout_seconds = trivy_db_bootstrap_timeout_seconds()
    if blocking:
        _bootstrap_runner(timeout_seconds)
        return None

    with _thread_lock:
        # Idempotency guard for multi-process / multi-signal edge cases:
        # if a thread is already running for this process, do not spawn a
        # second one. The Celery worker_ready signal is supposed to fire
        # once per process, but a worker restart / cold-start race could
        # in theory fire it twice — Trivy serialises on its file lock so
        # a second download is harmless, but a second thread is wasteful.
        if _thread is not None and _thread.is_alive():
            log.info("trivy_db_bootstrap_already_running")
            return _thread
        thread = threading.Thread(
            target=_bootstrap_runner,
            args=(timeout_seconds,),
            name="trivy-db-bootstrap",
            daemon=True,
        )
        _thread = thread
        thread.start()
        return thread


@worker_ready.connect  # type: ignore[misc]
def _on_worker_ready(sender: Any | None = None, **_: Any) -> None:
    """Celery signal handler — fires once the worker is consuming the queue.

    Delegates to :func:`run_bootstrap` so the same code path is reachable
    from tests without driving Celery's signal machinery.
    """
    run_bootstrap(blocking=False)


__all__ = [
    "run_bootstrap",
    "_bootstrap_runner",
    "_on_worker_ready",
    "last_result",
]
