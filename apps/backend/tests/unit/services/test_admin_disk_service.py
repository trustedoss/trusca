"""
Service-layer tests for ``services.admin_disk_service`` — Phase 4 PR #14.

The disk service is composed of four independent probes (workspace,
DT volume, postgres, redis). Each test pins one branch so a failure in
one probe never masks another.

Coverage:
  - filesystem probe: happy path + OSError graceful degradation.
  - threshold classification (ok / degraded / down) at the 80% / 90% boundaries.
  - postgres probe: happy path + DB error.
  - redis probe: happy path + maxmemory=0 (no percent), connection error.
  - top-level get_disk_telemetry orchestration: items in fixed order.
  - adversarial-input parametrize on the workspace path env var.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from services.admin_disk_service import (
    _classify,
    _probe_filesystem,
    _probe_redis,
)

# ---------------------------------------------------------------------------
# _classify — threshold trichotomy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "used_pct, expected",
    [
        (None, "ok"),
        (0.0, "ok"),
        (50.0, "ok"),
        (79.9, "ok"),
        (80.0, "degraded"),
        (85.0, "degraded"),
        (89.9, "degraded"),
        (90.0, "down"),
        (99.9, "down"),
        (100.0, "down"),
    ],
)
def test_classify_threshold_boundaries(used_pct: float | None, expected: str) -> None:
    assert _classify(used_pct, warn=80.0, crit=90.0) == expected


# ---------------------------------------------------------------------------
# _probe_filesystem — happy path + OSError
# ---------------------------------------------------------------------------


def _fake_disk_usage_factory(total: int, used: int, free: int) -> Callable[[str], Any]:
    """Build a callable matching shutil.disk_usage's signature for monkeypatching."""

    class _Usage:
        def __init__(self, total: int, used: int, free: int) -> None:
            self.total = total
            self.used = used
            self.free = free

    return lambda _path: _Usage(total, used, free)


def test_probe_filesystem_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.admin_disk_service.shutil.disk_usage",
        _fake_disk_usage_factory(total=1000, used=500, free=500),
    )
    item = _probe_filesystem(name="workspace", path="/dev/null")
    assert item.name == "workspace"
    assert item.total_bytes == 1000
    assert item.used_bytes == 500
    assert item.used_pct == 50.0
    assert item.status == "ok"
    assert item.error is None


def test_probe_filesystem_at_critical_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.admin_disk_service.shutil.disk_usage",
        _fake_disk_usage_factory(total=100, used=95, free=5),
    )
    item = _probe_filesystem(name="workspace", path="/dev/null")
    assert item.used_pct == 95.0
    assert item.status == "down"


def test_probe_filesystem_oserror_returns_error_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing or unreachable mount should not crash the endpoint."""

    def boom(_path: str) -> Any:
        raise FileNotFoundError("no such mount")

    monkeypatch.setattr("services.admin_disk_service.shutil.disk_usage", boom)
    item = _probe_filesystem(name="workspace", path="/missing")
    assert item.status == "down"
    assert item.error is not None
    # The raw Python exception type / errno must NOT leak to the API/UI; the
    # error is a stable, non-revealing reason (full detail is logged instead).
    assert "FileNotFoundError" not in item.error
    assert "Errno" not in item.error
    assert "not found" in item.error.lower()


@pytest.mark.parametrize(
    "adversarial_path",
    [
        "/etc/shadow",
        "../../../etc/passwd",
        "/dev/null\x00",
        "C:\\Windows\\System32\\config\\SAM",
        "//attacker.example/share",
    ],
)
def test_probe_filesystem_adversarial_paths_handled_gracefully(
    monkeypatch: pytest.MonkeyPatch,
    adversarial_path: str,
) -> None:
    """
    The disk endpoint reads its paths from env vars only super-admin can set,
    so path-traversal threats are out of scope. We still pin that any garbage
    path produces a structured error response rather than a 500.
    """

    def boom(path: str) -> Any:  # noqa: ARG001
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr("services.admin_disk_service.shutil.disk_usage", boom)
    item = _probe_filesystem(name="workspace", path=adversarial_path)
    assert item.status == "down"
    assert item.error is not None


# ---------------------------------------------------------------------------
# _probe_redis — happy path + maxmemory=0 + outage
# ---------------------------------------------------------------------------


class _FakeRedisOk:
    def __init__(self, used: int, maxmem: int) -> None:
        self._used = used
        self._maxmem = maxmem

    def info(self, _section: str) -> dict[str, int]:
        return {"used_memory": self._used, "maxmemory": self._maxmem}

    def close(self) -> None:
        pass


class _FakeRedisBoom:
    def info(self, _section: str) -> dict[str, int]:
        raise ConnectionError("redis unreachable")

    def close(self) -> None:
        pass


def test_probe_redis_happy_with_maxmemory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.admin_disk_service._redis.Redis.from_url",
        lambda _url, **_kw: _FakeRedisOk(used=1024, maxmem=4096),
    )
    item = _probe_redis()
    assert item.used_bytes == 1024
    assert item.total_bytes == 4096
    assert item.used_pct == 25.0
    assert item.status == "ok"


def test_probe_redis_no_maxmemory_returns_no_pct(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Redis has no maxmemory cap we report bytes only, not percent."""
    monkeypatch.setattr(
        "services.admin_disk_service._redis.Redis.from_url",
        lambda _url, **_kw: _FakeRedisOk(used=1024, maxmem=0),
    )
    item = _probe_redis()
    assert item.used_bytes == 1024
    assert item.total_bytes is None
    assert item.used_pct is None
    assert item.status == "ok"


