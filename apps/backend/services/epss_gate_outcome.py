# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Whether the EPSS axis of the build gate actually judged anything.

``GATE_EPSS_THRESHOLD`` blocks a build when an open finding's CVE carries an
EPSS score at or above the threshold. ``NULL >= x`` is NULL in SQL, so a CVE
with no score cannot trip it, which is the right semantic for one CVE and the
wrong outcome for a whole deployment: when nothing has a score the count is 0,
the gate passes, and the pass looks like a verdict. An operator who set the
threshold has said they want this axis to block, and until now that intent was
discarded in silence.

This is the third time the codebase has answered the same question, and the
answer is the same both previous times: say which kind of zero it was.
``component_outcome`` distinguishes "no components found" from "nothing to
find" (``services.scan_outcome``), and ``malicious_gate_enforced`` says its
count is 0 "because nothing was checked, NOT because nothing was found". The
EPSS axis was the one that had no such marker.

Four outcomes
-------------

``not_configured``
    No threshold is set, so the axis is off by choice. A count of 0 here means
    what it says.

``evaluated``
    A threshold is set and every open finding on the scan carries a score. The
    count is a complete answer.

``partial``
    A threshold is set and some open findings carry no score. The count is
    true as far as it goes: a finding above the threshold still blocks. What
    it cannot say is that nothing else would have. This is a normal state, not
    a fault: EPSS does not score every CVE, so even a healthy sync leaves gaps
    on a scan whose CVEs the feed has not reached.

``no_data``
    A threshold is set and not one open finding carries a score. The axis
    decided nothing at all. This is what an air-gapped deployment with the
    sync off looks like, and it is the state that used to read as a pass.

Why ``block`` applies to ``no_data`` only
-----------------------------------------
``GATE_EPSS_ON_MISSING_DATA=block`` turns the undecided case into a blocked
build. It deliberately does NOT fire on ``partial``.

``partial`` is common, and an option that fires on a common state is an option
nobody can leave on: the first week of red builds gets it switched off, and a
safety control that has been switched off protects nothing. Reserving ``block``
for ``no_data`` keeps it switchable, and ``no_data`` is the state that actually
means the operator's threshold was ignored end to end.

A deployment that wants to block on ``partial`` too is a coherent thing to
want, and this is a string vocabulary rather than a boolean precisely so that
value can be added later without a schema change or a rename.
"""

from __future__ import annotations

from typing import Final

from services.gate_axis_outcome import (
    AXIS_EVALUATED,
    AXIS_NO_DATA,
    AXIS_NOT_CONFIGURED,
    AXIS_PARTIAL,
    ON_MISSING_ALLOW,
    ON_MISSING_BLOCK,
    axis_blocks,
    classify_axis_outcome,
)

# ER29 moved these four strings, and the rule for reading them, into
# ``services.gate_axis_outcome`` when KEV and end-of-life needed the same
# vocabulary. They are aliased rather than redefined so there is one definition
# of what "partial" means across every axis: three copies of four strings drift,
# and a drifted copy makes the same word mean different things depending on
# which axis produced it. The EPSS-specific reasoning above still applies and is
# why this module exists at all.

#: No threshold configured; the EPSS axis is off by choice.
EPSS_NOT_CONFIGURED: Final = AXIS_NOT_CONFIGURED

#: Threshold set and every open finding carries a score.
EPSS_EVALUATED: Final = AXIS_EVALUATED

#: Threshold set; some open findings carry no score.
EPSS_PARTIAL: Final = AXIS_PARTIAL

#: Threshold set; no open finding carries a score, so the axis decided nothing.
EPSS_NO_DATA: Final = AXIS_NO_DATA

EPSS_GATE_OUTCOME_VALUES: Final = (
    EPSS_NOT_CONFIGURED,
    EPSS_EVALUATED,
    EPSS_PARTIAL,
    EPSS_NO_DATA,
)

# ``allow`` / ``block`` rather than ``pass`` / ``fail``: the gate's own
# outcome is already pass/fail, and reusing those words for the policy makes
# "pass on missing data" read as a result when it is an instruction.
#: Let an undecided EPSS axis through. The default: an existing deployment
#: with the sync off must not have every build start failing on upgrade,
#: because nothing about that deployment changed.
EPSS_MISSING_ALLOW: Final = ON_MISSING_ALLOW

#: Block when the EPSS axis decided nothing, so a configured threshold cannot
#: be ignored in silence. See the module docstring on why this is ``no_data``
#: only.
EPSS_MISSING_BLOCK: Final = ON_MISSING_BLOCK

EPSS_ON_MISSING_DATA_VALUES: Final = (
    EPSS_MISSING_ALLOW,
    EPSS_MISSING_BLOCK,
)


def classify_epss_gate_outcome(
    *,
    threshold: float | None,
    open_findings: int,
    findings_with_score: int,
) -> str:
    """Classify what the EPSS axis was able to judge on one scan.

    Args:
        threshold: The active ``GATE_EPSS_THRESHOLD``, or None when unset.
        open_findings: Open findings on the evaluated scan, counted on the
            same status set the EPSS count itself uses so the two agree.
        findings_with_score: How many of those carry a non-NULL EPSS score.

    A scan with no open findings at all and a threshold set classifies as
    ``evaluated``: there was nothing to score, and the gate's pass is a real
    answer rather than an absence of one. Calling that ``no_data`` would put a
    caveat on the cleanest possible result.
    """
    return classify_axis_outcome(
        configured=threshold is not None,
        candidates=open_findings,
        with_data=findings_with_score,
    )


def epss_gate_blocks(outcome: str, on_missing_data: str) -> bool:
    """Whether this outcome blocks the build under the configured policy.

    Only ``no_data`` under ``block``. Every other combination passes,
    including ``partial`` under ``block`` (see the module docstring).
    """
    return axis_blocks(outcome, on_missing_data)


__all__ = [
    "EPSS_EVALUATED",
    "EPSS_GATE_OUTCOME_VALUES",
    "EPSS_MISSING_ALLOW",
    "EPSS_MISSING_BLOCK",
    "EPSS_NO_DATA",
    "EPSS_NOT_CONFIGURED",
    "EPSS_ON_MISSING_DATA_VALUES",
    "EPSS_PARTIAL",
    "classify_epss_gate_outcome",
    "epss_gate_blocks",
]
