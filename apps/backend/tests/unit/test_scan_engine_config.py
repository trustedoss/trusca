"""``SCAN_ENGINE`` (preview): read at call time, unknown values fall back safely."""

from __future__ import annotations

import pytest

from core.config import SCAN_ENGINE_VALUES, scan_engine


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
