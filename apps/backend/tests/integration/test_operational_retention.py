"""
Integration tests for W9 (concurrency-scaling-plan-2026-08-22.md §3.5, §4)
operational-history retention: ``notifications`` / ``webhook_deliveries`` /
``report_downloads`` rows are reclaimed once past their configured
occurrence-time age, and NOT before.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from models import Notification, Organization, Project, ReportDownload, Team, User, WebhookDelivery

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _sync_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping operational retention tests")
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic upgrade failed: {result.stderr[-400:]}")


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(_sync_url(), future=True)
    factory = sessionmaker(bind=engine, future=True)
    with factory() as s:
        yield s
    engine.dispose()


def _seed_project(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"W9 Org {suffix}", slug=f"w9-org-{suffix}")
    session.add(org)
    session.flush()
    team = Team(organization_id=org.id, name=f"W9 Team {suffix}", slug=f"w9-team-{suffix}")
    session.add(team)
    session.flush()
    project = Project(team_id=team.id, name=f"W9 Proj {suffix}", slug=f"w9-proj-{suffix}")
    session.add(project)
    session.commit()
    return project.id, team.id


def _seed_user(session: Session) -> uuid.UUID:
    suffix = uuid.uuid4().hex[:10]
    user = User(email=f"w9-op-{suffix}@example.com", hashed_password="x")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user.id


def _add_notification(session: Session, *, user_id: uuid.UUID, created_at: datetime) -> uuid.UUID:
    row = Notification(
        user_id=user_id,
        kind="scan_completed",
        title="t",
        body="b",
        created_at=created_at,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.id


def _add_webhook_delivery(session: Session, *, received_at: datetime) -> uuid.UUID:
    row = WebhookDelivery(
        provider="github",
        delivery_id=uuid.uuid4().hex,
        event_type="push",
        payload_hash=uuid.uuid4().hex.ljust(64, "0")[:64],
        received_at=received_at,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.id


def _add_report_download(
    session: Session, *, project_id: uuid.UUID, team_id: uuid.UUID, created_at: datetime
) -> uuid.UUID:
    row = ReportDownload(
        project_id=project_id,
        team_id=team_id,
        report_type="sbom",
        format="cyclonedx-json",
        created_at=created_at,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.id


def _exists(session: Session, model: Any, row_id: uuid.UUID) -> bool:
    return session.execute(select(model.id).where(model.id == row_id)).first() is not None


# ---------------------------------------------------------------------------
# notifications
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age_days", "should_be_deleted"),
    [(200, True), (10, False)],
)
def test_notification_deleted_only_past_retention_window(
    session: Session, age_days: int, should_be_deleted: bool
) -> None:
    from tasks.operational_retention import operational_retention_task

    user_id = _seed_user(session)
    now = datetime.now(UTC)
    row_id = _add_notification(session, user_id=user_id, created_at=now - timedelta(days=age_days))

    operational_retention_task()

    assert _exists(session, Notification, row_id) is (not should_be_deleted)


def test_notification_retention_days_is_configurable(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks.operational_retention import operational_retention_task

    monkeypatch.setenv("NOTIFICATION_RETENTION_DAYS", "1")
    user_id = _seed_user(session)
    now = datetime.now(UTC)
    row_id = _add_notification(session, user_id=user_id, created_at=now - timedelta(days=2))

    operational_retention_task()

    assert _exists(session, Notification, row_id) is False


# ---------------------------------------------------------------------------
# webhook_deliveries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age_days", "should_be_deleted"),
    [(100, True), (10, False)],
)
def test_webhook_delivery_deleted_only_past_retention_window(
    session: Session, age_days: int, should_be_deleted: bool
) -> None:
    from tasks.operational_retention import operational_retention_task

    now = datetime.now(UTC)
    row_id = _add_webhook_delivery(session, received_at=now - timedelta(days=age_days))

    operational_retention_task()

    assert _exists(session, WebhookDelivery, row_id) is (not should_be_deleted)


# ---------------------------------------------------------------------------
# report_downloads
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age_days", "should_be_deleted"),
    [(400, True), (10, False)],
)
def test_report_download_deleted_only_past_retention_window(
    session: Session, age_days: int, should_be_deleted: bool
) -> None:
    from tasks.operational_retention import operational_retention_task

    project_id, team_id = _seed_project(session)
    now = datetime.now(UTC)
    row_id = _add_report_download(
        session, project_id=project_id, team_id=team_id, created_at=now - timedelta(days=age_days)
    )

    operational_retention_task()

    assert _exists(session, ReportDownload, row_id) is (not should_be_deleted)


def test_operational_retention_is_idempotent(session: Session) -> None:
    from tasks.operational_retention import operational_retention_task

    user_id = _seed_user(session)
    project_id, team_id = _seed_project(session)
    now = datetime.now(UTC)
    _add_notification(session, user_id=user_id, created_at=now - timedelta(days=200))
    _add_webhook_delivery(session, received_at=now - timedelta(days=100))
    _add_report_download(
        session, project_id=project_id, team_id=team_id, created_at=now - timedelta(days=400)
    )

    first = operational_retention_task()
    second = operational_retention_task()

    assert first["deleted_notifications"] >= 1
    assert first["deleted_webhook_deliveries"] >= 1
    assert first["deleted_report_downloads"] >= 1
    assert second["deleted_notifications"] == 0
    assert second["deleted_webhook_deliveries"] == 0
    assert second["deleted_report_downloads"] == 0
