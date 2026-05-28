"""
Unit tests for ``tasks.trivy_db_bootstrap`` — W6-#44.

The bootstrap hook drives the worker-boot ``trivy --download-db-only`` call
on a background thread once Celery's ``worker_ready`` signal fires. The
tests cover:

  - Disabled toggle (``TRIVY_DB_BOOTSTRAP_ON_START=false``) returns None
    without touching the adapter.
  - Blocking mode runs the adapter inline → ``last_result`` populated.
  - Non-blocking mode spawns a daemon thread → result populated after join.
  - Re-entrant call while a thread is alive returns the running thread,
    does not spawn a second.
  - Adapter raising (defensive: it should NEVER raise, but a future refactor
    must not crash the worker) → ``last_result`` set to failed.
  - The ``worker_ready`` signal handler entry point delegates to
    :func:`run_bootstrap` without raising.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from integrations.trivy import TrivyDbDownloadResult
from tasks import trivy_db_bootstrap as bootstrap_module
from tasks.trivy_db_bootstrap import (
    _bootstrap_runner,
    _on_worker_ready,
    run_bootstrap,
)


@pytest.fixture(autouse=True)
def reset_module_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the module-level last_result / _thread between tests.

    The bootstrap module owns process-wide state for cross-call observability;
    tests need a clean slate so a prior assertion doesn't bleed forward.
    """
    monkeypatch.setattr(bootstrap_module, "last_result", None)
    monkeypatch.setattr(bootstrap_module, "_thread", None)


# ---------------------------------------------------------------------------
# Disabled toggle
# ---------------------------------------------------------------------------


def test_run_bootstrap_disabled_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``TRIVY_DB_BOOTSTRAP_ON_START=false`` → no work, no thread, no adapter call."""
    monkeypatch.setenv("TRIVY_DB_BOOTSTRAP_ON_START", "false")

    def _explode(**_kw: Any) -> Any:
        raise AssertionError("adapter must not be called when bootstrap is disabled")

    monkeypatch.setattr(bootstrap_module, "download_db_only", _explode)

    out = run_bootstrap(blocking=False)
    assert out is None
    assert bootstrap_module.last_result is None


# ---------------------------------------------------------------------------
# Blocking mode — synchronous adapter invocation
# ---------------------------------------------------------------------------


def test_run_bootstrap_blocking_populates_last_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """blocking=True drives the runner inline and stores the result."""
    monkeypatch.setenv("TRIVY_DB_BOOTSTRAP_ON_START", "true")

    def _fake_download(*, timeout_seconds: int) -> TrivyDbDownloadResult:
        assert timeout_seconds > 0
        return TrivyDbDownloadResult(status="downloaded", duration_seconds=12.0)

    monkeypatch.setattr(bootstrap_module, "download_db_only", _fake_download)

    out = run_bootstrap(blocking=True)
    assert out is None  # blocking mode returns None
    assert bootstrap_module.last_result is not None
    assert bootstrap_module.last_result.status == "downloaded"
    assert bootstrap_module.last_result.duration_seconds == 12.0


def test_run_bootstrap_blocking_records_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``skipped`` outcome lands in last_result without raising or logging ERROR."""
    monkeypatch.setenv("TRIVY_DB_BOOTSTRAP_ON_START", "true")

    def _fake_download(*, timeout_seconds: int) -> TrivyDbDownloadResult:
        return TrivyDbDownloadResult(status="skipped", duration_seconds=0.0)

    monkeypatch.setattr(bootstrap_module, "download_db_only", _fake_download)

    run_bootstrap(blocking=True)
    assert bootstrap_module.last_result is not None
    assert bootstrap_module.last_result.status == "skipped"


def test_run_bootstrap_blocking_records_failure_as_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``failed`` outcome MUST land in last_result so the admin panel can render."""
    monkeypatch.setenv("TRIVY_DB_BOOTSTRAP_ON_START", "true")

    def _fake_download(*, timeout_seconds: int) -> TrivyDbDownloadResult:
        return TrivyDbDownloadResult(
            status="failed",
            duration_seconds=1.0,
            error="mirror unreachable",
        )

    monkeypatch.setattr(bootstrap_module, "download_db_only", _fake_download)

    run_bootstrap(blocking=True)
    assert bootstrap_module.last_result is not None
    assert bootstrap_module.last_result.status == "failed"
    assert bootstrap_module.last_result.error == "mirror unreachable"


def test_run_bootstrap_adapter_raise_does_not_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive: ``download_db_only`` should not raise, but a future regression
    must not crash the worker. The runner catches and records a failed result."""
    monkeypatch.setenv("TRIVY_DB_BOOTSTRAP_ON_START", "true")

    def _raise(**_kw: Any) -> Any:
        raise RuntimeError("future refactor broke the contract")

    monkeypatch.setattr(bootstrap_module, "download_db_only", _raise)

    run_bootstrap(blocking=True)
    assert bootstrap_module.last_result is not None
    assert bootstrap_module.last_result.status == "failed"
    assert "RuntimeError" in (bootstrap_module.last_result.error or "")


