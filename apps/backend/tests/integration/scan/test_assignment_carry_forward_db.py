# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Assignee, deadline and ticket survive the row being replaced (ER68).

The product's guide promises this without qualification
(``docs-site/docs/user-guide/vulnerabilities.md``):

    A finding can carry who is fixing it, by when, and where the work is
    tracked.

Findings are per-scan rows, so that sentence only holds if the four columns
outlive the scan they were written against. Until this test existed they did
not. ``persist_trivy_findings`` carried the SLA clock and the analyst's verdict
forward and left these behind, so a rescan dropped them, and the rematch beat
DELETEs and re-inserts every succeeded scan's findings on a six-hour default
cadence, which meant an assignment made in the morning was gone by the
afternoon with nobody having done anything.

Hardening rule #5 (lifecycle sequences): a test that assigns and reads back
passes on a single write and says nothing about the replacement, so these drive
the two real sequences end to end through the same chokepoint the pipelines
use, exactly as the triage sibling does.

Hardening rule #7 (say what the assertion protects): the third sequence is the
one that is easy to leave out. Unassigning is a gesture the PATCH model exists
to support, and a carry-forward that skipped rows with nothing on them would
find the removed person on an older scan and put them back. That failure only
shows up in a sequence with three steps, and it is worse than the loss this
work fixes, because it would keep happening.

The in-place re-run path (``_reset_scan_for_rerun``) is deliberately not
covered: ``scan_source`` returns early on a scan already marked ``succeeded``,
and the vulnerability surface only reads a project's latest succeeded scan, so
there is no way to put an assignment on a row that path can reach.

Fixtures are the triage sibling's, imported rather than copied: the same
recorded report, the same component graph, the same realistic density (lodash
carries three CVEs across two ecosystems), so a carry-forward that painted the
project with one pair's values instead of resolving per pair fails here.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session, sessionmaker

from models import VulnerabilityFinding
from services.vulnerability_matching import (
    capture_assignment_state,
    persist_trivy_findings,
)
from tests._db_required import migrate_to_head

# Data helpers only. The two fixtures are defined below rather than imported:
# a fixture imported into a module whose tests take a parameter of the same
# name is a redefinition, and each sibling in this directory keeps its own
# copy for that reason.
from tests.integration.scan.test_triage_carry_forward_db import (
    _EXPECTED_FINDINGS,
    _findings,
    _pair_map,
    _seed_fixture_component_versions,
    _seed_project_with_scans,
    _trivy_report,
)

pytestmark = pytest.mark.integration

@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    """ER66's shared gate rather than a fourth private copy of it.

    Written with its own copy because the sibling this file borrows its
    fixtures from still has one, and copying the neighbour reproduced the
    behaviour ER66 exists to remove: a missing database or a broken migration
    turning into a skip, so the job that would have caught it exits green.
    """
    migrate_to_head()


