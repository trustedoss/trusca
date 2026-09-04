# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""A failed run followed by a successful one leaves both rows behind.

That sequence is the whole reason this table exists. A single-execution test
passes just as happily against a design that overwrites one row per task, and
overwriting is exactly what the sync-state tables already do, which is the
gap being closed. The retention sweep is checked here too, for the same
reason: a delete is not observable from unit tests over the recorder.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from models.task_run import TaskRun
from services import task_run_recorder as rec
from tests._db_required import migrate_to_head


def _sync_url() -> str:
    """The async URL rewritten for the sync driver the image actually ships.

    psycopg2, not psycopg: the requirements pin ``psycopg2-binary`` and the
    bare ``psycopg`` dialect resolves to a package that is not installed.
    """
    return os.environ["DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql+psycopg2://"
    )


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(_sync_url(), future=True)
    factory = sessionmaker(bind=engine, future=True)
    with factory() as s:
        yield s
    engine.dispose()


@pytest.fixture
def task_id() -> str:
    """A Celery id no other test shares, so rows never collide."""
    return uuid.uuid4().hex[:32]


def _rows(session: Session, task_id: str) -> list[TaskRun]:
    session.expire_all()
    return list(
        session.scalars(
            select(TaskRun)
            .where(TaskRun.celery_task_id == task_id)
            .order_by(TaskRun.attempt)
        )
    )


def test_a_failure_then_a_success_leaves_two_rows(
    session: Session, task_id: str
) -> None:
    """The sequence the design exists for.

    A counter would say "2 attempts, ended fine" and lose that the first one
    timed out. Two rows keep both stories, and the second attempt's success
    does not erase the first attempt's cause.
    """
    rec.record_start(
        task_name="trustedoss.test_probe",
        celery_task_id=task_id,
        request_id="req-x",
        attempt=1,
    )
    rec.record_finish(
        celery_task_id=task_id,
        attempt=1,
        outcome="failed",
        error=TimeoutError("upstream did not answer"),
    )
    rec.record_start(
        task_name="trustedoss.test_probe",
        celery_task_id=task_id,
        request_id="req-x",
        attempt=2,
    )
    rec.record_finish(
        celery_task_id=task_id,
        attempt=2,
        outcome="succeeded",
        retval={"detail_code": "probe.ok", "detail_params": {"listed": 3}},
    )

    first, second = _rows(session, task_id)

    assert (first.attempt, first.outcome) == (1, "failed")
    assert "TimeoutError" in (first.error or "")
    assert (second.attempt, second.outcome) == (2, "succeeded")
    assert second.result == {"detail_code": "probe.ok", "detail_params": {"listed": 3}}
    assert second.error is None


def test_a_skip_is_recorded_as_a_skip_not_a_success(
    session: Session, task_id: str
) -> None:
    """Celery reports SUCCESS for a task that returned without doing anything.

    Storing that verbatim would make a feed that has been unreachable for a
    week look like seven healthy runs.
    """
    rec.record_start(
        task_name="trustedoss.test_probe",
        celery_task_id=task_id,
        request_id=None,
        attempt=1,
    )
    rec.record_finish(
        celery_task_id=task_id,
        attempt=1,
        outcome="succeeded",
        retval={"skipped_reason": "feed_unavailable"},
    )

    (row,) = _rows(session, task_id)

    assert row.outcome == "skipped"
    assert row.skipped_reason == "feed_unavailable"


def test_an_unfinished_run_stays_open(session: Session, task_id: str) -> None:
    """A worker killed mid-task never reaches the closing handler.

    The open row is the only trace that death leaves, so it must not be
    mistaken for a run that completed.
    """
    rec.record_start(
        task_name="trustedoss.test_probe",
        celery_task_id=task_id,
        request_id=None,
        attempt=1,
    )

    (row,) = _rows(session, task_id)

    assert row.finished_at is None
    assert row.outcome is None


def test_closing_twice_does_not_reopen_a_finished_row(
    session: Session, task_id: str
) -> None:
    """Celery can fire postrun more than once in some failure modes.

    The update is scoped to rows that are still open, so a second call finds
    nothing and the first verdict stands.
    """
    rec.record_start(
        task_name="trustedoss.test_probe",
        celery_task_id=task_id,
        request_id=None,
        attempt=1,
    )
    rec.record_finish(celery_task_id=task_id, attempt=1, outcome="failed")
    rec.record_finish(celery_task_id=task_id, attempt=1, outcome="succeeded")

    (row,) = _rows(session, task_id)

    assert row.outcome == "failed"


def test_the_retention_sweep_deletes_aged_rows_only(
    session: Session, task_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old history goes; recent history stays.

    Unlike the audit log this table is diagnostic rather than evidential, so
    it is swept. The sweep must not take the rows an operator is currently
    looking at.
    """
    from tasks.operational_retention import operational_retention_task

    old_id = f"{task_id}-old"
    session.execute(
        text(
            "INSERT INTO task_runs (task_name, celery_task_id, attempt, started_at)"
            " VALUES (:n, :c, 1, :t)"
        ),
        {
            "n": "trustedoss.test_probe",
            "c": old_id,
            "t": datetime.now(UTC) - timedelta(days=200),
        },
    )
    session.commit()

    rec.record_start(
        task_name="trustedoss.test_probe",
        celery_task_id=task_id,
        request_id=None,
        attempt=1,
    )

    monkeypatch.setenv("TASK_RUN_RETENTION_DAYS", "90")
    operational_retention_task()

    assert _rows(session, old_id) == []
    assert len(_rows(session, task_id)) == 1