# ---------------------------------------------------------------------------
# Non-blocking mode — spawned thread
# ---------------------------------------------------------------------------


def test_run_bootstrap_non_blocking_spawns_daemon_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """blocking=False returns a daemon Thread that runs the adapter."""
    monkeypatch.setenv("TRIVY_DB_BOOTSTRAP_ON_START", "true")
    barrier = threading.Event()

    def _fake_download(*, timeout_seconds: int) -> TrivyDbDownloadResult:
        # Confirm the thread executes — the barrier is released only here.
        barrier.set()
        return TrivyDbDownloadResult(status="downloaded", duration_seconds=0.1)

    monkeypatch.setattr(bootstrap_module, "download_db_only", _fake_download)

    thread = run_bootstrap(blocking=False)
    assert thread is not None
    assert thread.daemon is True
    assert thread.name == "trivy-db-bootstrap"
    thread.join(timeout=5)
    assert barrier.is_set(), "background thread never ran the adapter"
    assert bootstrap_module.last_result is not None
    assert bootstrap_module.last_result.status == "downloaded"


def test_run_bootstrap_non_blocking_idempotent_reentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling run_bootstrap while a thread is alive returns the same thread."""
    monkeypatch.setenv("TRIVY_DB_BOOTSTRAP_ON_START", "true")
    release = threading.Event()

    def _fake_download(*, timeout_seconds: int) -> TrivyDbDownloadResult:
        release.wait(timeout=5)
        return TrivyDbDownloadResult(status="downloaded", duration_seconds=0.1)

    monkeypatch.setattr(bootstrap_module, "download_db_only", _fake_download)

    t1 = run_bootstrap(blocking=False)
    assert t1 is not None
    # Second call sees the running thread and returns IT, not a new one.
    t2 = run_bootstrap(blocking=False)
    assert t2 is t1

    # Let the first one finish so the test doesn't hang.
    release.set()
    t1.join(timeout=5)
    assert not t1.is_alive()


# ---------------------------------------------------------------------------
# Signal handler — worker_ready hook
# ---------------------------------------------------------------------------


def test_on_worker_ready_delegates_to_run_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The signal handler must call run_bootstrap exactly once, non-blocking."""
    monkeypatch.setenv("TRIVY_DB_BOOTSTRAP_ON_START", "true")
    calls: list[bool] = []

    def _fake_run_bootstrap(*, blocking: bool) -> Any:
        calls.append(blocking)
        return None

    monkeypatch.setattr(bootstrap_module, "run_bootstrap", _fake_run_bootstrap)

    # Celery passes sender + arbitrary kwargs to signal handlers.
    _on_worker_ready(sender=None, foo="bar")
    assert calls == [False]


# ---------------------------------------------------------------------------
# Pure helper — _bootstrap_runner branch coverage
# ---------------------------------------------------------------------------


def test_bootstrap_runner_writes_downloaded_to_last_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct runner invocation surfaces the downloaded outcome."""
    monkeypatch.setattr(
        bootstrap_module,
        "download_db_only",
        lambda *, timeout_seconds: TrivyDbDownloadResult(
            status="downloaded", duration_seconds=7.5
        ),
    )
    _bootstrap_runner(60)
    assert bootstrap_module.last_result is not None
    assert bootstrap_module.last_result.status == "downloaded"


def test_bootstrap_runner_writes_timeout_to_last_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timeout outcome surfaces with the timeout error."""
    monkeypatch.setattr(
        bootstrap_module,
        "download_db_only",
        lambda *, timeout_seconds: TrivyDbDownloadResult(
            status="timeout", duration_seconds=900.0, error="hit cap"
        ),
    )
    _bootstrap_runner(900)
    assert bootstrap_module.last_result is not None
    assert bootstrap_module.last_result.status == "timeout"
    assert bootstrap_module.last_result.error == "hit cap"
