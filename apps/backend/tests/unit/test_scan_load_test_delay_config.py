"""
Unit tests for ``scan_load_test_delay_seconds`` (M1, concurrency-scaling plan).

The accessor exists so a load test can hold a worker slot busy for a fixed
number of seconds instead of running the real cdxgen/Trivy chain. The
``mock`` backend finishes almost instantly, so it never builds a queue. The
safety property under test is the one that matters most: the delay is inert
unless BOTH the enable flag is set AND ``APP_ENV=dev``, so a load-test toggle
left on cannot silently fake scan results outside a dev box.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from core.config import scan_load_test_delay_seconds


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("SCAN_LOAD_TEST_DELAY_ENABLED", raising=False)
    monkeypatch.delenv("SCAN_LOAD_TEST_DELAY_SECONDS", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    yield


def test_defaults_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset ⇒ 0.0, even though APP_ENV also defaults to 'dev' (principle 5:
    new toggles start off regardless of environment)."""
    assert scan_load_test_delay_seconds() == 0.0


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off", "", "  ", "junk"])
def test_non_truthy_enable_flag_stays_disabled(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_ENABLED", value)
    monkeypatch.setenv("APP_ENV", "dev")
    assert scan_load_test_delay_seconds() == 0.0


@pytest.mark.parametrize("app_env", ["staging", "prod", "production", "PROD", ""])
def test_refused_outside_dev_even_when_enabled(
    app_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_ENABLED", "true")
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_SECONDS", "3")
    if app_env:
        monkeypatch.setenv("APP_ENV", app_env)
    else:
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.setenv("APP_ENV", "prod")
    assert scan_load_test_delay_seconds() == 0.0


def test_enabled_in_dev_returns_configured_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_SECONDS", "12.5")
    assert scan_load_test_delay_seconds() == 12.5


def test_enabled_in_dev_without_explicit_seconds_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")
    assert scan_load_test_delay_seconds() == 5.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", 0.1),  # below minimum -> clamped up
        ("-5", 0.1),
        ("999999", 3600.0),  # above maximum -> clamped down
        ("not-a-number", 5.0),  # junk -> falls back to default
    ],
)
def test_seconds_clamped_or_defaulted_when_out_of_range(
    raw: str, expected: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_SECONDS", raw)
    assert scan_load_test_delay_seconds() == expected


def test_accepted_truthy_spellings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_SECONDS", "1")
    for spelling in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_ENABLED", spelling)
        assert scan_load_test_delay_seconds() == 1.0
