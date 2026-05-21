"""
Unit tests for B1 — concurrency / stability backend (no DB required).

Covers the pure / config-level pieces:

  - DB pool config accessors read os.getenv at call time and clamp junk /
    out-of-range values safely (no crash on a typo).
  - The async + sync engine builders apply the configured pool sizing to the
    SQLAlchemy engine (pool_size / max_overflow / timeout / recycle).
  - The per-user slowapi key function derives ``user:<sub>`` from a valid
    access token and falls back to ``ip:<addr>`` otherwise.
  - The scan-trigger rate-limit + per-team concurrency-cap config accessors.

DB-backed enforcement (cap counting, boundary, race) lives in
``test_scan_service.py`` so it runs against the real Postgres service.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _clean_env() -> Iterator[None]:
    """Snapshot + restore the env vars these tests mutate.

    Every accessor reads os.getenv at call time (CLAUDE.md core rule #11), so
    we can set/unset freely as long as we restore afterwards.
    """
    keys = [
        "DB_POOL_SIZE",
        "DB_MAX_OVERFLOW",
        "DB_POOL_TIMEOUT",
        "DB_POOL_RECYCLE",
        "DB_SYNC_POOL_SIZE",
        "DB_SYNC_MAX_OVERFLOW",
        "DB_SYNC_POOL_TIMEOUT",
        "DB_SYNC_POOL_RECYCLE",
        "SCAN_TRIGGER_RATE_LIMIT",
        "SCAN_CONCURRENCY_CAP_PER_TEAM",
    ]
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# DB pool config accessors — defaults + runtime override + safe clamping
# ---------------------------------------------------------------------------


def test_db_pool_defaults_are_raised_above_sqlalchemy_baseline() -> None:
    """B1 raises the ceiling well above SQLAlchemy's 5 + 10 default."""
    from core.config import (
        db_max_overflow,
        db_pool_recycle_seconds,
        db_pool_size,
        db_pool_timeout_seconds,
    )

    assert db_pool_size() == 20
    assert db_max_overflow() == 10
    assert db_pool_timeout_seconds() == 30
    assert db_pool_recycle_seconds() == 1800


def test_db_sync_pool_defaults_are_smaller_than_async() -> None:
    """Celery worker concurrency is low, so the sync pool stays small."""
    from core.config import (
        db_pool_size,
        db_sync_max_overflow,
        db_sync_pool_recycle_seconds,
        db_sync_pool_size,
        db_sync_pool_timeout_seconds,
    )

    assert db_sync_pool_size() == 5
    assert db_sync_max_overflow() == 5
    assert db_sync_pool_timeout_seconds() == 30
    assert db_sync_pool_recycle_seconds() == 1800
    # The async pool is the busier one — it must be at least as large.
    assert db_pool_size() >= db_sync_pool_size()


def test_db_pool_size_reads_env_at_call_time() -> None:
    from core.config import db_max_overflow, db_pool_size

    os.environ["DB_POOL_SIZE"] = "50"
    os.environ["DB_MAX_OVERFLOW"] = "25"
    assert db_pool_size() == 50
    assert db_max_overflow() == 25


def test_db_pool_size_clamps_zero_and_negative_to_minimum() -> None:
    """A zero/negative pool_size would deadlock the engine — clamp to 1."""
    from core.config import db_pool_size

    os.environ["DB_POOL_SIZE"] = "0"
    assert db_pool_size() == 1
    os.environ["DB_POOL_SIZE"] = "-5"
    assert db_pool_size() == 1


def test_db_max_overflow_allows_zero_but_clamps_negative() -> None:
    """max_overflow=0 is a valid hard cap; negative is nonsense → 0."""
    from core.config import db_max_overflow

    os.environ["DB_MAX_OVERFLOW"] = "0"
    assert db_max_overflow() == 0
    os.environ["DB_MAX_OVERFLOW"] = "-3"
    assert db_max_overflow() == 0


def test_db_pool_size_falls_back_to_default_on_junk() -> None:
    """A non-numeric typo must not crash engine construction."""
    from core.config import db_pool_size

    os.environ["DB_POOL_SIZE"] = "not-a-number"
    assert db_pool_size() == 20
    os.environ["DB_POOL_SIZE"] = ""
    assert db_pool_size() == 20


def test_db_pool_recycle_allows_minus_one_disable() -> None:
    """-1 disables recycling and must survive the clamp."""
    from core.config import db_pool_recycle_seconds

    os.environ["DB_POOL_RECYCLE"] = "-1"
    assert db_pool_recycle_seconds() == -1


# ---------------------------------------------------------------------------
# L2 — upper-bound clamps. A fat-finger like DB_POOL_SIZE=100000 must not let
# one process try to exhaust Postgres' max_connections (self-DoS).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_var", "accessor_name", "ceiling"),
    [
        ("DB_POOL_SIZE", "db_pool_size", 200),
        ("DB_MAX_OVERFLOW", "db_max_overflow", 200),
        ("DB_POOL_TIMEOUT", "db_pool_timeout_seconds", 3600),
        ("DB_POOL_RECYCLE", "db_pool_recycle_seconds", 86_400),
        ("DB_SYNC_POOL_SIZE", "db_sync_pool_size", 200),
        ("DB_SYNC_MAX_OVERFLOW", "db_sync_max_overflow", 200),
        ("DB_SYNC_POOL_TIMEOUT", "db_sync_pool_timeout_seconds", 3600),
        ("DB_SYNC_POOL_RECYCLE", "db_sync_pool_recycle_seconds", 86_400),
    ],
)
def test_pool_knob_clamps_absurd_value_to_ceiling(
    env_var: str, accessor_name: str, ceiling: int
) -> None:
    """A grossly oversized value is clamped down to the documented ceiling."""
    import core.config as config

    accessor = getattr(config, accessor_name)
    os.environ[env_var] = "100000"
    assert accessor() == ceiling


