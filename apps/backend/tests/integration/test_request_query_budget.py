"""
Per-request SQL budget for the paths that later work will keep adding lookups to.

Why this file exists: policy is moving out of code and into rows, and every one
of those settings is a lookup on a request path. Each addition costs one or two
statements, which no single change is going to fail a review over, and the cost
that matters is the sum. Nothing in the suite could see that sum: the one query
counter we had watches a single enrichment service for N+1, so a policy read
added to authentication was free as far as CI was concerned.

These budgets are measured, not chosen. The number beside each path is what the
path executes today, plus headroom, so the test is quiet through ordinary work
and speaks up when a lookup lands on a shared path. The authenticated read is
the one to watch: it runs on every request in the product, so a statement added
there is multiplied by traffic, and the plan this file belongs to says raising
that ceiling is not a decision to make while implementing something else. Raise
it deliberately, in its own change, with the reason in the message.

A budget is an upper bound, so it cannot catch a query that got slower, only
one that got added. Wall-clock belongs to the load tests, which run outside CI.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event

from core.security import create_access_token
from models import User
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration

# Measured on a seeded project over repeated runs, which returned the same
# count every time, so the margin below is deliberate slack rather than cover
# for noise. It absorbs ordinary work (a column that needs another row loaded);
# it is not room to spend on new lookups.
#
#   authenticated read   6 statements measured, budget 8
#   gate evaluation     12 statements measured, budget 15
#   status transition   13 statements measured, budget 16
AUTHENTICATED_READ_BUDGET = 8
GATE_EVALUATION_BUDGET = 15
STATUS_TRANSITION_BUDGET = 16


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set: skipping DB-backed tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            "alembic upgrade head failed; query-budget tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


class _Counter:
    """Counts statements the request issues, and keeps them for the failure message."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    @property
    def count(self) -> int:
        return len(self.statements)

    def summary(self) -> str:
        return "\n".join(f"  {i + 1:2d}. {s[:110]}" for i, s in enumerate(self.statements))


@pytest.fixture
async def counting(client: AsyncClient) -> AsyncIterator[Iterator[_Counter]]:
    """Count SQL on the app's own engine, so the seeding above is not counted."""
    await _factory(client)
    app = client._transport.app  # type: ignore[attr-defined]
    engine = app.state.engine
    counter = _Counter()
    armed = False

    def _record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        if armed:
            counter.statements.append(" ".join(statement.split()))

    event.listen(engine.sync_engine, "before_cursor_execute", _record)

    def arm() -> _Counter:
        nonlocal armed
        armed = True
        return counter

    try:
        yield arm  # type: ignore[misc]
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record)


def _bearer_for(user: User) -> dict[str, str]:
    role = "super_admin" if user.is_superuser else None
    return {"Authorization": f"Bearer {create_access_token(subject=str(user.id), role=role)}"}


async def _seed_project_with_member(client: AsyncClient):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")
        project = await make_project(session, team=team)
        return team.id, user, project.id


async def _seed_succeeded_scan(client: AsyncClient, *, project_id: uuid.UUID) -> uuid.UUID:
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Project

        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one()
        scan = await make_scan(session, project=project, status="succeeded")
        return scan.id


async def _seed_open_finding(client: AsyncClient, *, scan_id: uuid.UUID) -> uuid.UUID:
    """One component with one open finding, the shape a transition acts on."""
    factory = await _factory(client)
    async with factory() as session:
        from models import (
            Component,
            ComponentVersion,
            ScanComponent,
            Vulnerability,
            VulnerabilityFinding,
        )

        suffix = unique_suffix()
        purl = f"pkg:npm/budget-{suffix}"
        component = Component(purl=purl, package_type="npm", name=f"budget-{suffix}")
        session.add(component)
        await session.commit()
        await session.refresh(component)

        version = ComponentVersion(
            component_id=component.id,
            version="1.0.0",
            purl_with_version=f"{purl}@1.0.0",
        )
        session.add(version)
        await session.commit()
        await session.refresh(version)

        session.add(
            ScanComponent(
                scan_id=scan_id, component_version_id=version.id, direct=True, raw_data={}
            )
        )
        vulnerability = Vulnerability(
            external_id=f"CVE-2024-{suffix}",
            source="NVD",
            severity="high",
            summary="query budget fixture",
        )
        session.add(vulnerability)
        await session.commit()
        await session.refresh(vulnerability)

        finding = VulnerabilityFinding(
            scan_id=scan_id,
            component_version_id=version.id,
            vulnerability_id=vulnerability.id,
            status="new",
        )
        session.add(finding)
        await session.commit()
        await session.refresh(finding)
        return finding.id


# ---------------------------------------------------------------------------
# The budgets
# ---------------------------------------------------------------------------


async def test_authenticated_read_stays_within_its_query_budget(client, counting) -> None:
    """The path every other request pays for: resolve the caller, then read.

    A settings lookup added to authentication is charged to all traffic, not to
    the feature that introduced it, which is why this ceiling is the strictest
    of the three.
    """
    _, user, _ = await _seed_project_with_member(client)
    headers = _bearer_for(user)

    counter = counting()
    response = await client.get("/v1/projects", headers=headers)

    assert response.status_code == 200, response.text
    assert counter.count <= AUTHENTICATED_READ_BUDGET, (
        f"the authenticated read issued {counter.count} statements, over the "
        f"budget of {AUTHENTICATED_READ_BUDGET}:\n{counter.summary()}"
    )


async def test_gate_evaluation_stays_within_its_query_budget(client, counting) -> None:
    """The build gate: CI calls it on every pipeline run, so its cost is repeated."""
    _, user, project_id = await _seed_project_with_member(client)
    await _seed_succeeded_scan(client, project_id=project_id)
    headers = _bearer_for(user)

    counter = counting()
    response = await client.get(f"/v1/projects/{project_id}/gate-result", headers=headers)

    assert response.status_code == 200, response.text
    assert counter.count <= GATE_EVALUATION_BUDGET, (
        f"gate evaluation issued {counter.count} statements, over the budget of "
        f"{GATE_EVALUATION_BUDGET}:\n{counter.summary()}"
    )


async def test_status_transition_stays_within_its_query_budget(client, counting) -> None:
    """One write with its authorization, audit and optimistic-lock reads."""
    _, user, project_id = await _seed_project_with_member(client)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)
    finding_id = await _seed_open_finding(client, scan_id=scan_id)
    headers = _bearer_for(user)

    counter = counting()
    response = await client.patch(
        f"/v1/vulnerability_findings/{finding_id}/status",
        headers=headers,
        json={
            "status": "analyzing",
            "justification": "measuring the per-request statement budget",
        },
    )

    assert response.status_code in (200, 204), response.text
    assert counter.count <= STATUS_TRANSITION_BUDGET, (
        f"the status transition issued {counter.count} statements, over the "
        f"budget of {STATUS_TRANSITION_BUDGET}:\n{counter.summary()}"
    )
