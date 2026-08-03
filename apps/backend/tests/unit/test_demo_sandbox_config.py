"""
Unit tests for the feat/demo-sandbox-scan config accessor
``demo_allow_sandbox_scans`` (no DB).

This is a PUBLIC-EXPOSED security boundary: the flag opts the read-only demo into
accepting the two bounded sandbox write paths. Because turning it on widens the
public write surface, it MUST default OFF and fail CLOSED on any non-truthy value
so a fat-finger cannot silently open the carve-out. Read at call time
(CLAUDE.md core rule #11).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from core.config import demo_allow_sandbox_scans


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("DEMO_ALLOW_SANDBOX_SCANS", raising=False)
    yield


def test_defaults_off() -> None:
    assert demo_allow_sandbox_scans() is False


@pytest.mark.parametrize("value", ["true", "TRUE", "True", "1", "yes", "YES", " yes "])
def test_truthy_tokens_enable(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_ALLOW_SANDBOX_SCANS", value)
    assert demo_allow_sandbox_scans() is True


@pytest.mark.parametrize(
    "value",
    ["false", "0", "no", "off", "on", "enabled", "", "  ", "y", "t", "disable"],
)
def test_fails_closed_on_non_truthy(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEMO_ALLOW_SANDBOX_SCANS", value)
    assert demo_allow_sandbox_scans() is False
