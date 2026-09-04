# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""What the task-run series say, and what they must not say.

The aggregates are computed in the database (``percentile_cont``, a grouped
count, a ``max``), so a unit test over a stubbed session would assert the
shape of a query rather than its result. These run against a real one.

Two properties are load-bearing. Runs that started and never reported an end
must appear rather than vanish, because that is the shape a killed worker
leaves and an absent series reads as "nothing wrong". And the window must
actually bound the aggregate, because a value computed over ninety days of
retained rows would hide the regression that started yesterday.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services.metrics_service import (
    _task_run_durations,
    _task_run_last_recorded,
    _task_run_outcomes,
)
from tests._db_required import migrate_to_head


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(os.environ["DATABASE_URL"], future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def task_name() -> str:
    """A task name no other test shares.

    The series group by name over the whole table, so a fixed name would pick
    up rows an earlier run of this same test left behind.
    """
    return f"trustedoss.metrics_probe_{uuid.uuid4().hex[:10]}"


async def _insert(
    session: AsyncSession,
    task_name: str,
    *,
    started_ago: timedelta,
    duration: timedelta | None,
    outcome: str | None,
    attempt: int = 1,
) -> None:
    started = datetime.now(UTC) - started_ago
    await session.execute(
        text(
            "INSERT INTO task_runs"
            " (task_name, celery_task_id, attempt, started_at, finished_at, outcome)"
            " VALUES (:n, :c, :a, :s, :f, :o)"
        ),
        {
            "n": task_name,
            "c": uuid.uuid4().hex[:32],
            "a": attempt,
            "s": started,
            "f": started + duration if duration else None,
            "o": outcome,
        },
    )
    await session.commit()


async def test_outcomes_are_counted_per_task_and_outcome(
    session: AsyncSession, task_name: str
) -> None:
    await _insert(
        session, task_name, started_ago=timedelta(hours=1),
        duration=timedelta(seconds=5), outcome="succeeded",
    )
    await _insert(
        session, task_name, started_ago=timedelta(hours=2),
        duration=timedelta(seconds=7), outcome="succeeded",
    )
    await _insert(
        session, task_name, started_ago=timedelta(hours=3),
        duration=timedelta(seconds=1), outcome="failed",
    )

    counts = await _task_run_outcomes(session)

    assert counts[(task_name, "succeeded")] == 2
    assert counts[(task_name, "failed")] == 1


async def test_an_unfinished_run_is_counted_as_running(
    session: AsyncSession, task_name: str
) -> None:
    """A NULL outcome is a state, not missing data.

    Dropping these would turn a worker that died mid-task into an absence,
    and an absence on a dashboard reads as nothing being wrong.
    """
    await _insert(
        session, task_name, started_ago=timedelta(minutes=30),
        duration=None, outcome=None,
    )

    counts = await _task_run_outcomes(session)

    assert counts[(task_name, "running")] == 1


async def test_the_window_excludes_older_runs(
    session: AsyncSession, task_name: str
) -> None:
    """Rows are kept for ninety days; the series covers one.

    Without the bound, a task that got slower yesterday would stay hidden
    behind three months of healthy history.
    """
    await _insert(
        session, task_name, started_ago=timedelta(hours=2),
        duration=timedelta(seconds=1), outcome="succeeded",
    )
    await _insert(
        session, task_name, started_ago=timedelta(days=3),
        duration=timedelta(seconds=1), outcome="succeeded",
    )

    counts = await _task_run_outcomes(session)

    assert counts[(task_name, "succeeded")] == 1


async def test_duration_quantiles_are_reported_per_task(
    session: AsyncSession, task_name: str
) -> None:
    for seconds in (1, 2, 3, 4, 100):
        await _insert(
            session, task_name, started_ago=timedelta(hours=1),
            duration=timedelta(seconds=seconds), outcome="succeeded",
        )

    durations = await _task_run_durations(session)

    assert durations[(task_name, "p50")] == pytest.approx(3.0)
    # p95 of this set sits in the tail, well above the median.
    assert durations[(task_name, "p95")] > durations[(task_name, "p50")]


async def test_unfinished_runs_do_not_enter_the_duration_quantiles(
    session: AsyncSession, task_name: str
) -> None:
    """An open row has no duration yet.

    Treating its NULL end as now would make a hung task look like a slow one,
    and the two need different responses.
    """
    await _insert(
        session, task_name, started_ago=timedelta(hours=1),
        duration=timedelta(seconds=10), outcome="succeeded",
    )
    await _insert(
        session, task_name, started_ago=timedelta(hours=6),
        duration=None, outcome=None,
    )

    durations = await _task_run_durations(session)

    assert durations[(task_name, "p50")] == pytest.approx(10.0)


async def test_last_recorded_tracks_the_newest_row(
    session: AsyncSession, task_name: str
) -> None:
    """The series that watches the recorder.

    The recorder swallows its own errors, so a missing grant or an unrun
    migration leaves tasks succeeding and the table empty. This value going
    stale is the only outward sign.

    The new row is placed a second past both the current maximum and the
    present moment, rather than a second before now. The series reports a
    maximum over the whole table and this suite shares its database with
    every other integration test, so a row recorded at ``now()`` by any of
    them is one this test cannot beat by reaching into the past.

    Taking the later of the two bounds closes that in both directions: past
    the maximum when the maximum is recent, and past ``now()`` when it is
    not. Only the first is needed while the suite runs serially, but the
    repository already anticipates parallel runs (see the ``serial`` marker
    in pyproject), and under those the second is what holds.
    """
    before = await _task_run_last_recorded(session)

    now = datetime.now(UTC)
    newest = max(datetime.fromtimestamp(before, tz=UTC), now) if before else now
    await _insert(
        session,
        task_name,
        started_ago=datetime.now(UTC) - (newest + timedelta(seconds=1)),
        duration=timedelta(seconds=1),
        outcome="succeeded",
    )

    after = await _task_run_last_recorded(session)

    assert after > before