@pytest.mark.parametrize(
    ("env_var", "accessor_name", "ceiling"),
    [
        ("DB_POOL_SIZE", "db_pool_size", 200),
        ("DB_POOL_TIMEOUT", "db_pool_timeout_seconds", 3600),
        ("DB_SYNC_POOL_RECYCLE", "db_sync_pool_recycle_seconds", 86_400),
    ],
)
def test_pool_knob_at_ceiling_is_unchanged(
    env_var: str, accessor_name: str, ceiling: int
) -> None:
    """Exactly the ceiling is allowed through unchanged (boundary)."""
    import core.config as config

    accessor = getattr(config, accessor_name)
    os.environ[env_var] = str(ceiling)
    assert accessor() == ceiling


def test_pool_knob_just_below_ceiling_is_unchanged() -> None:
    """A legitimate large-but-sane value is not clamped."""
    from core.config import db_pool_size

    os.environ["DB_POOL_SIZE"] = "199"
    assert db_pool_size() == 199


def test_recycle_minus_one_disable_survives_upper_clamp() -> None:
    """The -1 disable sentinel is below the ceiling and must pass through."""
    from core.config import db_pool_recycle_seconds, db_sync_pool_recycle_seconds

    os.environ["DB_POOL_RECYCLE"] = "-1"
    os.environ["DB_SYNC_POOL_RECYCLE"] = "-1"
    assert db_pool_recycle_seconds() == -1
    assert db_sync_pool_recycle_seconds() == -1


def test_int_env_logs_warning_when_clamping_to_max() -> None:
    """L2: an over-ceiling value emits a WARNING so the operator notices."""
    from unittest.mock import MagicMock, patch

    from core.config import _int_env

    os.environ["DB_POOL_SIZE"] = "999999"
    fake_logger = MagicMock()
    with patch("structlog.get_logger", return_value=fake_logger):
        result = _int_env("DB_POOL_SIZE", 20, minimum=1, maximum=200)
    assert result == 200
    fake_logger.warning.assert_called_once()
    _, kwargs = fake_logger.warning.call_args
    assert kwargs["env_var"] == "DB_POOL_SIZE"
    assert kwargs["clamped_to"] == 200


# ---------------------------------------------------------------------------
# Engine builders apply the configured pool sizing
# ---------------------------------------------------------------------------


def test_build_engine_applies_pool_settings() -> None:
    """The async engine's QueuePool reflects the env-configured sizing."""
    from core.db import build_engine

    os.environ["DB_POOL_SIZE"] = "17"
    os.environ["DB_MAX_OVERFLOW"] = "9"
    os.environ["DB_POOL_TIMEOUT"] = "25"
    os.environ["DB_POOL_RECYCLE"] = "900"

    engine = build_engine()
    try:
        pool = engine.pool
        # AsyncEngine wraps a sync Engine; pool.size() is the configured
        # pool_size, _max_overflow / _timeout / _recycle the rest.
        assert pool.size() == 17
        assert pool._max_overflow == 9  # type: ignore[attr-defined]
        assert pool._timeout == 25  # type: ignore[attr-defined]
        assert pool._recycle == 900  # type: ignore[attr-defined]
    finally:
        # sync_engine is the underlying Engine; dispose without an event loop.
        engine.sync_engine.dispose()


def test_build_sync_engine_applies_sync_pool_settings() -> None:
    """The Celery sync engine reflects the DB_SYNC_* sizing."""
    from core.db import build_sync_engine

    os.environ["DB_SYNC_POOL_SIZE"] = "4"
    os.environ["DB_SYNC_MAX_OVERFLOW"] = "2"
    os.environ["DB_SYNC_POOL_TIMEOUT"] = "15"
    os.environ["DB_SYNC_POOL_RECYCLE"] = "600"

    engine = build_sync_engine()
    try:
        pool = engine.pool
        assert pool.size() == 4
        assert pool._max_overflow == 2  # type: ignore[attr-defined]
        assert pool._timeout == 15  # type: ignore[attr-defined]
        assert pool._recycle == 600  # type: ignore[attr-defined]
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Scan-trigger abuse-control config
# ---------------------------------------------------------------------------


