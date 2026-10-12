"""``SCAN_ENGINE`` (preview): read at call time, unknown values fall back safely."""

from __future__ import annotations

import pytest

from core.config import (
    SCAN_ENGINE_VALUES,
    bomlens_engine_in_use,
    scan_container_engine,
    scan_engine,
)


def test_the_default_is_the_in_process_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCAN_ENGINE", raising=False)
    assert scan_engine() == "inprocess"


@pytest.mark.parametrize(("raw", "expected"), [("bomlens", "bomlens"), (" BomLens ", "bomlens")])
def test_bomlens_is_selected_by_name(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    monkeypatch.setenv("SCAN_ENGINE", raw)
    assert scan_engine() == expected


@pytest.mark.parametrize("raw", ["", "docker", "bomlens2"])
def test_an_unknown_value_reads_as_the_in_process_engine(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("SCAN_ENGINE", raw)
    assert scan_engine() == "inprocess"


def test_the_value_is_read_per_call_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_ENGINE", "bomlens")
    assert scan_engine() == "bomlens"
    monkeypatch.setenv("SCAN_ENGINE", "inprocess")
    assert scan_engine() == "inprocess"


def test_the_documented_values_are_the_accepted_ones() -> None:
    assert SCAN_ENGINE_VALUES == ("inprocess", "bomlens")


# ---------------------------------------------------------------------------
# SCAN_CONTAINER_ENGINE: the same values, its own setting
# ---------------------------------------------------------------------------


def test_the_container_engine_defaults_to_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCAN_CONTAINER_ENGINE", raising=False)
    monkeypatch.setenv("SCAN_ENGINE", "bomlens")
    # SCAN_ENGINE says nothing about container scans.
    assert scan_container_engine() == "inprocess"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("bomlens", "bomlens"), (" BOMLENS ", "bomlens"), ("", "inprocess"), ("docker", "inprocess")],
)
def test_the_container_engine_reads_its_own_variable(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    monkeypatch.setenv("SCAN_ENGINE", "inprocess")
    monkeypatch.setenv("SCAN_CONTAINER_ENGINE", raw)
    assert scan_container_engine() == expected


@pytest.mark.parametrize(
    ("source", "container", "in_use"),
    [
        ("inprocess", "inprocess", False),
        ("bomlens", "inprocess", True),
        ("inprocess", "bomlens", True),
    ],
)
def test_the_engine_is_in_use_when_either_kind_selects_it(
    monkeypatch: pytest.MonkeyPatch, source: str, container: str, in_use: bool
) -> None:
    monkeypatch.setenv("SCAN_ENGINE", source)
    monkeypatch.setenv("SCAN_CONTAINER_ENGINE", container)
    assert bomlens_engine_in_use() is in_use
