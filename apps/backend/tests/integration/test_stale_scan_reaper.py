# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Integration tests for the stale running-scan reaper.

Runs against the real PostgreSQL (CLAUDE.md core rule #1): the reaper's whole
selection is one SQL predicate over ``GREATEST(COALESCE(started_at,
created_at), updated_at)``, and the interesting part is which rows that
predicate does and does not pick. Against a mock it would be testing the mock.

The bug: a worker SIGKILLed or restarted mid-scan leaves its row saying
``running`` forever, which holds the project's ``ix_scans_project_active``
slot (that project can never be scanned again) and one of the team's
concurrency slots.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def db_session() -> Iterator[Session]:
    from core.config import database_url

    sync_url = database_url().replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url, pool_pre_ping=True, future=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _make_project(session: Session) -> uuid.UUID:
    """Minimal org → team → project chain, via SQL to stay independent of the
    async model helpers the rest of the suite uses."""
    suffix = uuid.uuid4().hex[:10]
    org_id = uuid.uuid4()
    team_id = uuid.uuid4()
    project_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO organizations (id, name, slug, created_at, updated_at) "
            "VALUES (:id, :name, :slug, now(), now())"
        ),
        {"id": org_id, "name": f"reaper-org-{suffix}", "slug": f"reaper-org-{suffix}"},
    )
    session.execute(
        text(
            "INSERT INTO teams (id, organization_id, name, slug, created_at, "
            "updated_at) VALUES (:id, :org, :name, :slug, now(), now())"
        ),
        {
            "id": team_id,
            "org": org_id,
            "name": f"reaper-team-{suffix}",
            "slug": f"reaper-team-{suffix}",
        },
    )
    session.execute(
        text(
            "INSERT INTO projects (id, team_id, name, slug, visibility, "
            "created_at, updated_at) VALUES (:id, :team, :name, :slug, 'team', "
            "now(), now())"
        ),
        {
            "id": project_id,
            "team": team_id,
            "name": f"reaper-project-{suffix}",
            "slug": f"reaper-project-{suffix}",
        },
    )
    session.commit()
    return project_id


def _make_scan(
    session: Session,
    *,
    project_id: uuid.UUID,
    status: str,
    age_seconds: float,
) -> uuid.UUID:
    """A scan whose liveness stamps are ``age_seconds`` in the past."""
    scan_id = uuid.uuid4()
    stamp = datetime.now(UTC) - timedelta(seconds=age_seconds)
    session.execute(
        # The JSONB column is named ``metadata`` in the DB; the ORM renames the
        # Python attribute to ``scan_metadata`` only because the original
        # clashes with DeclarativeBase.metadata (models/scan.py).
        text(
            "INSERT INTO scans (id, project_id, kind, status, progress_percent, "
            "metadata, created_at, updated_at, started_at) "
            "VALUES (:id, :project, 'source', :status, 10, '{}'::jsonb, "
            ":stamp, :stamp, :stamp)"
        ),
        {"id": scan_id, "project": project_id, "status": status, "stamp": stamp},
    )
    session.commit()
    return scan_id


def _status_of(session: Session, scan_id: uuid.UUID) -> str:
    session.expire_all()
    row = session.execute(
        text("SELECT status FROM scans WHERE id = :id"), {"id": scan_id}
    ).one()
    return str(row[0])


def _cutoff_seconds() -> int:
    from core.config import (
        scan_hard_time_limit_seconds,
        stale_running_scan_grace_seconds,
    )

    return scan_hard_time_limit_seconds() + stale_running_scan_grace_seconds()


def test_a_scan_older_than_any_live_task_is_marked_failed(
    db_session: Session,
) -> None:
    """The observed case: the container runtime restarted mid-scan.

    Nothing could still be running after the hard time limit, so a row this
    old is a row whose worker is gone.
    """
    from tasks.stale_scan_reaper import stale_scan_reaper_task

    project_id = _make_project(db_session)
    scan_id = _make_scan(
        db_session,
        project_id=project_id,
        status="running",
        age_seconds=_cutoff_seconds() + 600,
    )

    out = stale_scan_reaper_task()

    assert str(scan_id) in out["reaped"]
    assert _status_of(db_session, scan_id) == "failed"


def test_the_failure_says_the_worker_stopped_rather_than_blaming_the_scan(
    db_session: Session,
) -> None:
    """The operator retrying this needs to know nothing was wrong with it."""
    from tasks.stale_scan_reaper import stale_scan_reaper_task

    project_id = _make_project(db_session)
    scan_id = _make_scan(
        db_session,
        project_id=project_id,
        status="running",
        age_seconds=_cutoff_seconds() + 600,
    )

    stale_scan_reaper_task()

    db_session.expire_all()
    message = db_session.execute(
        text("SELECT error_message FROM scans WHERE id = :id"), {"id": scan_id}
    ).scalar_one()
    assert "worker" in message
    assert "retried" in message


