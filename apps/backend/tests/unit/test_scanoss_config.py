"""
Unit tests for the SCANOSS config accessors (no DB) — Phase J / P3-11.

These four accessors read ``os.getenv`` at call time (CLAUDE.md core rule #11).
The one that matters most for safety is ``scanoss_enabled``: because the feature
sends fingerprints to an external API, it MUST default to OFF and fail closed on
any non-truthy value, so a fat-finger cannot silently open an egress channel.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from core.config import (
    scanoss_api_key,
    scanoss_api_url,
    scanoss_enabled,
    scanoss_timeout_seconds,
)

_ENV_KEYS = (
    "SCANOSS_ENABLED",
    "SCANOSS_API_URL",
    "SCANOSS_API_KEY",
    "SCANOSS_TIMEOUT_SECONDS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


# --- scanoss_enabled --------------------------------------------------------


def test_enabled_defaults_off() -> None:
    assert scanoss_enabled() is False


@pytest.mark.parametrize("value", ["true", "TRUE", "True", "1", "yes", "YES"])
def test_enabled_truthy_tokens(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCANOSS_ENABLED", value)
    assert scanoss_enabled() is True


@pytest.mark.parametrize(
    "value", ["false", "0", "no", "off", "on", "enabled", "", "  ", "y", "t"]
)
def test_enabled_fails_closed_on_non_truthy(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCANOSS_ENABLED", value)
    assert scanoss_enabled() is False


# --- scanoss_api_url --------------------------------------------------------


def test_api_url_default() -> None:
    assert scanoss_api_url() == "https://api.osskb.org"


def test_api_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCANOSS_API_URL", "https://scanoss.internal.example/api")
    assert scanoss_api_url() == "https://scanoss.internal.example/api"


# --- scanoss_api_key --------------------------------------------------------


def test_api_key_default_empty() -> None:
    assert scanoss_api_key() == ""


def test_api_key_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCANOSS_API_KEY", "sk-abc")
    assert scanoss_api_key() == "sk-abc"


# --- scanoss_timeout_seconds ------------------------------------------------


def test_timeout_default() -> None:
    assert scanoss_timeout_seconds() == 300


def test_timeout_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCANOSS_TIMEOUT_SECONDS", "120")
    assert scanoss_timeout_seconds() == 120


@pytest.mark.parametrize("bad", ["", "  ", "not-a-number", "0", "-5", "3.5"])
def test_timeout_falls_back_on_bad_value(
    bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCANOSS_TIMEOUT_SECONDS", bad)
    assert scanoss_timeout_seconds() == 300
