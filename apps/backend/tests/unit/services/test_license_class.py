# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Copyleft-strength classification (gap #27, gap #20 folded in).

The headline behaviour under test is negative: an unrecognised license must
never be classified permissive. Everything else — the pattern ORDER, the
worst-of precedence, the suffix variants — exists to serve that, so each gets
its own case rather than being implied by a happy path.
"""

from __future__ import annotations

import pytest

from services.license_class import (
    CLASS_RANK,
    LICENSE_CLASS_VALUES,
    MAX_IDENTIFIER_LENGTH,
    NETWORK_COPYLEFT,
    PERMISSIVE,
    PERMISSIVE_IDS,
    STRONG_COPYLEFT,
    UNCATEGORIZED,
    WEAK_COPYLEFT,
    classify_license_class,
    worst_license_class,
)


@pytest.mark.parametrize("spdx_id", sorted(PERMISSIVE_IDS))
def test_every_allowlisted_id_is_permissive(spdx_id: str) -> None:
    assert classify_license_class(spdx_id) == PERMISSIVE


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Order matters: AGPL and LGPL are tested before the bare GPL check, or
        # both would collapse into strong-copyleft and the axis would be useless.
        ("AGPL-3.0-only", NETWORK_COPYLEFT),
        ("AGPL-3.0-or-later", NETWORK_COPYLEFT),
        ("agpl-3.0", NETWORK_COPYLEFT),
        ("LGPL-2.1-only", WEAK_COPYLEFT),
        ("LGPL-3.0-or-later", WEAK_COPYLEFT),
        ("LGPL-2.1+", WEAK_COPYLEFT),
        ("GPL-2.0-only", STRONG_COPYLEFT),
        ("GPL-3.0-or-later", STRONG_COPYLEFT),
        ("GPL-2.0+", STRONG_COPYLEFT),
        # The non-GPL reciprocal family.
        ("MPL-2.0", WEAK_COPYLEFT),
        ("EPL-2.0", WEAK_COPYLEFT),
        ("CDDL-1.0", WEAK_COPYLEFT),
        ("CPL-1.0", WEAK_COPYLEFT),
        ("OSL-3.0", WEAK_COPYLEFT),
        ("EUPL-1.2", WEAK_COPYLEFT),
        ("CECILL-2.1", WEAK_COPYLEFT),
        ("Sleepycat", WEAK_COPYLEFT),
    ],
)
def test_pattern_classification(value: str, expected: str) -> None:
    assert classify_license_class(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "LicenseRef-Acme-Proprietary",
        "Frobnicate-1.0",
        "SEE LICENSE IN COPYING",
        "NOASSERTION",
    ],
)
def test_unrecognised_is_never_permissive(value: str | None) -> None:
    """The rule the whole module exists for: not knowing is not "harmless"."""
    assert classify_license_class(value) == UNCATEGORIZED


def test_over_long_input_is_uncategorized_not_permissive() -> None:
    # A pathological string must degrade to "a human must look", not to the
    # safe-looking end of the scale.
    assert classify_license_class("M" * (MAX_IDENTIFIER_LENGTH + 1)) == UNCATEGORIZED


def test_whitespace_is_stripped_before_matching() -> None:
    assert classify_license_class("  MIT  ") == PERMISSIVE


def test_class_rank_puts_unknown_above_permissive() -> None:
    """An unrecognised license outranks a known-permissive one, deliberately."""
    assert CLASS_RANK[UNCATEGORIZED] > CLASS_RANK[PERMISSIVE]
    assert CLASS_RANK[NETWORK_COPYLEFT] > CLASS_RANK[STRONG_COPYLEFT]
    assert CLASS_RANK[STRONG_COPYLEFT] > CLASS_RANK[WEAK_COPYLEFT]
    assert CLASS_RANK[WEAK_COPYLEFT] > CLASS_RANK[UNCATEGORIZED]


def test_rank_covers_the_whole_vocabulary() -> None:
    assert set(CLASS_RANK) == set(LICENSE_CLASS_VALUES)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([], UNCATEGORIZED),
        (["MIT"], PERMISSIVE),
        (["MIT", "GPL-3.0-only"], STRONG_COPYLEFT),
        (["MIT", "Frobnicate-1.0"], UNCATEGORIZED),
        (["GPL-2.0-only", "AGPL-3.0-only"], NETWORK_COPYLEFT),
        (["LGPL-2.1-only", "MPL-2.0"], WEAK_COPYLEFT),
        ([None, "", "  "], UNCATEGORIZED),
    ],
)
def test_worst_of_across_a_component(values: list[str | None], expected: str) -> None:
    assert worst_license_class(values) == expected
