"""
Unit tests for the pure data-shaping helpers in tasks/dt_resync.py.

Coverage focus — the upsert / fetch loop is exercised by integration tests
(those need a real DB and a mock DT client). The helpers below are pure
functions over DT JSON shapes, so we test them in isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest


def test_normalize_severity_lowercases_known_values() -> None:
    from tasks.dt_resync import _normalize_severity

    assert _normalize_severity("CRITICAL") == "critical"
    assert _normalize_severity("High") == "high"
    assert _normalize_severity("medium") == "medium"
    assert _normalize_severity("LOW") == "low"
    assert _normalize_severity("INFO") == "info"


@pytest.mark.parametrize(
    "value",
    [None, "", "moderate", 42, "weird"],
)
def test_normalize_severity_returns_unknown_for_anything_else(value: object) -> None:
    from tasks.dt_resync import _normalize_severity

    assert _normalize_severity(value) == "unknown"


def test_coerce_cvss_quantizes_to_one_decimal() -> None:
    from tasks.dt_resync import _coerce_cvss

    assert _coerce_cvss(7.456) == Decimal("7.5")
    assert _coerce_cvss("9.8") == Decimal("9.8")
    assert _coerce_cvss(0) == Decimal("0.0")


def test_coerce_cvss_handles_missing_or_invalid() -> None:
    from tasks.dt_resync import _coerce_cvss

    assert _coerce_cvss(None) is None
    assert _coerce_cvss("not-a-number") is None
    assert _coerce_cvss(float("inf")) is None  # ArithmeticError on Decimal


def test_parse_dt_timestamp_iso_with_z_suffix() -> None:
    from tasks.dt_resync import _parse_dt_timestamp

    parsed = _parse_dt_timestamp("2024-08-05T12:34:56Z")
    assert parsed == datetime(2024, 8, 5, 12, 34, 56, tzinfo=UTC)


def test_parse_dt_timestamp_iso_with_offset() -> None:
    from tasks.dt_resync import _parse_dt_timestamp

    parsed = _parse_dt_timestamp("2024-08-05T12:34:56+09:00")
    assert parsed is not None
    assert parsed.year == 2024


def test_parse_dt_timestamp_returns_none_for_garbage() -> None:
    from tasks.dt_resync import _parse_dt_timestamp

    assert _parse_dt_timestamp(None) is None
    assert _parse_dt_timestamp("") is None
    assert _parse_dt_timestamp("not-a-date") is None
    assert _parse_dt_timestamp(12345) is None


def test_dt_health_check_task_returns_outcome_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Celery wrapper must surface every field run_health_check produces."""
    import tasks.dt_health as dt_health_mod
    from integrations.dt.breaker import BreakerSnapshot
    from integrations.dt.health import HealthCheckOutcome

    fake_outcome = HealthCheckOutcome(
        healthy=True,
        snapshot_before=BreakerSnapshot(state="closed", fail_count=0, opened_at=None),
        snapshot_after=BreakerSnapshot(state="closed", fail_count=0, opened_at=None),
        auto_restart_attempted=False,
        error=None,
    )
    monkeypatch.setattr(dt_health_mod, "run_health_check", lambda: fake_outcome)

    result = dt_health_mod.dt_health_check_task()

    assert result == {
        "healthy": True,
        "state_before": "closed",
        "state_after": "closed",
        "fail_count": 0,
        "auto_restart_attempted": False,
        "error": None,
    }


def test_dt_orphan_cleaner_classify_marks_unknown_scans_as_orphans() -> None:
    """`_classify_page` is the heart of the orphan detector — pure over DT JSON."""
    import uuid as _uuid
    from typing import Any
    from unittest.mock import MagicMock

    from tasks.dt_orphan_cleaner import _classify_page

    known_scan = _uuid.uuid4()
    unknown_scan = _uuid.uuid4()

    # `_classify_page` expects `list[dict[str, Any]]` but tolerates malformed
    # entries (string scalars, missing keys) by skipping them. The annotation
    # below documents that intent and keeps mypy happy without a `cast`.
    page: list[Any] = [
        {"uuid": "dt-project-a", "version": str(known_scan), "name": "p-a"},
        {"uuid": "dt-project-b", "version": str(unknown_scan), "name": "p-b"},
        {"uuid": "dt-project-c", "version": "not-a-uuid", "name": "p-c"},
        "junk",  # not a dict — must be skipped
        {"uuid": None, "version": str(_uuid.uuid4())},  # missing uuid — skipped
    ]
    orphans: list[str] = []

    session = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = [known_scan]
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars
    session.execute.return_value = execute_result

    _classify_page(session, page=page, orphans=orphans)

    assert orphans == ["dt-project-b"]


def test_dt_orphan_cleaner_classify_noop_when_no_uuid_versions() -> None:
    from typing import Any
    from unittest.mock import MagicMock

    from tasks.dt_orphan_cleaner import _classify_page

    page: list[Any] = [{"uuid": "dt-x", "version": "branch-name"}]
    orphans: list[str] = []
    session = MagicMock()

    _classify_page(session, page=page, orphans=orphans)

    assert orphans == []
    session.execute.assert_not_called()