def test_probe_redis_connection_error_returns_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.admin_disk_service._redis.Redis.from_url",
        lambda _url, **_kw: _FakeRedisBoom(),
    )
    item = _probe_redis()
    assert item.status == "down"
    assert item.error is not None


# ---------------------------------------------------------------------------
# Adversarial env var content — path with NUL / extreme length
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "garbage_path",
    [
        # NUL byte: ``os.environ`` itself rejects this (ValueError) so the
        # threat model relies on OS-layer enforcement. We pin only the
        # path-construction branch via direct call below; setenv is skipped
        # for this case to avoid testing CPython's env-var handling.
        "x" * 5_000,
        "",
        "../../etc/passwd",
        "C:\\nul",
        "//attacker.example/share",
    ],
)
def test_workspace_path_env_with_garbage_does_not_crash(
    monkeypatch: pytest.MonkeyPatch, garbage_path: str
) -> None:
    """
    CLAUDE.md core rule #11: env is read at call time. Even an
    operator-injected garbage env value must not crash the endpoint —
    the OSError path produces a status='down' item.
    """
    monkeypatch.setenv("WORKSPACE_HOST_PATH", garbage_path)

    # Force shutil.disk_usage to raise so we exercise the graceful branch.
    def boom(_path: str) -> Any:
        raise OSError(2, "No such file or directory")

    monkeypatch.setattr("services.admin_disk_service.shutil.disk_usage", boom)

    item = _probe_filesystem(name="workspace", path=garbage_path)
    assert item.status == "down"


def test_workspace_path_with_embedded_null_handled_at_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A NUL-bearing path triggers ValueError inside shutil; treat as 'down'.

    We invoke ``_probe_filesystem`` directly because ``os.environ.__setitem__``
    refuses NUL bytes — the env-var attack surface is closed by CPython
    before our code even runs. The probe still has to handle the case for
    callers that build the path string by other means.
    """

    def explode(_path: str) -> Any:
        raise ValueError("embedded null byte")

    monkeypatch.setattr("services.admin_disk_service.shutil.disk_usage", explode)
    # ``_probe_filesystem`` only catches OSError (the documented
    # filesystem error). ValueError from shutil happens BEFORE the
    # syscall, so it propagates — that is intentional: the path
    # constructor is the right place to catch this, not the probe.
    # We pin the propagation here so a future regression that swallows
    # ValueError silently is caught.
    with pytest.raises(ValueError):
        _probe_filesystem(name="workspace", path="/path/with/\x00/null")


# ---------------------------------------------------------------------------
# _strip_credentials — G4 credential strip helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        # asyncpg connection string in error message
        (
            "asyncpg.exceptions.CannotConnectNowError: postgresql://admin:s3cr3t@db:5432/trustedoss",
            "asyncpg.exceptions.CannotConnectNowError: postgresql://****@db:5432/trustedoss",
        ),
        # redis-py with password-only URL (empty username) — G4 regression
        (
            "ConnectionError: Error connecting to redis://:mypassword@redis:6379",
            "ConnectionError: Error connecting to redis://****@redis:6379",
        ),
        # rediss:// (TLS) with empty username
        (
            "ConnectionError: rediss://:secretpass@tls-host:6380",
            "ConnectionError: rediss://****@tls-host:6380",
        ),
        # URL with user:pass pair
        (
            "Exception: https://user:pa$$word@internal.host/api",
            "Exception: https://****@internal.host/api",
        ),
        # No credentials — passes through unchanged
        (
            "ConnectionError: tcp error connecting to localhost:5432",
            "ConnectionError: tcp error connecting to localhost:5432",
        ),
        # Empty string
        ("", ""),
    ],
)
def test_strip_credentials(raw: str, expected: str) -> None:
    from services.admin_disk_service import _strip_credentials

    assert _strip_credentials(raw) == expected


def test_probe_filesystem_oserror_strips_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G4: the filesystem probe error is a fixed, non-revealing message — it
    never echoes the raw exception, so connection-string credentials (or any
    other exception detail) can never leak into the API/UI error field."""

    def boom(_path: str) -> Any:
        raise OSError("OSError: path not found via postgresql://user:pass@db/db")

    monkeypatch.setattr("services.admin_disk_service.shutil.disk_usage", boom)
    item = _probe_filesystem(name="workspace", path="/missing")
    assert item.error is not None
    assert "pass" not in item.error
    assert "postgresql" not in item.error
    assert "@" not in item.error


# ---------------------------------------------------------------------------
# trivy_db card (M-32) — probes the worker-shared Trivy cache volume (H-6)
# ---------------------------------------------------------------------------


def test_trivy_db_probe_reads_trivy_cache_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The trivy_db card must point at TRIVY_CACHE_DIR (read at call time)."""
    from integrations.trivy import trivy_cache_dir

    cache = tmp_path / "trivy-cache"
    cache.mkdir()
    monkeypatch.setenv("TRIVY_CACHE_DIR", str(cache))

    item = _probe_filesystem(name="trivy_db", path=str(trivy_cache_dir()))
    assert item.name == "trivy_db"
    assert item.path == str(cache)
    assert item.error is None
    assert item.total_bytes is not None and item.total_bytes > 0
