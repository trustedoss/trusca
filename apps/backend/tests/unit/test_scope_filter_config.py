"""
Unit tests for the runtime-scope filter config accessors (no DB) — Phase K.

All three accessors read ``os.getenv`` at call time (CLAUDE.md core rule #11)
and default ON. The parsing is deliberately the INVERSE of ``scanoss_enabled``:
only the exact falsy tokens ``false`` / ``0`` / ``no`` disable, so a typo
fails OPEN to the correct-by-default filtering behaviour. SCANOSS fails closed
because its failure direction is "unexpected egress"; here the failure
direction is "silently reverting to over-counted CVEs", so open-to-correct is
the safe side.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from core.config import (
    scan_scope_filter_enabled,
    scan_scope_filter_maven_enabled,
    scan_scope_filter_node_enabled,
)

_ENV_KEYS = (
    "SCAN_SCOPE_FILTER_ENABLED",
    "SCAN_SCOPE_FILTER_MAVEN_ENABLED",
    "SCAN_SCOPE_FILTER_NODE_ENABLED",
)

_ACCESSOR_BY_KEY = {
    "SCAN_SCOPE_FILTER_ENABLED": scan_scope_filter_enabled,
    "SCAN_SCOPE_FILTER_MAVEN_ENABLED": scan_scope_filter_maven_enabled,
    "SCAN_SCOPE_FILTER_NODE_ENABLED": scan_scope_filter_node_enabled,
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.mark.parametrize("key", _ENV_KEYS)
def test_defaults_on(key: str) -> None:
    assert _ACCESSOR_BY_KEY[key]() is True


@pytest.mark.parametrize("key", _ENV_KEYS)
@pytest.mark.parametrize("value", ["false", "FALSE", "False", "0", "no", " no "])
def test_exact_falsy_tokens_disable(
    key: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(key, value)
    assert _ACCESSOR_BY_KEY[key]() is False


@pytest.mark.parametrize("key", _ENV_KEYS)
@pytest.mark.parametrize("value", ["true", "1", "yes", "", "  ", "off", "disable", "n"])
def test_anything_else_stays_on(
    key: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fail-open-to-correct: typos ("off", "disable", "n") keep the filter ON.
    monkeypatch.setenv(key, value)
    assert _ACCESSOR_BY_KEY[key]() is True
