"""
Unit tests for the feat/demo-sandbox-scan boot-time safe-limit guard
``validate_demo_sandbox_limits`` (security review finding).

M-1: the public-demo sandbox carve-out (``DEMO_ALLOW_SANDBOX_SCANS``) and the
knobs that keep it safe (input size, per-team concurrency, scancode off) are
decoupled, so turning the flag on WITHOUT the overlay would silently serve the
sandbox with production defaults. The guard fails the boot instead. These tests
pin: no-op when the flag is off, pass when every ceiling is met, and a
``RuntimeError`` for each individual violation (parametrized per CLAUDE.md §2
hardening rule 1 — the flag×knob intersection, not one axis), plus an app-boot
smoke proving the crash happens at lifespan startup before any DB work.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from core.config import validate_demo_sandbox_limits

# The safe baseline the docker-compose.demo.yml overlay applies.
_SAFE_ENV = {
    "DEMO_ALLOW_SANDBOX_SCANS": "true",
    "SCAN_SOURCE_RAW_DOWNLOAD_MAX_BYTES": "10485760",  # 10 MiB
    "SCAN_CONCURRENCY_CAP_PER_TEAM": "1",
    "SCANCODE_ENABLED": "false",
    "SBOM_INGEST_MAX_BYTES": "10485760",  # 10 MiB
    "SBOM_INGEST_MAX_COMPONENTS": "5000",
}

_OVERLAY_KEYS = tuple(k for k in _SAFE_ENV if k != "DEMO_ALLOW_SANDBOX_SCANS")


@pytest.fixture
def safe_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    for key, value in _SAFE_ENV.items():
        monkeypatch.setenv(key, value)
    yield monkeypatch


# --------------------------------------------------------------------------- #
# No-op / pass paths.
# --------------------------------------------------------------------------- #


def test_noop_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag off (production / plain read-only demo): the ceilings are irrelevant,
    so even production-default (over-ceiling) values pass untouched."""
    monkeypatch.delenv("DEMO_ALLOW_SANDBOX_SCANS", raising=False)
    for key in _OVERLAY_KEYS:
        monkeypatch.delenv(key, raising=False)  # production defaults
    validate_demo_sandbox_limits()  # must not raise


def test_passes_when_all_safe(safe_env: pytest.MonkeyPatch) -> None:
    validate_demo_sandbox_limits()  # must not raise


def test_passes_at_exact_ceilings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boundary: values exactly AT each ceiling are accepted (≤, not <)."""
    monkeypatch.setenv("DEMO_ALLOW_SANDBOX_SCANS", "true")
    monkeypatch.setenv("SCAN_SOURCE_RAW_DOWNLOAD_MAX_BYTES", "10485760")
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "2")
    monkeypatch.setenv("SCANCODE_ENABLED", "false")
    monkeypatch.setenv("SBOM_INGEST_MAX_BYTES", "10485760")
    monkeypatch.setenv("SBOM_INGEST_MAX_COMPONENTS", "5000")
    validate_demo_sandbox_limits()  # must not raise


# --------------------------------------------------------------------------- #
# Violations — flag on + one over-ceiling knob → RuntimeError.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        ("SCAN_SOURCE_RAW_DOWNLOAD_MAX_BYTES", "10485761"),  # 10 MiB + 1
        ("SCAN_CONCURRENCY_CAP_PER_TEAM", "3"),  # over ceiling
        ("SCAN_CONCURRENCY_CAP_PER_TEAM", "0"),  # unlimited sentinel — unsafe
        ("SCAN_CONCURRENCY_CAP_PER_TEAM", "-1"),  # unlimited sentinel — unsafe
        ("SCANCODE_ENABLED", "true"),  # scancode must be off
        ("SBOM_INGEST_MAX_BYTES", "10485761"),
        ("SBOM_INGEST_MAX_COMPONENTS", "5001"),
    ],
)
def test_each_violation_fails_boot(
    safe_env: pytest.MonkeyPatch, key: str, bad_value: str
) -> None:
    safe_env.setenv(key, bad_value)
    with pytest.raises(RuntimeError, match="safe limits are not applied"):
        validate_demo_sandbox_limits()


def test_production_defaults_fail_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag on but the overlay entirely absent (production defaults) → fail."""
    monkeypatch.setenv("DEMO_ALLOW_SANDBOX_SCANS", "true")
    for key in _OVERLAY_KEYS:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="safe limits are not applied"):
        validate_demo_sandbox_limits()


# --------------------------------------------------------------------------- #
# App-boot smoke — the crash happens at lifespan startup, before any DB work.
# --------------------------------------------------------------------------- #


async def test_app_boot_fails_when_sandbox_on_without_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entering the app lifespan with the carve-out on + production defaults
    raises at ``validate_demo_sandbox_limits()`` — which runs after
    ``secret_key()`` but BEFORE ``build_engine()`` — so no Postgres is needed."""
    monkeypatch.setenv("APP_ENV", "dev")  # secret_key() placeholder path
    monkeypatch.setenv("DEMO_ALLOW_SANDBOX_SCANS", "true")
    for key in _OVERLAY_KEYS:
        monkeypatch.delenv(key, raising=False)

    from main import app

    with pytest.raises(RuntimeError, match="safe limits are not applied"):
        async with app.router.lifespan_context(app):
            pass  # pragma: no cover — startup raises before the body runs
