# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Whether a data-driven gate axis actually judged anything (ER43, ER29).

An axis that decides from per-finding data can return a count of 0 for two
completely different reasons: it looked and found nothing, or it had nothing to
look at. Only the first is a verdict. The second is an operator's intent being
discarded in silence, and it reads as a pass.

ER43 answered this for the EPSS axis. ER29 added KEV and end-of-life, and the
answer is the same shape every time, so the vocabulary lives here once rather
than being retyped per axis. That matters beyond tidiness: three copies of four
strings drift, and a drifted copy means the same word describes different
states depending on which axis produced it. This module is what
``services.epss_gate_outcome`` and the KEV / EOL axes all resolve to.

The four outcomes
-----------------

``not_configured``
    The axis is off by choice. A count of 0 means what it says.

``evaluated``
    The axis was on and every candidate carried the data it needs. The count is
    a complete answer. A scan with no candidates at all also lands here: there
    was nothing to judge, which is a real result and not an absence of one.

``partial``
    Some candidates carried the data and some did not. The count is true as far
    as it goes; what it cannot say is that nothing else would have tripped.

``no_data``
    Not one candidate carried the data. The axis decided nothing whatsoever,
    and this is the state that used to read as a pass.

Why ``block`` fires only on ``no_data``
--------------------------------------
``partial`` is a normal state for every axis here, not a fault: EPSS does not
score every CVE, endoflife.date covers a curated set of products, and a CVE
discovered after the last KEV sync has not been reconciled yet. An option that
fires on a common state is one nobody can leave switched on, and a safety
control that has been switched off protects nothing. Reserving ``block`` for
``no_data`` keeps it switchable, and ``no_data`` is the state that actually
means the axis was ignored end to end.

Blocking on ``partial`` is a coherent thing to want, and these are strings
rather than booleans precisely so that value can be added later without a
schema change or a rename.
"""

from __future__ import annotations

from typing import Final

#: The axis is off by choice.
AXIS_NOT_CONFIGURED: Final = "not_configured"

#: On, and every candidate carried the data.
AXIS_EVALUATED: Final = "evaluated"

#: On, and only some candidates carried the data.
AXIS_PARTIAL: Final = "partial"

#: On, and no candidate carried the data. The axis judged nothing.
AXIS_NO_DATA: Final = "no_data"

AXIS_OUTCOME_VALUES: Final = (
    AXIS_NOT_CONFIGURED,
    AXIS_EVALUATED,
    AXIS_PARTIAL,
    AXIS_NO_DATA,
)

#: Undecided is not a reason to fail the build (the default, and the behaviour
#: every deployment had before these axes learned to say so).
ON_MISSING_ALLOW: Final = "allow"

#: An axis that judged nothing fails the build instead of passing quietly.
ON_MISSING_BLOCK: Final = "block"

ON_MISSING_DATA_VALUES: Final = (ON_MISSING_ALLOW, ON_MISSING_BLOCK)


def classify_axis_outcome(
    *,
    configured: bool,
    candidates: int,
    with_data: int,
) -> str:
    """Classify what one axis was able to judge on one scan.

    Args:
        configured: Whether the operator switched this axis on at all.
        candidates: Rows the axis would judge (open findings, scanned
            components), counted over exactly the row set the axis's own count
            uses. A coverage figure taken over a different row set than the
            count it qualifies is worse than none.
        with_data: How many of those carry the signal the axis needs.

    ``candidates == 0`` classifies as ``evaluated`` rather than ``no_data``:
    there was nothing to judge, so the pass is a real answer. Calling that
    "no data" would put a caveat on the cleanest possible result.
    """
    if not configured:
        return AXIS_NOT_CONFIGURED
    if candidates == 0:
        return AXIS_EVALUATED
    if with_data == 0:
        return AXIS_NO_DATA
    if with_data < candidates:
        return AXIS_PARTIAL
    return AXIS_EVALUATED


def axis_blocks(outcome: str, on_missing_data: str) -> bool:
    """Whether this outcome blocks the build under the configured policy.

    Only ``no_data`` under ``block``. Every other combination passes, including
    ``partial`` under ``block`` (see the module docstring).
    """
    return outcome == AXIS_NO_DATA and on_missing_data == ON_MISSING_BLOCK


__all__ = [
    "AXIS_EVALUATED",
    "AXIS_NOT_CONFIGURED",
    "AXIS_NO_DATA",
    "AXIS_OUTCOME_VALUES",
    "AXIS_PARTIAL",
    "ON_MISSING_ALLOW",
    "ON_MISSING_BLOCK",
    "ON_MISSING_DATA_VALUES",
    "axis_blocks",
    "classify_axis_outcome",
]
