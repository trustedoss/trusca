"""
Unit tests for ``core.config.vuln_sla_days`` (no DB) — X1 SLA/aging.

The per-severity SLA accessor reads ``os.getenv`` at call time (CLAUDE.md core
rule #11) and must degrade safely: a non-numeric or non-positive override falls
back to the severity's default so a fat-finger can neither disable (0) nor
invert (-N) the SLA clock. ``info`` / ``unknown`` (and any unrecognised value)
carry NO SLA and return ``None``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from core.config import vuln_sla_days

_ENV_VARS = (
    "VULN_SLA_DAYS_CRITICAL",
    "VULN_SLA_DAYS_HIGH",
    "VULN_SLA_DAYS_MEDIUM",
    "VULN_SLA_DAYS_LOW",
)

_DEFAULTS = {"critical": 7, "high": 30, "medium": 90, "low": 180}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.mark.parametrize(("severity", "expected"), sorted(_DEFAULTS.items()))
def test_defaults_per_severity(severity: str, expected: int) -> None:
    assert vuln_sla_days(severity) == expected


@pytest.mark.parametrize("severity", ["info", "unknown", "", "bogus", "CRITICAL!"])
def test_no_sla_severities_return_none(severity: str) -> None:
    assert vuln_sla_days(severity) is None


def test_severity_is_case_and_whitespace_insensitive() -> None:
    assert vuln_sla_days("CRITICAL") == 7
    assert vuln_sla_days("  High  ") == 30


@pytest.mark.parametrize(
    ("severity", "env", "value", "expected"),
    [
        ("critical", "VULN_SLA_DAYS_CRITICAL", "3", 3),
        ("high", "VULN_SLA_DAYS_HIGH", "14", 14),
        ("medium", "VULN_SLA_DAYS_MEDIUM", "45", 45),
        ("low", "VULN_SLA_DAYS_LOW", "365", 365),
    ],
)
def test_env_override_is_used(
    severity: str,
    env: str,
    value: str,
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(env, value)
    assert vuln_sla_days(severity) == expected


def test_override_of_one_severity_leaves_others_at_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VULN_SLA_DAYS_CRITICAL", "3")
    assert vuln_sla_days("critical") == 3
    assert vuln_sla_days("high") == 30
    assert vuln_sla_days("medium") == 90
    assert vuln_sla_days("low") == 180


@pytest.mark.parametrize("bad", ["", "not-a-number", "0", "-1", "3.5", "7 days"])
@pytest.mark.parametrize("severity", sorted(_DEFAULTS))
def test_invalid_or_nonpositive_falls_back_to_default(
    severity: str, bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(f"VULN_SLA_DAYS_{severity.upper()}", bad)
    assert vuln_sla_days(severity) == _DEFAULTS[severity]


def test_none_input_returns_none() -> None:
    # Defensive: a caller passing a NULL-ish severity must not crash.
    assert vuln_sla_days(None) is None  # type: ignore[arg-type]
