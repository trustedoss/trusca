"""
N18 x N9 pairing: a schedule-triggered scan's completion notifies the
project's team (tasks._scan_pipeline.mark_succeeded/mark_failed).

Nobody is watching a scheduled scan the way a person watches one they just
clicked "scan" on, so this is the one trigger source that notifies on its
own completion. A manual/webhook/CI scan must NOT gain this side effect —
each of those already reports through the surface that started it.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from core.db import sync_session_scope
from models import Membership
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
)

pytestmark = pytest.mark.integration


def _require_database_url() -> None:
    import os

    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set: skip schedule-scan notification tests")


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    import subprocess
    from pathlib import Path

    _require_database_url()
    backend_root = Path(__file__).resolve().parent.parent.parent
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=backend_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic upgrade head failed\n{result.stdout}\n{result.stderr}")


async def _seed_team_of_two(db_session):
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    dev = await make_user(db_session)
    admin = await make_user(db_session)
    await make_membership(db_session, user=dev, team=team, role="developer")
    await make_membership(db_session, user=admin, team=team, role="team_admin")
    return project, {dev.id, admin.id}


@pytest.fixture
async def db_session():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


def _record_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    import tasks.notify as notify_module

    calls: list[dict[str, Any]] = []

    def _record(kind, context, channels, recipients=None, **kwargs):  # noqa: ANN001
        calls.append({"kind": kind, "context": context, **kwargs})

    monkeypatch.setattr(notify_module.send_notification_task, "delay", _record)
    return calls


async def test_a_schedule_triggered_success_notifies_every_team_member(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks._scan_pipeline import mark_succeeded

    project, member_ids = await _seed_team_of_two(db_session)
    scan = await make_scan(
        db_session,
        project=project,
        status="running",
        scan_metadata={"trigger": "schedule", "source": "scheduled-scan"},
    )
    calls = _record_calls(monkeypatch)

    mark_succeeded(scan.id)

    notified = {uuid.UUID(c["user_id"]) for c in calls}
    assert notified == member_ids
    assert all(c["kind"] == "scan_completed" for c in calls)


async def test_a_schedule_triggered_failure_notifies_too(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks._scan_pipeline import record_terminal_failure

    project, member_ids = await _seed_team_of_two(db_session)
    scan = await make_scan(
        db_session,
        project=project,
        status="running",
        scan_metadata={"trigger": "schedule"},
    )
    calls = _record_calls(monkeypatch)

    record_terminal_failure(scan.id, "cdxgen crashed")

    notified = {uuid.UUID(c["user_id"]) for c in calls}
    assert notified == member_ids
    assert all(c["kind"] == "scan_failed" for c in calls)


async def test_a_manually_triggered_scan_does_not_gain_this_notification(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the schedule trigger gets this side effect — a manual/webhook/CI
    scan already reports through the surface that started it."""
    from tasks._scan_pipeline import mark_succeeded

    project, _member_ids = await _seed_team_of_two(db_session)
    scan = await make_scan(db_session, project=project, status="running", scan_metadata={})
    calls = _record_calls(monkeypatch)

    mark_succeeded(scan.id)

    assert calls == []


async def test_a_service_account_membership_is_never_notified(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks._scan_pipeline import mark_succeeded

    project, member_ids = await _seed_team_of_two(db_session)
    with sync_session_scope() as session:
        from models import User

        service_account = User(
            email=f"svc-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="!",
            full_name="CI bot",
            is_service_account=True,
        )
        session.add(service_account)
        session.flush()
        session.add(
            Membership(user_id=service_account.id, team_id=project.team_id, role="developer")
        )
        session.commit()

    scan = await make_scan(
        db_session, project=project, status="running", scan_metadata={"trigger": "schedule"}
    )
    calls = _record_calls(monkeypatch)

    mark_succeeded(scan.id)

    notified = {uuid.UUID(c["user_id"]) for c in calls}
    assert notified == member_ids  # service account excluded
