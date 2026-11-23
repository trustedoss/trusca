# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Singular/plural helper used by server-built English text."""

from __future__ import annotations

import pytest

from core.plural import count_noun, noun


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, "0 scans"), (1, "1 scan"), (2, "2 scans"), (-1, "-1 scans")],
)
def test_count_noun_default_plural(count: int, expected: str) -> None:
    assert count_noun(count, "scan") == expected


def test_count_noun_irregular_plural() -> None:
    assert count_noun(1, "entry", "entries") == "1 entry"
    assert count_noun(3, "entry", "entries") == "3 entries"


def test_noun_only_returns_the_word() -> None:
    assert noun(1, "CVE") == "CVE"
    assert noun(5, "CVE") == "CVEs"