def test_scan_trigger_rate_limit_default_and_override() -> None:
    from core.config import scan_trigger_rate_limit

    assert scan_trigger_rate_limit() == "20/minute"
    os.environ["SCAN_TRIGGER_RATE_LIMIT"] = "5/minute"
    assert scan_trigger_rate_limit() == "5/minute"


def test_scan_concurrency_cap_default_and_override() -> None:
    from core.config import scan_concurrency_cap_per_team

    assert scan_concurrency_cap_per_team() == 10
    os.environ["SCAN_CONCURRENCY_CAP_PER_TEAM"] = "3"
    assert scan_concurrency_cap_per_team() == 3


def test_scan_concurrency_cap_zero_and_negative_are_safe() -> None:
    """0 disables the cap; a negative typo clamps to 0 (also disabled)."""
    from core.config import scan_concurrency_cap_per_team

    os.environ["SCAN_CONCURRENCY_CAP_PER_TEAM"] = "0"
    assert scan_concurrency_cap_per_team() == 0
    os.environ["SCAN_CONCURRENCY_CAP_PER_TEAM"] = "-7"
    assert scan_concurrency_cap_per_team() == 0


def test_scan_concurrency_cap_junk_falls_back_to_default() -> None:
    from core.config import scan_concurrency_cap_per_team

    os.environ["SCAN_CONCURRENCY_CAP_PER_TEAM"] = "lots"
    assert scan_concurrency_cap_per_team() == 10


# ---------------------------------------------------------------------------
# Per-user rate-limit key function
# ---------------------------------------------------------------------------


def test_user_key_uses_token_sub_when_present() -> None:
    """A valid access token yields a per-user bucket (`user:<sub>`)."""
    from unittest.mock import MagicMock

    from core.ratelimit import _authenticated_user_key
    from core.security import create_access_token

    user_id = "11111111-1111-1111-1111-111111111111"
    token = create_access_token(subject=user_id)

    req = MagicMock()
    req.headers = {"authorization": f"Bearer {token}"}
    assert _authenticated_user_key(req) == f"user:{user_id}"


def test_user_key_falls_back_to_ip_without_token() -> None:
    """No Authorization header → IP bucket so anon floods don't share keys."""
    from unittest.mock import MagicMock

    from core.ratelimit import _authenticated_user_key

    req = MagicMock()
    req.headers = {}
    req.client.host = "198.51.100.9"
    assert _authenticated_user_key(req) == "ip:198.51.100.9"


def test_user_key_falls_back_to_ip_on_garbage_token() -> None:
    """An unverifiable bearer token must not key by attacker-chosen value."""
    from unittest.mock import MagicMock

    from core.ratelimit import _authenticated_user_key

    req = MagicMock()
    req.headers = {"authorization": "Bearer not.a.real.jwt"}
    req.client.host = "203.0.113.55"
    assert _authenticated_user_key(req) == "ip:203.0.113.55"


# ---------------------------------------------------------------------------
# RFC 7807 envelope for the concurrency-cap exception (no DB)
# ---------------------------------------------------------------------------


def test_concurrent_scan_limit_problem_envelope_has_limit_and_retry_after() -> None:
    """The 429 cap response carries type URI, `limit`, and Retry-After."""
    import json
    from unittest.mock import MagicMock

    from api.v1.projects import _problem_for_scan_error
    from services.scan_service import ConcurrentScanLimitExceeded

    request = MagicMock()
    request.url.path = "/v1/projects/abc/scans"

    exc = ConcurrentScanLimitExceeded(
        "team has too many scans", running_scans=10, limit=10
    )
    response = _problem_for_scan_error(request, exc)

    assert response.status_code == 429
    assert response.media_type == "application/problem+json"
    assert response.headers["Retry-After"] == "30"

    body = json.loads(bytes(response.body))
    assert body["type"] == "urn:trustedoss:problem:concurrent_scan_limit"
    assert body["status"] == 429
    assert body["title"] == "Concurrent Scan Limit Exceeded"
    assert body["limit"] == 10
    assert body["instance"] == "/v1/projects/abc/scans"


def test_concurrent_scan_limit_problem_body_omits_running_scans() -> None:
    """M1: the live per-team active-scan count must NOT leak into the body.

    `running_scans` is server-side log context only — exposing it to every
    team developer on each 429 is an intra-team side-channel. The exception
    still carries the value for logging, but the response body never serializes
    it.
    """
    import json
    from unittest.mock import MagicMock

    from api.v1.projects import _problem_for_scan_error
    from services.scan_service import ConcurrentScanLimitExceeded

    request = MagicMock()
    request.url.path = "/v1/projects/abc/scans"

    exc = ConcurrentScanLimitExceeded(
        "team has too many scans", running_scans=42, limit=10
    )
    # The value is retained on the instance for the server-side log.warning.
    assert exc.running_scans == 42

    response = _problem_for_scan_error(request, exc)
    body = json.loads(bytes(response.body))
    assert "running_scans" not in body
    # No other key carries the count either (defense against a renamed leak).
    assert 42 not in body.values()