@pytest.fixture
def sync_session() -> Iterator[Session]:
    from core.config import database_url_sync

    engine = create_engine(database_url_sync(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()

_DUE = date(2026, 12, 31)
_TICKET_URL = "https://tracker.example.com/browse/SEC-1"
_TICKET_KEY = "SEC-1"


def _assign(
    session: Session,
    row: VulnerabilityFinding,
    *,
    assignee_id: uuid.UUID | None,
    due_on: date | None,
    ticket_url: str | None,
    ticket_key: str | None,
) -> None:
    """Write the four columns the assignment PATCH writes, and nothing else."""
    row.assignee_user_id = assignee_id
    row.due_on = due_on
    row.ticket_url = ticket_url
    row.ticket_key = ticket_key
    session.commit()


def _assert_owned(
    row: VulnerabilityFinding, *, assignee_id: uuid.UUID, where: str
) -> None:
    assert row.assignee_user_id == assignee_id, f"assignee lost {where}"
    assert row.due_on == _DUE, f"deadline lost {where}"
    assert row.ticket_url == _TICKET_URL, f"ticket link lost {where}"
    assert row.ticket_key == _TICKET_KEY, f"ticket key lost {where}"


def _assert_unowned(row: VulnerabilityFinding, *, where: str) -> None:
    assert row.assignee_user_id is None, f"assignee reappeared {where}"
    assert row.due_on is None, f"deadline reappeared {where}"
    assert row.ticket_url is None, f"ticket link reappeared {where}"
    assert row.ticket_key is None, f"ticket key reappeared {where}"


# ---------------------------------------------------------------------------
# Sequence 1 - a re-scan keeps the assignment
# ---------------------------------------------------------------------------


def test_rescan_carries_assignment_forward(sync_session: Session) -> None:
    project_id, user_id, (scan1, scan2) = _seed_project_with_scans(2)
    _seed_fixture_component_versions(sync_session, [scan1, scan2])

    assert (
        persist_trivy_findings(
            sync_session, scan_uuid=scan1, trivy_report=_trivy_report()
        )
        == _EXPECTED_FINDINGS
    )
    sync_session.commit()

    scan1_rows = _findings(sync_session, scan1)
    # Assign exactly ONE of the five. The other four are the control: a
    # carry-forward that applied one pair's values across the project would
    # pass an all-or-nothing assertion and be badly wrong.
    owned_pair = sorted(_pair_map(scan1_rows))[0]
    _assign(
        sync_session,
        _pair_map(scan1_rows)[owned_pair],
        assignee_id=user_id,
        due_on=_DUE,
        ticket_url=_TICKET_URL,
        ticket_key=_TICKET_KEY,
    )
    # The write landed before anything is claimed about surviving it. A
    # precondition that is never asserted is how an assertion stops being able
    # to fail.
    _assert_owned(
        _pair_map(_findings(sync_session, scan1))[owned_pair],
        assignee_id=user_id,
        where="before the rescan",
    )

    assert (
        persist_trivy_findings(
            sync_session, scan_uuid=scan2, trivy_report=_trivy_report()
        )
        == _EXPECTED_FINDINGS
    )
    sync_session.commit()

    scan2_map = _pair_map(_findings(sync_session, scan2))
    _assert_owned(scan2_map[owned_pair], assignee_id=user_id, where="on the rescan")

    untouched = [scan2_map[p] for p in scan2_map if p != owned_pair]
    assert len(untouched) == _EXPECTED_FINDINGS - 1
    for row in untouched:
        _assert_unowned(row, where="on a finding nobody assigned")


# ---------------------------------------------------------------------------
# Sequence 2 - the rematch beat's wipe and replace keeps it
# ---------------------------------------------------------------------------


def test_rematch_wipe_and_replace_keeps_assignment(sync_session: Session) -> None:
    """The sequence ``tasks.vulnerability_rematch`` drives every six hours.

    Same scan row throughout: capture, DELETE, re-persist with the capture.
    This is the path that made the loss automatic rather than occasional.
    """
    project_id, user_id, (scan1,) = _seed_project_with_scans(1)
    _seed_fixture_component_versions(sync_session, [scan1])

    persist_trivy_findings(
        sync_session, scan_uuid=scan1, trivy_report=_trivy_report()
    )
    sync_session.commit()

    rows = _findings(sync_session, scan1)
    owned_pair = sorted(_pair_map(rows))[0]
    _assign(
        sync_session,
        _pair_map(rows)[owned_pair],
        assignee_id=user_id,
        due_on=_DUE,
        ticket_url=_TICKET_URL,
        ticket_key=_TICKET_KEY,
    )
    _assert_owned(
        _pair_map(_findings(sync_session, scan1))[owned_pair],
        assignee_id=user_id,
        where="before the rematch",
    )

    # One scan in the project, so nothing else can supply the values: they come
    # from the capture or they are gone.
    prior_assignment = capture_assignment_state(sync_session, scan_uuid=scan1)
    sync_session.execute(
        delete(VulnerabilityFinding).where(VulnerabilityFinding.scan_id == scan1)
    )
    sync_session.flush()
    assert _findings(sync_session, scan1) == []

    persist_trivy_findings(
        sync_session,
        scan_uuid=scan1,
        trivy_report=_trivy_report(),
        prior_assignment=prior_assignment,
    )
    sync_session.commit()

    _assert_owned(
        _pair_map(_findings(sync_session, scan1))[owned_pair],
        assignee_id=user_id,
        where="after the rematch",
    )


# ---------------------------------------------------------------------------
# Sequence 3 - removing an assignment sticks
# ---------------------------------------------------------------------------


def test_unassigning_is_not_undone_by_the_next_scan(sync_session: Session) -> None:
    """Assign on scan 1, remove on scan 2, and scan 3 must still find nobody.

    Three scans because two cannot tell the difference: with only the scan the
    removal happened on, "carry the latest row" and "carry the latest row that
    has somebody on it" give the same answer. The older scan is what makes the
    second rule wrong, and it is the rule the triage carry-forward uses.
    """
    project_id, user_id, (scan1, scan2, scan3) = _seed_project_with_scans(3)
    _seed_fixture_component_versions(sync_session, [scan1, scan2, scan3])

    persist_trivy_findings(
        sync_session, scan_uuid=scan1, trivy_report=_trivy_report()
    )
    sync_session.commit()
    owned_pair = sorted(_pair_map(_findings(sync_session, scan1)))[0]
    _assign(
        sync_session,
        _pair_map(_findings(sync_session, scan1))[owned_pair],
        assignee_id=user_id,
        due_on=_DUE,
        ticket_url=_TICKET_URL,
        ticket_key=_TICKET_KEY,
    )

    persist_trivy_findings(
        sync_session, scan_uuid=scan2, trivy_report=_trivy_report()
    )
    sync_session.commit()
    # It arrived on scan 2 by carry-forward. Asserted, so the removal below is
    # removing something rather than clearing a field that was already empty.
    _assert_owned(
        _pair_map(_findings(sync_session, scan2))[owned_pair],
        assignee_id=user_id,
        where="on the second scan",
    )

    _assign(
        sync_session,
        _pair_map(_findings(sync_session, scan2))[owned_pair],
        assignee_id=None,
        due_on=None,
        ticket_url=None,
        ticket_key=None,
    )

    persist_trivy_findings(
        sync_session, scan_uuid=scan3, trivy_report=_trivy_report()
    )
    sync_session.commit()

    _assert_unowned(
        _pair_map(_findings(sync_session, scan3))[owned_pair],
        where="after somebody removed it",
    )


# ---------------------------------------------------------------------------
# Sequence 4 - the removal survives the beat as well as the next scan
# ---------------------------------------------------------------------------


def test_rematch_does_not_restore_an_assignment_that_was_removed(
    sync_session: Session,
) -> None:
    """The same removal as sequence 3, met by the beat instead of a rescan.

    These are different code paths and only one of them is the capture. Here
    the removal is on the scan being replaced, so the snapshot is the only
    thing that can say the pair has nobody on it; a capture that skipped rows
    with nothing set would leave the pair uncovered and the older scan's
    assignee would come back. Sequence 3 cannot see that: it never captures.
    """
    project_id, user_id, (scan1, scan2) = _seed_project_with_scans(2)
    _seed_fixture_component_versions(sync_session, [scan1, scan2])

    persist_trivy_findings(
        sync_session, scan_uuid=scan1, trivy_report=_trivy_report()
    )
    sync_session.commit()
    owned_pair = sorted(_pair_map(_findings(sync_session, scan1)))[0]
    _assign(
        sync_session,
        _pair_map(_findings(sync_session, scan1))[owned_pair],
        assignee_id=user_id,
        due_on=_DUE,
        ticket_url=_TICKET_URL,
        ticket_key=_TICKET_KEY,
    )

    persist_trivy_findings(
        sync_session, scan_uuid=scan2, trivy_report=_trivy_report()
    )
    sync_session.commit()
    _assert_owned(
        _pair_map(_findings(sync_session, scan2))[owned_pair],
        assignee_id=user_id,
        where="on the second scan",
    )

    _assign(
        sync_session,
        _pair_map(_findings(sync_session, scan2))[owned_pair],
        assignee_id=None,
        due_on=None,
        ticket_url=None,
        ticket_key=None,
    )

    # The beat's sequence, on the scan the removal happened on.
    prior_assignment = capture_assignment_state(sync_session, scan_uuid=scan2)
    sync_session.execute(
        delete(VulnerabilityFinding).where(VulnerabilityFinding.scan_id == scan2)
    )
    sync_session.flush()
    persist_trivy_findings(
        sync_session,
        scan_uuid=scan2,
        trivy_report=_trivy_report(),
        prior_assignment=prior_assignment,
    )
    sync_session.commit()

    _assert_unowned(
        _pair_map(_findings(sync_session, scan2))[owned_pair],
        where="after a rematch followed the removal",
    )
