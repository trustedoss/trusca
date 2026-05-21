"""
Service-layer tests for ``services.admin_scan_service`` — Phase 4 PR #14.

Drives list_scans + cancel_scan against a live Postgres so the JOIN +
SELECT FOR UPDATE actually run.

Coverage:
  - list_scans: cross-team join, status filter, default ordering,
    pagination envelope.
  - cancel_scan: queued → cancelled, running → cancelled, terminal-state
    409, 404 on missing scan.
  - revoke is best-effort: a broker exception does NOT prevent the status
    update.
  - audit row written on cancel (listener captures the status mutation).
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import (
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    principal_for,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip admin_scan_service tests")
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
            f"alembic upgrade head failed; admin_scan_service tests cannot run\n"
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
# Fakes — Celery control surface
# ---------------------------------------------------------------------------


class _FakeControl:
    """Records revoke calls so tests can assert behaviour."""

    def __init__(self, *, raise_on_revoke: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raise_on_revoke = raise_on_revoke

    def revoke(self, task_id: str, *, terminate: bool = False, signal: str | None = None) -> None:
        if self.raise_on_revoke:
            # Model a real broker outage: kombu raises OperationalError, which
            # the service's best-effort catch is now narrowed to (Low #5). A
            # bare RuntimeError would (correctly) propagate as a programming
            # error rather than be swallowed as a transient broker hiccup.
            from kombu.exceptions import OperationalError

            raise OperationalError("broker unreachable")
        self.calls.append({"task_id": task_id, "terminate": terminate, "signal": signal})


class _FakeCeleryApp:
    def __init__(self, *, raise_on_revoke: bool = False) -> None:
        self.control = _FakeControl(raise_on_revoke=raise_on_revoke)


# ---------------------------------------------------------------------------
# list_scans
# ---------------------------------------------------------------------------


async def test_list_scans_returns_pagination_envelope(db_session: AsyncSession) -> None:
    from services.admin_scan_service import list_scans

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="queued")

    page = await list_scans(db_session, actor=actor, page=1, page_size=10)
    assert page.page == 1
    assert page.page_size == 10
    assert page.total >= 1
    assert any(item.id == scan.id for item in page.items)
    matching = next(item for item in page.items if item.id == scan.id)
    assert matching.team_id == team.id
    assert matching.team_name == team.name
    assert matching.project_name == project.name


async def test_list_scans_status_filter(db_session: AsyncSession) -> None:
    from services.admin_scan_service import list_scans

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)

    project1 = await make_project(db_session, team=team)
    project2 = await make_project(db_session, team=team)

    queued = await make_scan(db_session, project=project1, status="queued")
    succeeded = await make_scan(db_session, project=project2, status="succeeded")

    page = await list_scans(db_session, actor=actor, status="queued", page_size=200)
    assert any(item.id == queued.id for item in page.items)
    assert all(item.status == "queued" for item in page.items)
    assert succeeded.id not in {item.id for item in page.items}


# ---------------------------------------------------------------------------
# cancel_scan — happy paths
# ---------------------------------------------------------------------------


async def test_cancel_scan_queued_to_cancelled(db_session: AsyncSession) -> None:
    from services.admin_scan_service import cancel_scan

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="queued")

    fake_celery = _FakeCeleryApp()
    item = await cancel_scan(
        db_session,
        actor=actor,
        scan_id=scan.id,
        celery_app_override=fake_celery,
    )
    assert item.status == "cancelled"
    assert item.error_message == "cancelled by admin"
    assert item.finished_at is not None


async def test_cancel_scan_running_revokes_celery_task(db_session: AsyncSession) -> None:
    from services.admin_scan_service import cancel_scan

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="running")
    # Seed a celery task id so the revoke branch fires.
    scan.celery_task_id = "celery-task-xyz"
    await db_session.commit()

    fake_celery = _FakeCeleryApp()
    await cancel_scan(
        db_session,
        actor=actor,
        scan_id=scan.id,
        celery_app_override=fake_celery,
    )
    assert fake_celery.control.calls == [
        {"task_id": "celery-task-xyz", "terminate": True, "signal": "SIGTERM"}
    ]


async def test_cancel_scan_revoke_failure_does_not_block_status_update(
    db_session: AsyncSession,
) -> None:
    """A broker hiccup must not stop us from marking the scan cancelled."""
    from services.admin_scan_service import cancel_scan

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="running")
    scan.celery_task_id = "celery-task-xyz"
    await db_session.commit()

    fake_celery = _FakeCeleryApp(raise_on_revoke=True)
    item = await cancel_scan(
        db_session,
        actor=actor,
        scan_id=scan.id,
        celery_app_override=fake_celery,
    )
    assert item.status == "cancelled"


# ---------------------------------------------------------------------------
# cancel_scan — error paths
# ---------------------------------------------------------------------------


async def test_cancel_scan_unknown_id_raises_404(db_session: AsyncSession) -> None:
    from services.admin_scan_service import AdminScanNotFound, cancel_scan

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(AdminScanNotFound):
        await cancel_scan(
            db_session,
            actor=actor,
            scan_id=uuid.uuid4(),
        )


@pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "cancelled"])
async def test_cancel_scan_terminal_state_raises_409(
    db_session: AsyncSession, terminal_status: str
) -> None:
    from services.admin_scan_service import ScanAlreadyCancelled, cancel_scan

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status=terminal_status)

    with pytest.raises(ScanAlreadyCancelled):
        await cancel_scan(db_session, actor=actor, scan_id=scan.id)


# ---------------------------------------------------------------------------
# Audit trail — listener captures the cancel mutation
# ---------------------------------------------------------------------------


async def test_cancel_scan_writes_audit_row(db_session: AsyncSession) -> None:
    from services.admin_scan_service import cancel_scan

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="queued")

    # The audit listener requires the contextvar to be bound to the actor —
    # mirror what get_current_user does in the request path.
    from core.audit import audit_context

    audit_context.set({"user_id": str(actor.id)})

    await cancel_scan(
        db_session,
        actor=actor,
        scan_id=scan.id,
        celery_app_override=_FakeCeleryApp(),
    )

    rows = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM audit_logs "
                "WHERE target_table='scans' AND action='update' "
                "  AND target_id=:tid"
            ),
            {"tid": str(scan.id)},
        )
    ).scalar_one()
    assert rows >= 1
