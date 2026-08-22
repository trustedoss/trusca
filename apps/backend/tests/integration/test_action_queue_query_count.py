# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The action queue's policy axes must not re-introduce a query per project.

``services/action_queue_service.py`` used to pay one query per project for
the dynamic forbidden-licence recount, and a second one for the malicious
waiver recount, on any team that runs an enabled licence policy: the N+1 the
concurrency-scaling plan (W5) documents. Both axes now batch every affected
scan into one query regardless of team size.

A parity test (``test_action_queue_gate_parity.py``) proves the *values* are
unchanged. It cannot prove the *query count* stays flat, because a query
counter and a project-count knob are exactly what parity assertions do not
carry. This file adds that knob: build the same policy-enabled team at two
sizes and assert the statement count does not grow between them. If either
axis regresses to a query per project, growing the team from one project to
six would nearly double one of these counts; equality is the strict form of
that assertion.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import (
    make_organization,
    make_project,
    make_scan,
    make_team,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration

T = TypeVar("T")


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set: skip action-queue query-count tests")
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
        pytest.skip(f"alembic upgrade head failed:\n{result.stderr}")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _count_statements(
    session: AsyncSession, call: Callable[[], Awaitable[T]]
) -> tuple[T, int]:
    """Run ``call`` and return its result plus the number of statements it issued."""
    # ``get_bind()`` on an AsyncSession proxies through to the underlying sync
    # Session and returns the plain (sync) ``Engine`` the async engine wraps,
    # not an ``AsyncEngine``, so the event goes on it directly, unlike the
    # ``engine.sync_engine`` indirection used where the app's own AsyncEngine
    # is already in hand (see ``test_request_query_budget.py``).
    engine = session.get_bind()
    counted = 0

    def _record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        nonlocal counted
        counted += 1

    event.listen(engine, "before_cursor_execute", _record)
    try:
        result = await call()
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    return result, counted


async def _enable_policy(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    team_id: uuid.UUID,
    category_overrides: dict | None = None,
    malicious_exceptions: list | None = None,
) -> None:
    from models import LicensePolicy

    session.add(
        LicensePolicy(
            organization_id=organization_id,
            team_id=team_id,
            name="query-count policy",
            enabled=True,
            category_overrides=category_overrides or {},
            license_exceptions=[],
            malicious_exceptions=malicious_exceptions or [],
            unknown_license_category="conditional",
        )
    )
    await session.commit()


async def _component_version(session: AsyncSession) -> uuid.UUID:
    from models import Component, ComponentVersion

    suffix = unique_suffix()
    purl = f"pkg:npm/pkg-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"pkg-{suffix}")
    session.add(component)
    await session.commit()
    await session.refresh(component)

    cv = ComponentVersion(
        component_id=component.id,
        version="1.0.0",
        purl_with_version=f"{purl}@1.0.0",
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)
    return cv.id


async def _project_with_forbidden_override_component(
    session: AsyncSession, *, team, spdx_id: str
) -> None:
    """One project, one succeeded scan, one component whose licence the policy
    override (not the static catalogue) resolves to forbidden.

    Using the override rather than a statically-forbidden ``License.category``
    keeps the dynamic path live regardless of what the static grouped query
    already found: the case that matters here, since the dynamic axis runs
    for every scan in a policy-enabled team, not only the ones the static
    count already flagged.
    """
    from models import License, LicenseFinding, ScanComponent

    project = await make_project(session, team=team)
    scan = await make_scan(session, project=project, status="succeeded")
    cv_id = await _component_version(session)
    session.add(
        ScanComponent(scan_id=scan.id, component_version_id=cv_id, direct=True, raw_data={})
    )
    lic = License(spdx_id=spdx_id, name=spdx_id, category="allowed")
    session.add(lic)
    await session.commit()
    await session.refresh(lic)
    session.add(
        LicenseFinding(
            scan_id=scan.id,
            component_version_id=cv_id,
            license_id=lic.id,
            kind="concluded",
            source_path=f"path-{unique_suffix()}",
        )
    )
    await session.commit()


async def _project_with_malicious_component(session: AsyncSession, *, team) -> None:
    """One project, one succeeded scan, one component flagged malicious.

    A single, unrelated waiver entry on the team policy is enough to exercise
    the batched purl lookup for every such scan; the waiver need not match
    the flagged purl; only ``_active_malicious_waivers`` returning a non-empty
    set matters for reaching the batch query at all.
    """
    from models import ComponentVersion, ScanComponent

    project = await make_project(session, team=team)
    scan = await make_scan(session, project=project, status="succeeded")
    cv_id = await _component_version(session)
    cv = await session.get(ComponentVersion, cv_id)
    assert cv is not None
    cv.malicious_state = "flagged"
    cv.malicious_source = "osv.dev@seed"
    session.add(cv)
    session.add(
        ScanComponent(scan_id=scan.id, component_version_id=cv_id, direct=True, raw_data={})
    )
    await session.commit()


async def test_forbidden_license_axis_query_count_does_not_grow_with_project_count(
    db_session: AsyncSession,
) -> None:
    from services.action_queue_service import get_action_queue
    from tests._helpers import principal_for

    org = await make_organization(db_session)

    team_one = await make_team(db_session, organization=org)
    spdx = f"MIT-{unique_suffix()}"
    await _enable_policy(
        db_session,
        organization_id=org.id,
        team_id=team_one.id,
        category_overrides={spdx: "forbidden"},
    )
    await _project_with_forbidden_override_component(db_session, team=team_one, spdx_id=spdx)

    # ``licenses.spdx_id`` is globally unique, so six components need six
    # distinct ids; the override map just needs to forbid all of them.
    team_many = await make_team(db_session, organization=org)
    spdx_ids_many = [f"MIT-{unique_suffix()}" for _ in range(6)]
    await _enable_policy(
        db_session,
        organization_id=org.id,
        team_id=team_many.id,
        category_overrides=dict.fromkeys(spdx_ids_many, "forbidden"),
    )
    for spdx_id in spdx_ids_many:
        await _project_with_forbidden_override_component(
            db_session, team=team_many, spdx_id=spdx_id
        )

    from tests._helpers import make_membership, make_user

    user_one = await make_user(db_session)
    await make_membership(db_session, user=user_one, team=team_one, role="developer")
    user_many = await make_user(db_session)
    await make_membership(db_session, user=user_many, team=team_many, role="developer")

    queue_one, count_one = await _count_statements(
        db_session,
        lambda: get_action_queue(
            db_session, actor=principal_for(user_one, team_ids=[team_one.id])
        ),
    )
    queue_many, count_many = await _count_statements(
        db_session,
        lambda: get_action_queue(
            db_session, actor=principal_for(user_many, team_ids=[team_many.id])
        ),
    )

    assert len(queue_one.gate_blocked) == 1
    assert len(queue_many.gate_blocked) == 6

    assert count_many == count_one, (
        f"one project issued {count_one} statements, six issued {count_many}, "
        "the dynamic forbidden-licence recount is costing a query per project "
        "again"
    )


async def test_malicious_axis_query_count_does_not_grow_with_project_count(
    db_session: AsyncSession,
) -> None:
    from services.action_queue_service import get_action_queue
    from tests._helpers import make_membership, make_user, principal_for

    org = await make_organization(db_session)
    unrelated_waiver = [
        {
            "component_purl": "pkg:npm/unrelated-package",
            "reason": "keeps _active_malicious_waivers non-empty",
            "expires_at": (datetime.now(tz=UTC) + timedelta(days=7)).isoformat(),
        }
    ]

    team_one = await make_team(db_session, organization=org)
    await _enable_policy(
        db_session,
        organization_id=org.id,
        team_id=team_one.id,
        malicious_exceptions=unrelated_waiver,
    )
    await _project_with_malicious_component(db_session, team=team_one)

    team_many = await make_team(db_session, organization=org)
    await _enable_policy(
        db_session,
        organization_id=org.id,
        team_id=team_many.id,
        malicious_exceptions=unrelated_waiver,
    )
    for _ in range(6):
        await _project_with_malicious_component(db_session, team=team_many)

    user_one = await make_user(db_session)
    await make_membership(db_session, user=user_one, team=team_one, role="developer")
    user_many = await make_user(db_session)
    await make_membership(db_session, user=user_many, team=team_many, role="developer")

    queue_one, count_one = await _count_statements(
        db_session,
        lambda: get_action_queue(
            db_session, actor=principal_for(user_one, team_ids=[team_one.id])
        ),
    )
    queue_many, count_many = await _count_statements(
        db_session,
        lambda: get_action_queue(
            db_session, actor=principal_for(user_many, team_ids=[team_many.id])
        ),
    )

    assert len(queue_one.gate_blocked) == 1
    assert len(queue_many.gate_blocked) == 6

    assert count_many == count_one, (
        f"one project issued {count_one} statements, six issued {count_many}, "
        "the malicious-waiver recount is costing a query per project again"
    )
