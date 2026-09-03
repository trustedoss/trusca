# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""``services.scan_outcome``: telling apart the ways a scan can find nothing.

The defect this guards is that a scan whose SBOM came back empty exited 0, was
recorded ``succeeded``, passed the build gate because every count it reads was
0, and showed the never-scanned empty state to somebody who had just scanned.
The fix is not to fail those scans, because for a build system the scanner
cannot read an empty result is the correct answer. It is to say which kind of
empty it was.
"""

from __future__ import annotations

import pytest

from services.scan_outcome import (
    COMPONENT_OUTCOME_VALUES,
    COMPONENTS_FOUND,
    EMPTY_NO_MANIFESTS,
    EMPTY_WITH_MANIFESTS,
    classify_component_outcome,
    is_empty,
    manifest_count,
)


def _inventory(count: int) -> dict[str, object]:
    return {
        "files": [
            {"path": f"pkg-{i}/package.json", "size": 10, "sha256": "x"} for i in range(count)
        ],
        "count": count,
        "truncated": False,
    }


@pytest.mark.parametrize(
    ("components", "inventory", "expected"),
    [
        # The ordinary case, with and without manifests recorded.
        (12, _inventory(3), COMPONENTS_FOUND),
        (1, None, COMPONENTS_FOUND),
        # Nothing found and nothing declared: the expected answer for a build
        # system we do not read. Telling this user their project is clean would
        # be the lie; telling them we could not read it is the truth.
        (0, None, EMPTY_NO_MANIFESTS),
        (0, {"files": [], "count": 0, "truncated": False}, EMPTY_NO_MANIFESTS),
        # Nothing found despite manifests being there: build-prep is
        # best-effort, and a prep failure empties an ecosystem silently.
        (0, _inventory(1), EMPTY_WITH_MANIFESTS),
        (0, _inventory(40), EMPTY_WITH_MANIFESTS),
    ],
)
def test_classify_component_outcome(
    components: int, inventory: dict[str, object] | None, expected: str
) -> None:
    assert (
        classify_component_outcome(component_count=components, manifest_inventory=inventory)
        == expected
    )


def test_a_populated_sbom_is_never_an_empty_outcome() -> None:
    """Under-reporting is not emptiness and must not borrow its warning.

    A tree with a ``package.json`` and no ``package-lock.json`` yields the
    direct dependencies and drops the transitive ones. That is one component,
    not zero, and it has a different cause and a different fix than an empty
    result, so it must not be classified as one.
    """
    assert (
        classify_component_outcome(component_count=1, manifest_inventory=_inventory(1))
        == COMPONENTS_FOUND
    )
    assert is_empty(COMPONENTS_FOUND) is False


def test_is_empty_covers_both_empty_outcomes_and_nothing_else() -> None:
    assert is_empty(EMPTY_NO_MANIFESTS) is True
    assert is_empty(EMPTY_WITH_MANIFESTS) is True
    assert is_empty(COMPONENTS_FOUND) is False
    # None is a scan predating the capture: unknown, not either answer.
    assert is_empty(None) is False


def test_manifest_count_treats_absent_and_malformed_as_zero() -> None:
    assert manifest_count(None) == 0
    assert manifest_count({}) == 0
    assert manifest_count({"count": "3"}) == 0
    assert manifest_count(_inventory(3)) == 3


def test_every_named_outcome_is_in_the_published_tuple() -> None:
    """The tuple is what the API validates against, so it must not drift."""
    assert set(COMPONENT_OUTCOME_VALUES) == {
        COMPONENTS_FOUND,
        EMPTY_NO_MANIFESTS,
        EMPTY_WITH_MANIFESTS,
    }
