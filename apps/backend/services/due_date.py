# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Which deadline governs a finding: the policy's or the person's (ER28a).

A finding can carry two deadlines. ``vuln_sla_days`` derives one from the
severity, and ER28a lets a person write one down in ``due_on``. They disagree,
and something has to say which is the deadline.

The rule: the EARLIEST of the two. A person may commit to a date sooner than
the policy, never later.

Why that and not either alternative
-----------------------------------
1. A person who writes an earlier date is making a commitment. If the policy
   date kept winning, that commitment would be recorded and then quietly
   ignored, and the person would believe a date was being tracked that was not.
2. A person who writes a later date is not entitled to relax the organization's
   policy on their own. If the later date won, one person could move an
   organization-wide deadline by editing a field.
3. Nothing else can currently stop the SLA clock, so a later-date-wins rule
   would make this field the only way to silence an SLA breach, and it would do
   so with none of the record a status change leaves.
4. It gives ``due_on`` force where the policy has none. ``info`` and
   ``unknown`` severities have no SLA window, so their ``sla_due`` is NULL and
   they can never be overdue. Under earliest-wins a person can put a real
   deadline on one. Under policy-always-wins the field would be decorative on
   exactly the rows most likely to need it.

Reason 3 rests on ``suppressed`` being absent from
``services.policy_gate._CLOSED_FINDING_STATUSES``, which is true today. Reasons
1, 2 and 4 do not depend on it. If somebody later closes that gap, this rule
stands unchanged and is not reopened by that change.

A date is not an instant
------------------------
``due_on`` is a calendar date and the SLA due is a timestamp, so comparing them
needs a boundary. A deadline of the 7th is met by work finished during the 7th,
so it expires at the END of that day: the instant is the start of the 8th, in
UTC. Both the SQL and the Python side say that in those terms rather than each
rounding in its own direction.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

#: What ``due_source`` can say. ``None`` when the finding has no deadline at
#: all (no SLA window for its severity, and nobody wrote a date down).
DUE_SOURCE_SLA = "sla"
DUE_SOURCE_MANUAL = "manual"
DUE_SOURCE_VALUES = (DUE_SOURCE_SLA, DUE_SOURCE_MANUAL)


def manual_due_instant(due_on: date | None) -> datetime | None:
    """The instant a calendar deadline expires: the start of the NEXT day, UTC.

    The SQL twin builds the same instant with
    ``timezone('UTC', due_on::timestamp + interval '1 day')``. Written in UTC
    explicitly rather than casting to ``timestamptz``, which would interpret
    the date in whatever the session timezone happens to be and make the
    verdict depend on a connection setting.
    """
    if due_on is None:
        return None
    return datetime.combine(due_on, time.min, tzinfo=UTC) + timedelta(days=1)


def effective_due(
    *, sla_due: datetime | None, due_on: date | None
) -> tuple[datetime | None, str | None]:
    """``(instant, source)`` for the deadline that actually governs.

    Returns ``(None, None)`` when there is no deadline of either kind. A tie
    resolves to the policy, which changes no verdict and keeps the source
    stable when a person writes down exactly the policy date.
    """
    manual = manual_due_instant(due_on)
    if manual is None and sla_due is None:
        return None, None
    if manual is None:
        return sla_due, DUE_SOURCE_SLA
    if sla_due is None:
        return manual, DUE_SOURCE_MANUAL
    if manual < sla_due:
        return manual, DUE_SOURCE_MANUAL
    return sla_due, DUE_SOURCE_SLA


def manual_due_is_ignored(
    *, sla_due: datetime | None, due_on: date | None
) -> bool:
    """Whether a written-down date is LATER than the policy's, by calendar day.

    The write path reports this so the person who just typed a date is told at
    that moment when the policy date still applies. Learning it later from a
    list nobody re-reads is the mirror of the failure this rule exists to
    prevent: believing a deadline is tracked when it is not.

    This compares calendar days while :func:`effective_due` compares instants,
    and the difference is deliberate rather than an oversight.

    ``sla_due`` carries a time of day, because it is the detection moment plus
    a window: 7 Sep 14:23, not 7 Sep. A date written as 7 Sep expires at the
    end of that day, 8 Sep 00:00, which is later, so the policy correctly keeps
    the verdict. But the screen shows that policy deadline as "7 Sep", so
    telling somebody who typed 7 Sep that their date was ignored would be
    reporting a disagreement they cannot see and did not cause. They asked for
    the day the policy already requires, and they got it.

    So the verdict is decided on instants, where the time of day matters, and
    the message is decided on days, which is the unit the person typed in and
    the unit the screen displays. Only a strictly later DAY is worth a warning.
    """
    if due_on is None or sla_due is None:
        # No competition: a written date with no policy date always governs.
        return False
    return due_on > sla_due.astimezone(UTC).date()


__all__ = [
    "DUE_SOURCE_MANUAL",
    "DUE_SOURCE_SLA",
    "DUE_SOURCE_VALUES",
    "effective_due",
    "manual_due_instant",
    "manual_due_is_ignored",
]
