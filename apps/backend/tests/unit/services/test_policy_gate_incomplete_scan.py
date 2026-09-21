"""The pure decision and wording of the incomplete-scan gate axis (U3-D)."""

from __future__ import annotations

import pytest

from services import sbom_completeness as sc
from services.policy_gate import _build_reason, incomplete_scan_blocks


@pytest.mark.parametrize(
    ("outcome", "on_unknown", "blocks"),
    [
        (sc.INCOMPLETE, "allow", True),
        (sc.INCOMPLETE, "block", True),
        (sc.UNKNOWN, "allow", False),
        (sc.UNKNOWN, "block", True),
        (sc.COMPLETE, "allow", False),
        (sc.COMPLETE, "block", False),
        ("not_configured", "block", False),
    ],
)
def test_which_verdicts_fail_the_build(outcome: str, on_unknown: str, blocks: bool) -> None:
    assert incomplete_scan_blocks(outcome, on_unknown) is blocks


def test_the_reason_names_the_cause_and_leaves_other_text_alone() -> None:
    plain = _build_reason(0, 0)
    assert plain is None
    incomplete = _build_reason(
        0,
        0,
        incomplete_scan_blocked=True,
        incomplete_scan_outcome=sc.INCOMPLETE,
        incomplete_scan_basis="stage_degraded:prep",
    )
    assert incomplete is not None and "stage_degraded:prep" in incomplete
    unknown = _build_reason(
        0,
        0,
        incomplete_scan_blocked=True,
        incomplete_scan_outcome=sc.UNKNOWN,
        incomplete_scan_basis="not_recorded",
    )
    assert unknown is not None and "could not be established" in unknown
    # Not blocking: no clause, whatever the outcome says.
    assert _build_reason(0, 0, incomplete_scan_outcome=sc.INCOMPLETE) is None
