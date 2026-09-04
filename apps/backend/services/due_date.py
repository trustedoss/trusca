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

from datetime import UTC, date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

log = structlog.get_logger("services.due_date")

#: What ``due_source`` can say. ``None`` when the finding has no deadline at
#: all (no SLA window for its severity, and nobody wrote a date down).
DUE_SOURCE_SLA = "sla"
DUE_SOURCE_MANUAL = "manual"
DUE_SOURCE_VALUES = (DUE_SOURCE_SLA, DUE_SOURCE_MANUAL)


#: Used when an organization has no timezone, and when the one it has cannot
#: be loaded. Matches the column default, so a deployment that sets nothing
#: keeps the behaviour it had before 0083.
DEFAULT_TIMEZONE = "UTC"


def resolve_timezone(name: str | None, *, organization_id: object = None) -> tzinfo:
    """The zone to read calendar deadlines in, falling back to UTC.

    Falls back rather than raising, and that is a deliberate trade. The
    column takes any text because a CHECK constraint cannot consult
    ``pg_timezone_names``, so a value written straight to the database can be
    nonsense. This function is called from the SLA sweep and from list
    endpoints; raising there would take out an unrelated tenant's alerting
    over one organization's bad string. The input path (``schemas.admin``)
    rejects unloadable names, so this branch should be unreachable through
    the product.
    """
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        # The organization id travels with the value: without it the line
        # says a string could not be loaded and nobody can find whose setting
        # to fix, and that organization goes on believing its timezone
        # applies.
        log.warning(
            "due_date_timezone_unloadable",
            timezone=name,
            organization_id=None if organization_id is None else str(organization_id),
        )
        return UTC


def manual_due_instant(
    due_on: date | None,
    *,
    timezone: str | None = None,
    organization_id: object = None,
) -> datetime | None:
    """The instant a calendar deadline expires: the start of the NEXT day.

    Next day *in the organization's timezone*, not in UTC. A date somebody
    typed is a calendar date in the place they work: read in UTC, a deadline
    of the 7th expires at 19:00 on the 7th for someone at UTC-5, while it is
    still the day they were given. East of UTC the same reading hands out
    extra hours instead, which is why only half of this was ever reported.

    The SQL twin builds the same instant with
    ``timezone('UTC', timezone(<zone>, due_on::timestamp + interval '1 day'))``:
    the inner call reads the naive next-midnight AS that zone's wall clock and
    yields an instant, the outer normalises it back to UTC for comparison.
    Casting to ``timestamptz`` instead would read the date in whatever the
    session timezone happens to be and make the verdict depend on a connection
    setting. ``tests`` drives both against each other across zones east of,
    west of, and equal to UTC.
    """
    if due_on is None:
        return None
    zone = resolve_timezone(timezone, organization_id=organization_id)
    return datetime.combine(due_on + timedelta(days=1), time.min, tzinfo=zone)


def effective_due(
    *,
    sla_due: datetime | None,
    due_on: date | None,
    timezone: str | None = None,
    organization_id: object = None,
) -> tuple[datetime | None, str | None]:
    """``(instant, source)`` for the deadline that actually governs.

    Returns ``(None, None)`` when there is no deadline of either kind. A tie
    resolves to the policy, which changes no verdict and keeps the source
    stable when a person writes down exactly the policy date.
    """
    manual = manual_due_instant(
        due_on, timezone=timezone, organization_id=organization_id
    )
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
    "DEFAULT_TIMEZONE",
    "manual_due_instant",
    "resolve_timezone",
    "manual_due_is_ignored",
]
