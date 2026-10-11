"""Skipped and degraded scan stages as metrics (#496), against a real database.

The aggregation runs in Postgres (``jsonb_each`` over ``scans.metadata``), so a
stub session would test nothing. Scans are seeded with the records the
pipelines write, and the published values are compared before and after, so
rows other tests left in the shared database do not matter.

What is pinned: the window (a scan that finished two days ago and a scan still
running are not counted), the labels (values outside the vocabulary are
counted under ``unknown``, not dropped), and the sum for licence lookups (a
scan that skipped 300 lookups weighs 300).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._db_required import migrate_to_head
from tests._helpers import make_organization, make_project, make_scan, make_team

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


_SAMPLE = re.compile(r"^(trusca_[a-z0-9_]+)(?:\{([^}]*)\})? (\S+)$")


def _samples(document: str) -> dict[tuple[str, frozenset[tuple[str, str]]], float]:
    out: dict[tuple[str, frozenset[tuple[str, str]]], float] = {}
    for line in document.splitlines():
        match = _SAMPLE.match(line)
        if not match:
            continue
        name, raw_labels, value = match.groups()
        labels = frozenset(
            tuple(part.split("=", 1)) for part in (raw_labels or "").split(",") if part
        )
        out[(name, frozenset((k, v.strip('"')) for k, v in labels))] = float(value)
    return out


def _value(samples: dict[Any, float], name: str, **labels: str) -> float:
    return samples.get((name, frozenset(labels.items())), 0.0)


async def _render(session: AsyncSession) -> dict[Any, float]:
    from services.metrics_service import render_metrics

    return _samples(await render_metrics(session))


async def _seed(
    session: AsyncSession,
    *,
    status: str,
    metadata: dict[str, Any],
    finished_ago: timedelta | None,
) -> None:
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    project = await make_project(session, team=team)
    scan = await make_scan(session, project=project, status=status, scan_metadata=metadata)
    if finished_ago is not None:
        scan.completed_at = datetime.now(UTC) - finished_ago
    await session.commit()


async def test_degraded_stages_are_counted_per_stage_and_reason_inside_the_window(
    db_session: AsyncSession,
) -> None:
    name = "trusca_scan_stages_degraded_24h"
    before = await _render(db_session)

    recent = timedelta(hours=1)
    await _seed(
        db_session,
        status="succeeded",
        metadata={
            "stage_outcomes": {
                "generate": {"reason": "fallback"},
                "enrich": {"reason": "unavailable"},
                # Written by some other release: kept, under unknown.
                "teleport": {"reason": "failed"},
                "sign": {"reason": "cosmic_rays"},
                "prep": "not an object",
            }
        },
        finished_ago=recent,
    )
    await _seed(
        db_session,
        status="failed",
        metadata={"stage_outcomes": {"generate": {"reason": "fallback"}}},
        finished_ago=recent,
    )
    # Outside the window, and not finished: neither counts.
    await _seed(
        db_session,
        status="succeeded",
        metadata={"stage_outcomes": {"generate": {"reason": "fallback"}}},
        finished_ago=timedelta(days=2),
    )
    await _seed(
        db_session,
        status="running",
        metadata={"stage_outcomes": {"generate": {"reason": "fallback"}}},
        finished_ago=None,
    )
    # A record that is not an object counts as nothing degraded.
    await _seed(
        db_session, status="succeeded", metadata={"stage_outcomes": ["x"]}, finished_ago=recent
    )

    after = await _render(db_session)

    def delta(**labels: str) -> float:
        return _value(after, name, **labels) - _value(before, name, **labels)

    assert delta(stage="generate", reason="fallback") == 2
    assert delta(stage="enrich", reason="unavailable") == 1
    assert delta(stage="unknown", reason="failed") == 1
    assert delta(stage="sign", reason="unknown") == 1
    assert delta(stage="prep", reason="unknown") == 1

    finished = "trusca_scans_finished_24h"
    assert (
        _value(after, finished, status="succeeded") - _value(before, finished, status="succeeded")
        == 2
    )
    assert _value(after, finished, status="failed") - _value(before, finished, status="failed") == 1


async def test_scancode_skips_and_license_lookup_gaps_are_published_by_reason(
    db_session: AsyncSession,
) -> None:
    scancode = "trusca_scan_scancode_skipped_24h"
    lookups = "trusca_scan_license_lookups_skipped_24h"
    before = await _render(db_session)
    recent = timedelta(minutes=30)

    await _seed(
        db_session,
        status="succeeded",
        metadata={
            "scancode_skipped": "timeout",
            "license_enrichment": {
                "not_looked_up": 300,
                "not_looked_up_reason": "budget_exhausted",
            },
        },
        finished_ago=recent,
    )
    await _seed(
        db_session,
        status="succeeded",
        metadata={
            "scancode_skipped": "timeout",
            "license_enrichment": {"not_looked_up": 7, "not_looked_up_reason": "budget_exhausted"},
        },
        finished_ago=recent,
    )
    await _seed(
        db_session,
        status="succeeded",
        metadata={
            "scancode_skipped": "gremlins",
            "license_enrichment": {"not_looked_up": "many", "not_looked_up_reason": "breaker_open"},
        },
        finished_ago=recent,
    )
    await _seed(
        db_session,
        status="succeeded",
        metadata={"license_enrichment": {"not_looked_up": 4}},
        finished_ago=recent,
    )
    await _seed(
        db_session,
        status="succeeded",
        metadata={
            "scancode_skipped": "timeout",
            "license_enrichment": {"not_looked_up": 50, "not_looked_up_reason": "budget_exhausted"},
        },
        finished_ago=timedelta(days=3),
    )

    after = await _render(db_session)

    def delta(name: str, **labels: str) -> float:
        return _value(after, name, **labels) - _value(before, name, **labels)

    assert delta(scancode, reason="timeout") == 2
    assert delta(scancode, reason="unknown") == 1
    assert delta(lookups, reason="budget_exhausted") == 307
    # A count that is not a number adds nothing; the reason is still listed.
    assert delta(lookups, reason="breaker_open") == 0
    assert delta(lookups, reason="unknown") == 4
    # Every known reason is published even at zero, so absence of data and
    # "nothing skipped" read differently.
    for reason in ("not_installed", "failed", "timeout", "too_large"):
        assert (scancode, frozenset({("reason", reason)})) in after
    for reason in ("budget_exhausted", "breaker_open", "both"):
        assert (lookups, frozenset({("reason", reason)})) in after
