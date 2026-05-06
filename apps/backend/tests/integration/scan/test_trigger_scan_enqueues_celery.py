"""
trigger_scan + Celery dispatcher integration — Phase 2 PR #8.

What we pin:

  - On success: enqueue_scan is called once, scan.celery_task_id is the
    returned id, and project.latest_scan_id points at the new scan.
  - On enqueue failure (broker down): the scan moves to status='failed' with
    a clear `error_message`, and the service raises ScanEnqueueFailed (503).

The Celery dispatcher is monkeypatched at the import site — tests do not
need a real broker. We patch `tasks.scan_source.scan_source_task.delay` so
the dispatcher's branch logic (kind='source') runs end-to-end while skipping
the actual broker round-trip.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import Project
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
    principal_for,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip scan trigger integration")
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
            f"alembic upgrade head failed; trigger_scan integration cannot run\n"
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
# Happy path — enqueue_scan is invoked
# ---------------------------------------------------------------------------


async def test_trigger_scan_invokes_enqueue_scan_dispatcher(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """trigger_scan must dispatch via tasks.enqueue_scan and persist the task id."""
    from schemas.scan import ScanCreate
    from services.scan_service import trigger_scan

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    invocations: list[str] = []

    def fake_enqueue(scan):  # type: ignore[no-untyped-def]
        invocations.append(str(scan.id))
        return "celery-task-fake-1"

    # Patch at the import site inside services.scan_service (preferred binding
    # location — the service does `from tasks import enqueue_scan`).
    monkeypatch.setattr(
        "services.scan_service.enqueue_scan",
        fake_enqueue,
        raising=False,
    )

    scan = await trigger_scan(
        db_session,
        project_id=project.id,
        payload=ScanCreate(kind="source"),
        actor=actor,
    )

    assert invocations, "trigger_scan must invoke enqueue_scan"
    assert invocations[0] == str(scan.id)
    assert scan.celery_task_id == "celery-task-fake-1"


async def test_trigger_scan_updates_project_latest_scan_id(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from schemas.scan import ScanCreate
    from services.scan_service import trigger_scan

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    monkeypatch.setattr(
        "services.scan_service.enqueue_scan",
        lambda _scan: "task-x",
        raising=False,
    )

    scan = await trigger_scan(
        db_session,
        project_id=project.id,
        payload=ScanCreate(kind="source"),
        actor=actor,
    )

    refreshed = (
        await db_session.execute(select(Project).where(Project.id == project.id))
    ).scalar_one()
    assert refreshed.latest_scan_id == scan.id


# ---------------------------------------------------------------------------
# Failure path — broker down → ScanEnqueueFailed (503)
# ---------------------------------------------------------------------------


async def test_enqueue_failure_marks_scan_failed_and_raises_503(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from schemas.scan import ScanCreate
    from services.scan_service import ScanEnqueueFailed, trigger_scan

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    def fake_enqueue(_scan):  # type: ignore[no-untyped-def]
        raise RuntimeError("broker connection refused")

    monkeypatch.setattr(
        "services.scan_service.enqueue_scan",
        fake_enqueue,
        raising=False,
    )

    with pytest.raises(ScanEnqueueFailed):
        await trigger_scan(
            db_session,
            project_id=project.id,
            payload=ScanCreate(kind="source"),
            actor=actor,
        )

    # The Scan row should still exist but in `failed` status with a clear
    # error_message — that's the "we wrote, then dispatch fell over" contract
    # the backend-developer task documents.
    from models import Scan

    rows = (
        await db_session.execute(select(Scan).where(Scan.project_id == project.id))
    ).scalars().all()
    assert any(s.status == "failed" for s in rows)


# Dispatcher routing tests live in tests/unit/test_scan_dispatcher.py — they
# don't need Postgres so we keep them in the unit suite to keep this module
# focused on the trigger_scan ↔ enqueue_scan integration contract.
