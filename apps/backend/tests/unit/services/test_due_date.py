# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Which deadline governs, and when the author is told it is not theirs (ER28a).

The rule lives in one module, but it is evaluated by three things: the list's
SQL, the drawer's Python, and the SLA sweep. These pin the Python side and the
vocabulary; ``test_finding_assignment.py`` drives the same cases through real
SQL so the two cannot answer differently.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from services.due_date import (
    DUE_SOURCE_MANUAL,
    DUE_SOURCE_SLA,
    DUE_SOURCE_VALUES,
    effective_due,
    manual_due_instant,
    manual_due_is_ignored,
)

#: The policy deadline carries a TIME, because it is a detection moment plus a
#: window. That is what makes the same calendar day a losing bid.
SLA = datetime(2026, 9, 7, 14, 23, tzinfo=UTC)


# --- the four NULL combinations --------------------------------------------
#
# Postgres LEAST ignores NULLs and Python's min() raises on them, so these are
# where a twin implementation diverges. The SQL side is driven with the same
# four in the integration test.


def test_neither_deadline_means_no_deadline() -> None:
    assert effective_due(sla_due=None, due_on=None) == (None, None)


def test_only_a_policy_deadline() -> None:
    assert effective_due(sla_due=SLA, due_on=None) == (SLA, DUE_SOURCE_SLA)


def test_only_a_written_deadline() -> None:
    """The case that gives `due_on` force where the policy has none: info and
    unknown severities have no SLA window at all."""
    due, source = effective_due(sla_due=None, due_on=date(2026, 9, 3))
    assert source == DUE_SOURCE_MANUAL
    assert due == datetime(2026, 9, 4, tzinfo=UTC)


def test_both_deadlines_take_the_earlier() -> None:
    due, source = effective_due(sla_due=SLA, due_on=date(2026, 9, 3))
    assert source == DUE_SOURCE_MANUAL
    assert due == datetime(2026, 9, 4, tzinfo=UTC)


# --- the rule itself --------------------------------------------------------


def test_a_person_may_commit_to_a_sooner_date() -> None:
    _due, source = effective_due(sla_due=SLA, due_on=date(2026, 9, 1))
    assert source == DUE_SOURCE_MANUAL


def test_a_person_may_not_push_the_policy_out() -> None:
    """The half that protects the organization's policy from one editor."""
    due, source = effective_due(sla_due=SLA, due_on=date(2026, 12, 31))
    assert source == DUE_SOURCE_SLA
    assert due == SLA


def test_a_tie_resolves_to_the_policy() -> None:
    """Changes no verdict; keeps `due_source` stable rather than flipping on a
    value that means the same deadline."""
    same_day_end = datetime(2026, 9, 8, tzinfo=UTC)
    _due, source = effective_due(sla_due=same_day_end, due_on=date(2026, 9, 7))
    assert source == DUE_SOURCE_SLA


def test_a_written_date_expires_at_the_end_of_its_day() -> None:
    """A deadline of the 7th is met by work finished during the 7th."""
    assert manual_due_instant(date(2026, 9, 7)) == datetime(2026, 9, 8, tzinfo=UTC)
    assert manual_due_instant(None) is None


# --- what the author is told ------------------------------------------------


def test_a_later_day_is_reported_as_ignored() -> None:
    assert manual_due_is_ignored(sla_due=SLA, due_on=date(2026, 9, 8)) is True


def test_the_same_calendar_day_is_not_reported() -> None:
    """The verdict compares instants, so 7 Sep loses to 7 Sep 14:23. But the
    screen shows that policy deadline as "7 Sep", so telling somebody who typed
    7 Sep that their date was ignored would report a disagreement they cannot
    see. They asked for the day the policy requires and they got it."""
    assert manual_due_is_ignored(sla_due=SLA, due_on=date(2026, 9, 7)) is False
    # ...and the verdict is still the policy's, which is the point of keeping
    # the two comparisons on different axes deliberately.
    assert effective_due(sla_due=SLA, due_on=date(2026, 9, 7))[1] == DUE_SOURCE_SLA


def test_an_earlier_day_is_not_reported() -> None:
    assert manual_due_is_ignored(sla_due=SLA, due_on=date(2026, 9, 6)) is False


def test_nothing_is_reported_when_there_is_no_policy_to_lose_to() -> None:
    assert manual_due_is_ignored(sla_due=None, due_on=date(2099, 1, 1)) is False


def test_nothing_is_reported_when_no_date_was_written() -> None:
    assert manual_due_is_ignored(sla_due=SLA, due_on=None) is False


# --- vocabulary -------------------------------------------------------------


def test_the_wire_vocabulary_matches_the_rule() -> None:
    """`DueSource` on the schema is a second spelling of these values. Import
    both and compare, rather than trusting them to be edited together."""
    from typing import get_args

    from schemas.vulnerability_detail import DueSource

    assert set(get_args(DueSource)) == set(DUE_SOURCE_VALUES)


@pytest.mark.parametrize("source", DUE_SOURCE_VALUES)
def test_every_declared_source_is_reachable(source: str) -> None:
    """A vocabulary value nothing can produce is a lie in the schema."""
    produced = {
        effective_due(sla_due=SLA, due_on=date(2026, 9, 1))[1],
        effective_due(sla_due=SLA, due_on=None)[1],
    }
    assert source in produced
