"""
Service-layer tests for ``cancel_scan_for_actor`` — PR-A1 user-facing cancel.

Runs against a live Postgres (marked ``integration``) so the row lock + JOIN +
team-access gate execute for real. Mirrors the structure of
``test_admin_scan_service.py``.

Coverage:
  - own-team developer cancels a queued scan        → 200 / cancelled.
  - own-team developer cancels a running scan        → revoke fired.
  - OTHER team's developer                           → 404 existence-hide.
  - super_admin bypasses the team gate.
  - already-terminal scan                            → 409 idempotent.
  - missing scan                                     → 404.
  - revoke best-effort: broker hiccup does not block the status update.
  - race: a scan that flipped terminal between SELECT and cancel → 409 no-op.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    principal_for,
    principal_loaded_from_db,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip user cancel service tests")
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
            "alembic upgrade head failed; user cancel service tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


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


# ---------------------------------------------------------------------------
# Celery control fakes (same shape as the admin test)
# ---------------------------------------------------------------------------


class _FakeControl:
    def __init__(self, *, raise_on_revoke: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raise_on_revoke = raise_on_revoke

    def revoke(self, task_id: str, *, terminate: bool = False, signal: str | None = None) -> None:
        if self.raise_on_revoke:
            # Model a real broker outage: kombu raises OperationalError, which
            # the service narrows its best-effort catch to (Low #5). A bare
            # RuntimeError here would now (correctly) propagate.
            from kombu.exceptions import OperationalError

            raise OperationalError("broker unreachable")
        self.calls.append({"task_id": task_id, "terminate": terminate, "signal": signal})


class _FakeCeleryApp:
    def __init__(self, *, raise_on_revoke: bool = False) -> None:
        self.control = _FakeControl(raise_on_revoke=raise_on_revoke)


# ---------------------------------------------------------------------------
# Happy path — own team
# ---------------------------------------------------------------------------


async def test_own_team_developer_cancels_queued_scan(db_session: AsyncSession) -> None:
    from services.admin_scan_service import cancel_scan_for_actor

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = await principal_loaded_from_db(db_session, user=user)

    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="queued")

    item = await cancel_scan_for_actor(
        db_session,
        actor=actor,
        scan_id=scan.id,
        celery_app_override=_FakeCeleryApp(),
    )
    assert item.status == "cancelled"
    assert item.error_message == "cancelled by user"
    assert item.finished_at is not None


async def test_own_team_developer_cancel_running_revokes_task(
    db_session: AsyncSession,
) -> None:
    from services.admin_scan_service import cancel_scan_for_actor

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = await principal_loaded_from_db(db_session, user=user)

    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="running")
    scan.celery_task_id = "celery-task-abc"
    await db_session.commit()

    fake = _FakeCeleryApp()
    await cancel_scan_for_actor(
        db_session, actor=actor, scan_id=scan.id, celery_app_override=fake
    )
    assert fake.control.calls == [
        {"task_id": "celery-task-abc", "terminate": True, "signal": "SIGTERM"}
    ]


async def test_super_admin_bypasses_team_gate(db_session: AsyncSession) -> None:
    from services.admin_scan_service import cancel_scan_for_actor

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="queued")

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")  # no team memberships

    item = await cancel_scan_for_actor(
        db_session, actor=actor, scan_id=scan.id, celery_app_override=_FakeCeleryApp()
    )
    assert item.status == "cancelled"


# ---------------------------------------------------------------------------
# RBAC — other team is existence-hidden as 404
# ---------------------------------------------------------------------------


async def test_other_team_developer_gets_404(db_session: AsyncSession) -> None:
    from services.admin_scan_service import AdminScanNotFound, cancel_scan_for_actor

    org = await make_organization(db_session)
    owning_team = await make_team(db_session, organization=org)
    other_team = await make_team(db_session, organization=org)

    other_user = await make_user(db_session)
    await make_membership(db_session, user=other_user, team=other_team, role="developer")
    actor = await principal_loaded_from_db(db_session, user=other_user)

    project = await make_project(db_session, team=owning_team)
    scan = await make_scan(db_session, project=project, status="running")

    with pytest.raises(AdminScanNotFound):
        await cancel_scan_for_actor(
            db_session, actor=actor, scan_id=scan.id, celery_app_override=_FakeCeleryApp()
        )

    # The scan must remain untouched (still running) — no cross-team mutation.
    await db_session.refresh(scan)
    assert scan.status == "running"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_missing_scan_raises_404(db_session: AsyncSession) -> None:
    from services.admin_scan_service import AdminScanNotFound, cancel_scan_for_actor

    user = await make_user(db_session)
    actor = principal_for(user, role="developer")

    with pytest.raises(AdminScanNotFound):
        await cancel_scan_for_actor(db_session, actor=actor, scan_id=uuid.uuid4())


@pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "cancelled"])
async def test_already_terminal_raises_409(
    db_session: AsyncSession, terminal_status: str
) -> None:
    from services.admin_scan_service import ScanAlreadyCancelled, cancel_scan_for_actor

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = await principal_loaded_from_db(db_session, user=user)

    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status=terminal_status)

    with pytest.raises(ScanAlreadyCancelled):
        await cancel_scan_for_actor(
            db_session, actor=actor, scan_id=scan.id, celery_app_override=_FakeCeleryApp()
        )


async def test_revoke_failure_does_not_block_cancel(db_session: AsyncSession) -> None:
    from services.admin_scan_service import cancel_scan_for_actor

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = await principal_loaded_from_db(db_session, user=user)

    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="running")
    scan.celery_task_id = "celery-task-broken"
    await db_session.commit()

    item = await cancel_scan_for_actor(
        db_session,
        actor=actor,
        scan_id=scan.id,
        celery_app_override=_FakeCeleryApp(raise_on_revoke=True),
    )
    assert item.status == "cancelled"


# ---------------------------------------------------------------------------
# Race: scan flipped terminal between the read and the cancel
# ---------------------------------------------------------------------------


async def test_race_scan_already_succeeded_is_409_noop(
    db_session: AsyncSession,
) -> None:
    """Simulate a scan that succeeded just before the cancel landed.

    The row-lock + terminal-state guard inside ``_lock_cancellable_scan`` is
    the canonical defence; here we model the worker winning the race by
    flipping status to succeeded right before the cancel call. The cancel must
    be a 409 no-op, never overwrite a succeeded scan with cancelled.
    """
    from services.admin_scan_service import ScanAlreadyCancelled, cancel_scan_for_actor

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = await principal_loaded_from_db(db_session, user=user)

    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="running")

    # Worker wins the race: scan finishes successfully.
    scan.status = "succeeded"
    await db_session.commit()

    with pytest.raises(ScanAlreadyCancelled):
        await cancel_scan_for_actor(
            db_session, actor=actor, scan_id=scan.id, celery_app_override=_FakeCeleryApp()
        )
    await db_session.refresh(scan)
    assert scan.status == "succeeded"  # not clobbered
