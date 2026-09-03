# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The shared gate-axis outcome vocabulary (ER29).

Three axes now answer "did this axis actually judge anything": EPSS (ER43), KEV
and end-of-life. The rule is the same for all three, so it lives in one module.
These tests pin the rule, and the contract test at the bottom pins that the
EPSS axis really does resolve to it rather than carrying a second copy of four
strings that could drift.
"""

from __future__ import annotations

import pytest

from services.gate_axis_outcome import (
    AXIS_EVALUATED,
    AXIS_NO_DATA,
    AXIS_NOT_CONFIGURED,
    AXIS_OUTCOME_VALUES,
    AXIS_PARTIAL,
    ON_MISSING_ALLOW,
    ON_MISSING_BLOCK,
    axis_blocks,
    classify_axis_outcome,
)


@pytest.mark.parametrize(
    ("configured", "candidates", "with_data", "expected"),
    [
        # Off by choice: a 0 count means what it says.
        (False, 0, 0, AXIS_NOT_CONFIGURED),
        (False, 10, 0, AXIS_NOT_CONFIGURED),
        # Nothing to judge is a real answer, not an absence of one. Calling it
        # no_data would put a caveat on the cleanest possible result.
        (True, 0, 0, AXIS_EVALUATED),
        # The state that used to read as a pass.
        (True, 5, 0, AXIS_NO_DATA),
        # True as far as it goes.
        (True, 5, 1, AXIS_PARTIAL),
        (True, 5, 4, AXIS_PARTIAL),
        # Complete.
        (True, 5, 5, AXIS_EVALUATED),
    ],
)
def test_classification(configured: bool, candidates: int, with_data: int, expected: str) -> None:
    assert (
        classify_axis_outcome(
            configured=configured, candidates=candidates, with_data=with_data
        )
        == expected
    )


@pytest.mark.parametrize("outcome", AXIS_OUTCOME_VALUES)
def test_only_no_data_under_block_fails_the_build(outcome: str) -> None:
    """`partial` under `block` must pass.

    An option that fires on a common state is one nobody can leave switched on,
    and a safety control that has been switched off protects nothing.
    """
    assert axis_blocks(outcome, ON_MISSING_ALLOW) is False
    assert axis_blocks(outcome, ON_MISSING_BLOCK) is (outcome == AXIS_NO_DATA)


def test_the_epss_axis_resolves_to_this_vocabulary() -> None:
    """Hardening rule 2: the same vocabulary in two places needs an equality
    test. EPSS keeps its own names for its importers; they must be these."""
    from services import epss_gate_outcome as epss

    assert epss.EPSS_NOT_CONFIGURED == AXIS_NOT_CONFIGURED
    assert epss.EPSS_EVALUATED == AXIS_EVALUATED
    assert epss.EPSS_PARTIAL == AXIS_PARTIAL
    assert epss.EPSS_NO_DATA == AXIS_NO_DATA
    assert tuple(epss.EPSS_GATE_OUTCOME_VALUES) == AXIS_OUTCOME_VALUES
    assert epss.EPSS_MISSING_ALLOW == ON_MISSING_ALLOW
    assert epss.EPSS_MISSING_BLOCK == ON_MISSING_BLOCK


def test_the_epss_classifier_agrees_with_the_shared_one() -> None:
    """Not just the strings: the rule itself must be one implementation."""
    from services.epss_gate_outcome import classify_epss_gate_outcome

    for threshold in (None, 0.5):
        for candidates, with_data in ((0, 0), (5, 0), (5, 2), (5, 5)):
            assert classify_epss_gate_outcome(
                threshold=threshold,
                open_findings=candidates,
                findings_with_score=with_data,
            ) == classify_axis_outcome(
                configured=threshold is not None,
                candidates=candidates,
                with_data=with_data,
            )
