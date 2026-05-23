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


# ---------------------------------------------------------------------------
# _coerce_epss — EPSS is a probability on [0, 1]; out-of-range / non-numeric
# input is untrusted DT output and must coerce to None (not clamp). Adversarial
# parametrize is mandatory for untrusted-input parsing (MEMORY: adversarial
# input parametrize).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # --- valid in-range probabilities (quantized to 5 dp) ---
        (0.97123, Decimal("0.97123")),
        ("0.5", Decimal("0.50000")),
        (1.0, Decimal("1.00000")),
        (0.0, Decimal("0.00000")),
        (1, Decimal("1.00000")),
        (0, Decimal("0.00000")),
        (Decimal("0.00042"), Decimal("0.00042")),
        # very small value below the column scale rounds to 0, still valid
        (0.0000001, Decimal("0.00000")),
        ("0.000004", Decimal("0.00000")),
        # boundary string forms
        ("1", Decimal("1.00000")),
        ("1.000000", Decimal("1.00000")),
    ],
)
def test_coerce_epss_accepts_in_range_probabilities(value: object, expected: Decimal) -> None:
    from tasks.dt_resync import _coerce_epss

    result = _coerce_epss(value)
    assert result == expected
    # Quantized to the Numeric(6, 5) scale.
    assert result is not None
    assert result.as_tuple().exponent == -5


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "abc",
        "not-a-number",
        "0.5.5",  # malformed numeric string
        [],
        {},
        object(),
        # --- out of range: probabilities cannot exceed 1 or go negative ---
        1.0001,
        2,
        100,
        "1.5",
        -0.0001,
        -1,
        "-0.5",
        # --- non-finite: NaN / Inf are not valid probabilities ---
        float("nan"),
        float("inf"),
        float("-inf"),
        "nan",
        "inf",
        "-inf",
        "Infinity",
        # --- booleans must NOT coerce to 1/0 even though they are int-like ---
        True,
        False,
    ],
)
def test_coerce_epss_drops_invalid_or_out_of_range_to_none(value: object) -> None:
    from tasks.dt_resync import _coerce_epss

    assert _coerce_epss(value) is None


def test_coerce_epss_does_not_clamp() -> None:
    """An out-of-range value must become None, never a clamped 0/1.

    Clamping would silently fabricate a "valid-looking" score from garbage DT
    output, which is worse than admitting we have no EPSS for the row.
    """
    from tasks.dt_resync import _coerce_epss

    assert _coerce_epss(5.0) is None  # not Decimal("1.00000")
    assert _coerce_epss(-3.0) is None  # not Decimal("0.00000")


def test_upsert_vulnerability_maps_dt_epss_fields_on_insert() -> None:
    """DT catalog raw → Vulnerability: epssScore/epssPercentile map onto the
    new columns alongside cvss_score (insert path)."""
    from unittest.mock import MagicMock

    from tasks.dt_resync import _upsert_vulnerability

    session = MagicMock()
    # No existing row → insert path.
    session.execute.return_value.scalar_one_or_none.return_value = None

    raw = {
        "vulnId": "CVE-2099-0001",
        "source": "NVD",
        "severity": "CRITICAL",
        "cvssV3BaseScore": 9.8,
        # DT 4.x exposes EPSS at top level of the /api/v1/vulnerability item.
        "epssScore": 0.97123,
        "epssPercentile": 0.99001,
    }

    wrote = _upsert_vulnerability(session, raw)

    assert wrote is True
    # The model instance handed to session.add captures the mapping.
    assert session.add.call_count == 1
    added = session.add.call_args.args[0]
    assert added.external_id == "CVE-2099-0001"
    assert added.cvss_score == Decimal("9.8")
    assert added.epss_score == Decimal("0.97123")
    assert added.epss_percentile == Decimal("0.99001")


def test_upsert_vulnerability_maps_dt_epss_fields_on_update() -> None:
    """Update path mirrors the insert path: EPSS columns refresh in place."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from tasks.dt_resync import _upsert_vulnerability

    existing = SimpleNamespace(
        external_id="CVE-2099-0002",
        severity="low",
        cvss_score=None,
        epss_score=None,
        epss_percentile=None,
        cvss_vector=None,
        summary=None,
        details=None,
        published_at=None,
        modified_at=None,
        references=[],
        last_seen_at=None,
    )
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = existing

    raw = {
        "vulnId": "CVE-2099-0002",
        "source": "NVD",
        "severity": "HIGH",
        "cvssV3BaseScore": 8.1,
        "epssScore": 0.30000,
        "epssPercentile": 0.65000,
    }

    wrote = _upsert_vulnerability(session, raw)

    assert wrote is True
    session.add.assert_not_called()  # update path mutates in place
    assert existing.epss_score == Decimal("0.30000")
    assert existing.epss_percentile == Decimal("0.65000")
    assert existing.cvss_score == Decimal("8.1")


def test_upsert_vulnerability_drops_out_of_range_epss_to_none() -> None:
    """Adversarial DT output (EPSS > 1) must land as NULL, not a clamped 1.0."""
    from unittest.mock import MagicMock

    from tasks.dt_resync import _upsert_vulnerability

    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None

    raw = {
        "vulnId": "CVE-2099-0003",
        "source": "NVD",
        "severity": "medium",
        "epssScore": 4.2,  # impossible probability
        "epssPercentile": "garbage",
    }

    assert _upsert_vulnerability(session, raw) is True
    added = session.add.call_args.args[0]
    assert added.epss_score is None
    assert added.epss_percentile is None


def test_upsert_vulnerability_epss_with_dict_source_shape() -> None:
    """DT 4.12 emits `source` as a dict; EPSS mapping is independent of that.

    Also exercises the dict-`source` branch so the EPSS additions are covered
    on both DT payload shapes.
    """
    from unittest.mock import MagicMock

    from tasks.dt_resync import _upsert_vulnerability

    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None

    raw = {
        "vulnId": "CVE-2099-0005",
        "source": {"name": "OSV"},
        "severity": "high",
        "epssScore": "0.42000",
        "epssPercentile": "0.80000",
    }

    assert _upsert_vulnerability(session, raw) is True
    added = session.add.call_args.args[0]
    assert added.source == "OSV"
    assert added.epss_score == Decimal("0.42000")
    assert added.epss_percentile == Decimal("0.80000")


def test_upsert_vulnerability_absent_epss_keys_are_none() -> None:
    """A finding with no EPSS keys (older DT) leaves both columns NULL."""
    from unittest.mock import MagicMock

    from tasks.dt_resync import _upsert_vulnerability

    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None

    raw = {"vulnId": "CVE-2099-0004", "source": "NVD", "severity": "low"}

    assert _upsert_vulnerability(session, raw) is True
    added = session.add.call_args.args[0]
    assert added.epss_score is None
    assert added.epss_percentile is None


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
