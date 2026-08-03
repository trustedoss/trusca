"""
Unit tests for ``core.config.dependency_graph_max_nodes`` (no DB) — BomLens H-1.

The node-cap accessor reads ``os.getenv`` at call time (CLAUDE.md core rule #11)
and must degrade safely: a non-numeric or non-positive value falls back to the
5000 default so a fat-finger can neither disable the guard nor ship an empty
graph for every scan.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from core.config import dependency_graph_max_nodes


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("DEPENDENCY_GRAPH_MAX_NODES", raising=False)
    yield


def test_default_is_5000() -> None:
    assert dependency_graph_max_nodes() == 5000


def test_valid_value_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPENDENCY_GRAPH_MAX_NODES", "250")
    assert dependency_graph_max_nodes() == 250


@pytest.mark.parametrize("bad", ["", "not-a-number", "0", "-1", "3.5"])
def test_invalid_or_nonpositive_falls_back_to_5000(
    bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEPENDENCY_GRAPH_MAX_NODES", bad)
    assert dependency_graph_max_nodes() == 5000