def test_a_long_but_live_scan_is_left_alone(db_session: Session) -> None:
    """Inside the hard time limit the task may still be working. Reaping here
    would fail a scan that is about to succeed."""
    from tasks.stale_scan_reaper import stale_scan_reaper_task

    project_id = _make_project(db_session)
    scan_id = _make_scan(
        db_session,
        project_id=project_id,
        status="running",
        age_seconds=max(0, _cutoff_seconds() - 600),
    )

    out = stale_scan_reaper_task()

    assert str(scan_id) not in out["reaped"]
    assert _status_of(db_session, scan_id) == "running"


def test_a_queued_scan_is_never_reaped_by_the_running_pass(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Queue wait is unbounded under backlog: sitting for hours is a backlog,
    not a fault.

    ER11 gave `queued` its own pass, which decides on what the broker is
    holding rather than on age. The running pass must still never touch these
    rows, and age alone must still never reclaim one, so the broker is pinned
    here as still holding the message.
    """
    from tasks import stale_scan_reaper
    from tasks.stale_scan_reaper import stale_scan_reaper_task

    project_id = _make_project(db_session)
    task_id = str(uuid.uuid4())
    scan_id = _make_queued_scan(
        db_session,
        project_id=project_id,
        age_seconds=_cutoff_seconds() * 5,
        celery_task_id=task_id,
    )

    monkeypatch.setattr(
        stale_scan_reaper, "broker_known_task_ids", lambda: {task_id}
    )
    out = stale_scan_reaper_task()

    assert str(scan_id) not in out["reaped"]
    assert str(scan_id) not in out["orphaned_queued"]
    assert _status_of(db_session, scan_id) == "queued"


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
def test_a_terminal_scan_is_untouched(db_session: Session, terminal: str) -> None:
    """An old succeeded scan is the normal state of a healthy deployment."""
    from tasks.stale_scan_reaper import stale_scan_reaper_task

    project_id = _make_project(db_session)
    scan_id = _make_scan(
        db_session,
        project_id=project_id,
        status=terminal,
        age_seconds=_cutoff_seconds() * 5,
    )

    out = stale_scan_reaper_task()

    assert str(scan_id) not in out["reaped"]
    assert _status_of(db_session, scan_id) == terminal


def test_a_recent_progress_write_counts_as_alive(db_session: Session) -> None:
    """``updated_at`` moving proves the task was alive then, even if it started
    long ago. Taking the later stamp is what keeps a slow scan safe."""
    from tasks.stale_scan_reaper import stale_scan_reaper_task

    project_id = _make_project(db_session)
    scan_id = _make_scan(
        db_session,
        project_id=project_id,
        status="running",
        age_seconds=_cutoff_seconds() + 600,
    )
    # A progress step landed a moment ago: started long ago, still working.
    db_session.execute(
        text("UPDATE scans SET updated_at = now() WHERE id = :id"), {"id": scan_id}
    )
    db_session.commit()

    out = stale_scan_reaper_task()

    assert str(scan_id) not in out["reaped"]
    assert _status_of(db_session, scan_id) == "running"


def test_reaping_frees_the_project_active_scan_slot(db_session: Session) -> None:
    """The point of the whole task.

    ``ix_scans_project_active`` allows one queued/running scan per (project,
    ref). While the dead row holds it, every retry of that project is a 409 no
    operator can clear from the UI.
    """
    from tasks.stale_scan_reaper import stale_scan_reaper_task

    project_id = _make_project(db_session)
    _make_scan(
        db_session,
        project_id=project_id,
        status="running",
        age_seconds=_cutoff_seconds() + 600,
    )

    stale_scan_reaper_task()

    # The slot is free: a fresh queued scan for the same project now inserts.
    replacement = _make_scan(
        db_session, project_id=project_id, status="queued", age_seconds=0
    )
    assert _status_of(db_session, replacement) == "queued"


# ---------------------------------------------------------------------------
# ER11 - orphaned `queued` scans
#
# The reaper decides these by asking the broker, not by age, so every test
# below pins what the broker claims to be holding. Each one runs the real task
# rather than the helper, because the failure this guards against is a pass
# that selects rows correctly and never commits them.
# ---------------------------------------------------------------------------


def _make_queued_scan(
    session: Session,
    *,
    project_id: uuid.UUID,
    age_seconds: float,
    celery_task_id: str | None,
) -> uuid.UUID:
    scan_id = uuid.uuid4()
    stamp = datetime.now(UTC) - timedelta(seconds=age_seconds)
    session.execute(
        text(
            "INSERT INTO scans (id, project_id, kind, status, progress_percent, "
            "metadata, created_at, updated_at, celery_task_id) "
            "VALUES (:id, :project, 'source', 'queued', 0, '{}'::jsonb, "
            ":stamp, :stamp, :task_id)"
        ),
        {
            "id": scan_id,
            "project": project_id,
            "stamp": stamp,
            "task_id": celery_task_id,
        },
    )
    session.commit()
    return scan_id


def _queued_grace_seconds() -> int:
    from core.config import stale_queued_scan_grace_seconds

    return stale_queued_scan_grace_seconds()


def test_a_queued_scan_the_broker_still_holds_is_left_alone(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deep backlog is not a fault. Age alone must never reclaim a scan."""
    from tasks import stale_scan_reaper
    from tasks.stale_scan_reaper import stale_scan_reaper_task

    project_id = _make_project(db_session)
    task_id = str(uuid.uuid4())
    scan_id = _make_queued_scan(
        db_session,
        project_id=project_id,
        age_seconds=_queued_grace_seconds() + 86_400,
        celery_task_id=task_id,
    )

    monkeypatch.setattr(
        stale_scan_reaper, "broker_known_task_ids", lambda: {task_id}
    )
    out = stale_scan_reaper_task()

    assert str(scan_id) not in out["orphaned_queued"]
    assert _status_of(db_session, scan_id) == "queued"


def test_a_queued_scan_the_broker_lost_is_marked_failed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The observed case: Redis restarted and the message went with it."""
    from tasks import stale_scan_reaper
    from tasks.stale_scan_reaper import stale_scan_reaper_task

    project_id = _make_project(db_session)
    scan_id = _make_queued_scan(
        db_session,
        project_id=project_id,
        age_seconds=_queued_grace_seconds() + 600,
        celery_task_id=str(uuid.uuid4()),
    )

    # The broker is reachable and holding nothing, which is what licenses a
    # reap. An empty set is deliberately not the same as None.
    monkeypatch.setattr(stale_scan_reaper, "broker_known_task_ids", lambda: set())
    out = stale_scan_reaper_task()

    assert str(scan_id) in out["orphaned_queued"]
    assert _status_of(db_session, scan_id) == "failed"


def test_a_recently_queued_scan_is_left_alone(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The publish window: the row is committed before the task is published,
    so the broker legitimately knows nothing about it yet."""
    from tasks import stale_scan_reaper
    from tasks.stale_scan_reaper import stale_scan_reaper_task

    project_id = _make_project(db_session)
    scan_id = _make_queued_scan(
        db_session,
        project_id=project_id,
        age_seconds=0,
        celery_task_id=str(uuid.uuid4()),
    )

    monkeypatch.setattr(stale_scan_reaper, "broker_known_task_ids", lambda: set())
    out = stale_scan_reaper_task()

    assert str(scan_id) not in out["orphaned_queued"]
    assert _status_of(db_session, scan_id) == "queued"


def test_an_unreadable_broker_reaps_nothing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Could not look" must never be read as "nothing is out there"."""
    from tasks import stale_scan_reaper
    from tasks.stale_scan_reaper import stale_scan_reaper_task

    project_id = _make_project(db_session)
    scan_id = _make_queued_scan(
        db_session,
        project_id=project_id,
        age_seconds=_queued_grace_seconds() + 86_400,
        celery_task_id=str(uuid.uuid4()),
    )

    monkeypatch.setattr(stale_scan_reaper, "broker_known_task_ids", lambda: None)
    out = stale_scan_reaper_task()

    assert out["orphaned_queued"] == []
    assert _status_of(db_session, scan_id) == "queued"


def test_a_queued_scan_that_was_never_published_is_marked_failed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No task id means no message was ever published, so nothing will run it."""
    from tasks import stale_scan_reaper
    from tasks.stale_scan_reaper import stale_scan_reaper_task

    project_id = _make_project(db_session)
    scan_id = _make_queued_scan(
        db_session,
        project_id=project_id,
        age_seconds=_queued_grace_seconds() + 600,
        celery_task_id=None,
    )

    monkeypatch.setattr(stale_scan_reaper, "broker_known_task_ids", lambda: set())
    out = stale_scan_reaper_task()

    assert str(scan_id) in out["orphaned_queued"]
    assert _status_of(db_session, scan_id) == "failed"


def test_a_queued_scan_without_a_task_id_survives_a_busy_broker(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The crash window between publishing and recording the id.

    scan_service commits the row, publishes, then writes the id back. A crash
    in between leaves a NULL id on a row whose message may be live, and that
    message is indistinguishable from any other id in the inventory. So while
    the broker is holding anything at all, a NULL row must be left alone.
    """
    from tasks import stale_scan_reaper
    from tasks.stale_scan_reaper import stale_scan_reaper_task

    project_id = _make_project(db_session)
    scan_id = _make_queued_scan(
        db_session,
        project_id=project_id,
        age_seconds=_queued_grace_seconds() + 86_400,
        celery_task_id=None,
    )

    monkeypatch.setattr(
        stale_scan_reaper,
        "broker_known_task_ids",
        lambda: {str(uuid.uuid4())},
    )
    out = stale_scan_reaper_task()

    assert str(scan_id) not in out["orphaned_queued"]
    assert _status_of(db_session, scan_id) == "queued"
