"""
The D7 scheduled-scan poller (N18).

The property worth pinning is the one in the plan's own words: no schedule
anywhere means no automatic scan. After that, the poller must refuse a scan
for exactly the same reasons the webhook path already does (an active scan on
the project, the team's concurrency cap); it is not allowed a weaker guard
sequence of its own, sync twin or not.

Uses the REAL sync Celery-side session (``core.db.sync_session_scope``), not
a hand-rolled fake: the query joins ``scan_schedules`` to ``projects``/
``teams`` twice (own row, org-default row) and the skip-on-conflict path
depends on ``ix_scans_project_active``, both of which only a real Postgres
proves.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.db import sync_session_scope
from models import Scan
from tests._db_required import migrate_to_head
from tests._helpers import make_organization, make_project, make_scan, make_team

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture(autouse=True)
async def _clean_slate() -> None:
    """Truncate before each test, not just seed fresh orgs per test.

    ``poll_due_schedules`` sweeps every project in the database by design;
    that is the point of a poller. A leftover ``scan_schedules`` row from an
    earlier test (created for a different org, but still "due" at whatever
    ``now_utc`` a later test happens to poll with) would silently count
    toward THIS test's assertions otherwise. Leftover organizations/teams/
    projects are harmless: with no schedule row pointing at them they are
    invisible to the due-targets query.
    """
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE scan_schedules, scans RESTART IDENTITY CASCADE"))
    await engine.dispose()


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(session: AsyncSession, **project_kwargs):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    project = await make_project(session, team=team, **project_kwargs)
    return org, team, project


async def _write_schedule(session: AsyncSession, *, org, project=None, **fields):
    from models import ScanSchedule

    row = ScanSchedule(
        organization_id=org.id,
        project_id=project.id if project is not None else None,
        **fields,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def _stub_enqueue(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def _fake_enqueue(scan) -> str:  # noqa: ANN001
        calls.append(str(scan.id))
        return f"celery-task-{scan.id}"

    monkeypatch.setattr("services.scan_service.enqueue_scan", _fake_enqueue)
    return calls


# ---------------------------------------------------------------------------
# No schedule: nothing fires
# ---------------------------------------------------------------------------


async def test_no_schedule_anywhere_enqueues_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks.scan_scheduler import poll_due_schedules

    _org, _team, _project = await _seed(db_session)
    _stub_enqueue(monkeypatch)

    result = poll_due_schedules(now_utc=datetime(2026, 1, 5, 9, 0, tzinfo=UTC))

    assert result["enqueued_count"] == 0


# ---------------------------------------------------------------------------
# Due: enqueues via the existing scan-service guard sequence
# ---------------------------------------------------------------------------


async def test_a_due_organization_default_enqueues_a_scan(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks.scan_scheduler import poll_due_schedules

    org, _team, project = await _seed(db_session)
    schedule = await _write_schedule(
        db_session, org=org, cadence="daily", hour=9, timezone="UTC"
    )
    calls = _stub_enqueue(monkeypatch)

    result = poll_due_schedules(now_utc=datetime(2026, 1, 5, 9, 5, tzinfo=UTC))

    assert result["enqueued_count"] == 1
    assert calls, "enqueue_scan must have been invoked via the existing scan-service path"

    with sync_session_scope() as session:
        scan = session.execute(
            select(Scan).where(Scan.project_id == project.id)
        ).scalar_one()
        assert scan.kind == "source"
        assert scan.status == "queued"
        assert scan.requested_by_user_id is None
        assert scan.scan_metadata.get("trigger") == "schedule"

        refreshed = session.get(type(schedule), schedule.id)
        assert refreshed is not None
        assert refreshed.last_triggered_at is not None


async def test_a_schedule_at_a_different_hour_does_not_fire(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks.scan_scheduler import poll_due_schedules

    org, _team, _project = await _seed(db_session)
    await _write_schedule(db_session, org=org, cadence="daily", hour=9, timezone="UTC")
    _stub_enqueue(monkeypatch)

    result = poll_due_schedules(now_utc=datetime(2026, 1, 5, 14, 0, tzinfo=UTC))

    assert result["enqueued_count"] == 0


async def test_a_timezone_shifts_which_utc_hour_is_due(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """09:00 in Seoul (UTC+9, no DST) is 00:00 UTC, not 09:00 UTC."""
    from tasks.scan_scheduler import poll_due_schedules

    org, _team, _project = await _seed(db_session)
    await _write_schedule(
        db_session, org=org, cadence="daily", hour=9, timezone="Asia/Seoul"
    )
    _stub_enqueue(monkeypatch)

    not_due = poll_due_schedules(now_utc=datetime(2026, 1, 5, 9, 5, tzinfo=UTC))
    assert not_due["enqueued_count"] == 0

    due = poll_due_schedules(now_utc=datetime(2026, 1, 5, 0, 5, tzinfo=UTC))
    assert due["enqueued_count"] == 1


async def test_an_already_fired_schedule_does_not_fire_twice_the_same_day(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks.scan_scheduler import poll_due_schedules

    org, _team, _project = await _seed(db_session)
    await _write_schedule(db_session, org=org, cadence="daily", hour=9, timezone="UTC")
    _stub_enqueue(monkeypatch)

    first = poll_due_schedules(now_utc=datetime(2026, 1, 5, 9, 0, tzinfo=UTC))
    second = poll_due_schedules(now_utc=datetime(2026, 1, 5, 9, 20, tzinfo=UTC))

    assert first["enqueued_count"] == 1
    assert second["enqueued_count"] == 0


async def test_an_archived_project_is_never_scheduled(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks.scan_scheduler import poll_due_schedules

    org, _team, _project = await _seed(db_session, archived=True)
    await _write_schedule(db_session, org=org, cadence="daily", hour=9, timezone="UTC")
    _stub_enqueue(monkeypatch)

    result = poll_due_schedules(now_utc=datetime(2026, 1, 5, 9, 0, tzinfo=UTC))

    assert result["enqueued_count"] == 0


async def test_an_active_scan_blocks_without_losing_the_days_run(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same refusal reason the webhook path uses (ix_scans_project_active).

    last_triggered_at must stay unset so a later tick, still inside the same
    due hour, retries once the blocking scan clears.
    """
    from tasks.scan_scheduler import poll_due_schedules

    org, _team, project = await _seed(db_session)
    schedule = await _write_schedule(
        db_session, org=org, cadence="daily", hour=9, timezone="UTC"
    )
    await make_scan(db_session, project=project, status="running")
    _stub_enqueue(monkeypatch)

    result = poll_due_schedules(now_utc=datetime(2026, 1, 5, 9, 0, tzinfo=UTC))

    assert result["enqueued_count"] == 0
    assert result["skipped_active_scan"] == 1
    with sync_session_scope() as session:
        refreshed = session.get(type(schedule), schedule.id)
        assert refreshed is not None
        assert refreshed.last_triggered_at is None


async def test_a_team_at_its_concurrency_cap_is_skipped(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks.scan_scheduler import poll_due_schedules

    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "1")
    org, team, project = await _seed(db_session)
    # A project-level schedule (not org-default) so the only due target is
    # `project`; a second, schedule-less project in the same team supplies
    # the active scan that fills the team's cap.
    await _write_schedule(
        db_session, org=org, project=project, cadence="daily", hour=9, timezone="UTC"
    )
    other_project = await make_project(db_session, team=team)
    await make_scan(db_session, project=other_project, status="queued")
    _stub_enqueue(monkeypatch)

    result = poll_due_schedules(now_utc=datetime(2026, 1, 5, 9, 0, tzinfo=UTC))

    assert result["enqueued_count"] == 0
    assert result["skipped_team_at_capacity"] == 1
