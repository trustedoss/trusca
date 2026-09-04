# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Every module that decides "this finding is no longer open work" uses one set.

``services.policy_gate._CLOSED_FINDING_STATUSES`` is that set. It is a
judgement, not a list of constants: ``suppressed`` is deliberately absent,
because a suppressed critical is still work somebody owes. A module carrying
its own copy will keep whatever the judgement was on the day it was written.

Two modules did carry copies. Both said in a comment that they mirrored the
gate, which is exactly the sort of promise that holds until it does not: the
comment does not fail when the sets diverge. They now import, and this file
asserts import IDENTITY rather than equality, so a future local copy that
happens to hold the same values still fails.

The pattern is ``test_vuln_sla_sweep.test_status_vocabulary_mirrors_gate``;
this file gathers every consumer in one place so adding one has an obvious
home.
"""

from __future__ import annotations

import pytest

CONSUMERS = (
    "api.v1.policy_gate",
    "services.vulnerability_service",
    "services.metrics_service",
    "services.remediation_service",
    "services.upgrade_cluster_service",
    "tasks.vuln_sla_sweep",
)


@pytest.mark.parametrize("module_path", CONSUMERS)
def test_consumers_share_the_gate_object(module_path: str) -> None:
    """``is``, not ``==``: a copy with today's values would pass equality."""
    import importlib

    from services.policy_gate import _CLOSED_FINDING_STATUSES

    module = importlib.import_module(module_path)
    theirs = getattr(module, "_CLOSED_FINDING_STATUSES", None)
    assert theirs is not None, (
        f"{module_path} no longer exposes _CLOSED_FINDING_STATUSES; if it "
        "stopped needing the vocabulary, remove it from CONSUMERS, and if it "
        "renamed the import, this contract needs to follow"
    )
    assert theirs is _CLOSED_FINDING_STATUSES, (
        f"{module_path} holds its own closed-status set. Import the gate's "
        "instead: a copy is correct on the day it is written and silently "
        f"wrong the day the judgement changes. Its value is {theirs!r}"
    )


def test_suppressed_is_not_closed() -> None:
    """The judgement the set encodes, stated where it can fail.

    A suppressed finding is still open work for the team, so it must not be
    filtered out of gate counts or upgrade recommendations. Written as a test
    because the reasoning lives in a comment otherwise, and comments do not
    fail.
    """
    from services.policy_gate import _CLOSED_FINDING_STATUSES

    assert "suppressed" not in _CLOSED_FINDING_STATUSES
    assert set(_CLOSED_FINDING_STATUSES) == {"not_affected", "fixed", "false_positive"}
