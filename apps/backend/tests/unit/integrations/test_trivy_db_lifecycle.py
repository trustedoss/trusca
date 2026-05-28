"""
Unit tests for ``integrations.trivy.download_db_only`` — W6-#44.

The adapter helper drives both the worker-boot bootstrap hook and the
weekly Celery beat refresh. It MUST:
  - Return a ``TrivyDbDownloadResult`` (never raise) so every caller can
    branch on ``status`` without try/except wrapping.
  - Skip cleanly in mock mode (TRUSTEDOSS_SCAN_BACKEND=mock).
  - Skip cleanly when the trivy binary is absent.
  - Surface success + duration on the happy path.
  - Surface ``timeout`` / ``failed`` distinctly, both with redacted
    stderr_tail (no full stderr).
  - Pass the scrubbed env (no DATABASE_URL / SECRET_KEY leakage to the
    subprocess).

We never actually invoke ``trivy`` — every test monkeypatches the
``subprocess.run`` call to assert behaviour without a real binary.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from integrations.trivy import (
    TrivyDbDownloadResult,
    download_db_only,
)

# ---------------------------------------------------------------------------
# Skip paths — mock backend + missing binary
# ---------------------------------------------------------------------------


def test_download_db_only_mock_backend_skips_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``TRUSTEDOSS_SCAN_BACKEND=mock`` returns status=skipped without subprocess."""
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")

    def _explode(*_a: Any, **_kw: Any) -> Any:  # pragma: no cover — guarded
        raise AssertionError("subprocess.run must not be called in mock mode")

    monkeypatch.setattr(subprocess, "run", _explode)

    result = download_db_only(timeout_seconds=60)
    assert isinstance(result, TrivyDbDownloadResult)
    assert result.status == "skipped"
    assert result.duration_seconds == 0.0
    assert result.error is None
    assert result.stderr_tail is None


def test_download_db_only_missing_binary_skips_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real mode + ``shutil.which("trivy") is None`` → status=skipped."""
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr("integrations.trivy.shutil.which", lambda _name: None)

    def _explode(*_a: Any, **_kw: Any) -> Any:  # pragma: no cover — guarded
        raise AssertionError("subprocess.run must not be called when binary is absent")

    monkeypatch.setattr(subprocess, "run", _explode)

    result = download_db_only(timeout_seconds=60)
    assert result.status == "skipped"
    assert result.duration_seconds == 0.0


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_download_db_only_happy_path_returns_downloaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """returncode=0 → status=downloaded with a non-negative duration."""
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr("integrations.trivy.shutil.which", lambda _name: "/usr/bin/trivy")

    captured: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = download_db_only(timeout_seconds=300)
    assert result.status == "downloaded"
    assert result.duration_seconds >= 0.0
    assert result.error is None
    assert result.stderr_tail is None

    # Command shape — exact for regression safety.
    assert captured["cmd"][:2] == ["trivy", "image"]
    assert "--download-db-only" in captured["cmd"]
    assert "--quiet" in captured["cmd"]
    # Timeout forwarded.
    assert captured["timeout"] == 300
    # Env is the scrubbed env (no DATABASE_URL leakage).
    env = captured["env"] or {}
    assert "DATABASE_URL" not in env
    assert "SECRET_KEY" not in env
    # PATH must still be present so the subprocess can find sub-tools.
    assert "PATH" in env


# ---------------------------------------------------------------------------
# Failure modes — non-zero exit + timeout
# ---------------------------------------------------------------------------


def test_download_db_only_non_zero_exit_returns_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """returncode != 0 → status=failed, stderr_tail captured, error set."""
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr("integrations.trivy.shutil.which", lambda _name: "/usr/bin/trivy")

    def _fake_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=42,
            stdout=b"",
            stderr=b"FATAL: failed to pull image: unauthorized",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = download_db_only(timeout_seconds=60)
    assert result.status == "failed"
    assert result.error is not None
    assert "42" in result.error
    assert result.stderr_tail is not None
    assert "unauthorized" in result.stderr_tail


def test_download_db_only_long_stderr_is_truncated_to_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stderr_tail must cap at ~1000 chars so logs / notifications never balloon."""
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr("integrations.trivy.shutil.which", lambda _name: "/usr/bin/trivy")

    long_stderr = b"x" * 5000 + b"FATAL: end-of-stream-marker"

    def _fake_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout=b"", stderr=long_stderr
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = download_db_only(timeout_seconds=60)
    assert result.status == "failed"
    assert result.stderr_tail is not None
    # Tail is the LAST chunk so the trailing marker survives.
    assert "end-of-stream-marker" in result.stderr_tail
    assert len(result.stderr_tail) <= 1000


def test_download_db_only_timeout_returns_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TimeoutExpired → status=timeout with the timeout seconds in the error."""
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr("integrations.trivy.shutil.which", lambda _name: "/usr/bin/trivy")

    def _fake_run(cmd: list[str], **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(
            cmd=cmd, timeout=kwargs.get("timeout", 0), stderr=b"slow proxy stalled"
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = download_db_only(timeout_seconds=120)
    assert result.status == "timeout"
    assert result.error is not None
    assert "120" in result.error
    # stderr captured even on TimeoutExpired — Trivy may print before exit.
    assert result.stderr_tail is not None
    assert "stalled" in result.stderr_tail


def test_download_db_only_timeout_without_stderr_has_no_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TimeoutExpired without stderr → stderr_tail is None, not empty string."""
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr("integrations.trivy.shutil.which", lambda _name: "/usr/bin/trivy")

    def _fake_run(cmd: list[str], **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(
            cmd=cmd, timeout=kwargs.get("timeout", 0), stderr=None
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = download_db_only(timeout_seconds=60)
    assert result.status == "timeout"
    assert result.stderr_tail is None


def test_download_db_only_oserror_returns_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OSError (e.g. exec failure / EACCES on cache dir) → status=failed, no raise."""
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr("integrations.trivy.shutil.which", lambda _name: "/usr/bin/trivy")

    def _fake_run(*_a: Any, **_kw: Any) -> Any:
        raise PermissionError("EACCES: cache dir not writable")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = download_db_only(timeout_seconds=60)
    assert result.status == "failed"
    assert result.error is not None
    assert "EACCES" in result.error


# ---------------------------------------------------------------------------
# Concurrency — two parallel calls must both complete, neither crash
# ---------------------------------------------------------------------------


def test_download_db_only_safe_under_concurrent_invocations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two threads calling download_db_only must both return a result.

    Trivy's own file lock serialises real subprocess writes to ``cache_dir/db/``;
    this test exercises the Python wrapper's reentrancy. The fake subprocess
    is intentionally a no-op so the test stays deterministic — the file-lock
    serialisation lives inside the real ``trivy`` binary, not in this adapter.
    Documenting the contract here keeps an unintended module-level state
    addition (e.g. a global mutex) from regressing the worker pool.
    """
    import threading

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr("integrations.trivy.shutil.which", lambda _name: "/usr/bin/trivy")

    def _fake_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    results: list[TrivyDbDownloadResult] = []
    barrier = threading.Barrier(2)

    def _worker() -> None:
        barrier.wait(timeout=5)  # release both threads at the same instant
        results.append(download_db_only(timeout_seconds=60))

    threads = [threading.Thread(target=_worker, name=f"trivy-test-{i}") for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive(), "download_db_only blocked under concurrent invocation"

    assert len(results) == 2
    assert all(r.status == "downloaded" for r in results)
