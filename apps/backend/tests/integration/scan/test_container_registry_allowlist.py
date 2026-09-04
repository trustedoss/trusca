# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The worker's own registry check, driven through the real task (ER3).

This exists because a unit test that asserts the helpers are importable does
NOT catch the enforcement being deleted: the helpers stay importable and the
schema check still rejects new triggers, so everything looks green while the
worker will pull anything handed to it. That was confirmed by removing the
block and watching such a test pass.

The worker check is not redundant with the schema check. A scan row reaches
this task without passing the schema again when it was queued before the
allow-list was tightened, so the row that must be stopped is exactly the one
the API never re-validates.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from models import Scan
from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def db_session() -> Iterator[Session]:
    from core.config import database_url

    engine = create_engine(
        database_url().replace("+asyncpg", "+psycopg2"), pool_pre_ping=True, future=True
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed_queued_container_scan(image_ref: str) -> uuid.UUID:
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url
    from tests._helpers import (
        make_membership,
        make_organization,
        make_project,
        make_team,
        make_user,
    )

    async def _build() -> uuid.UUID:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team)
            scan = Scan(
                project_id=project.id,
                kind="container",
                status="queued",
                progress_percent=0,
                requested_by_user_id=user.id,
                scan_metadata={"image_ref": image_ref},
            )
            s.add(scan)
            await s.commit()
            await s.refresh(scan)
            scan_id = scan.id
        await engine.dispose()
        return scan_id

    return asyncio.run(_build())


def test_the_worker_refuses_a_registry_outside_the_allowlist(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row queued before the list was tightened.

    Nothing is mocked out of the pull path: if the check were absent the task
    would go on to call Trivy, so this fails loudly rather than silently
    passing.
    """
    monkeypatch.setenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", "ghcr.io")
    scan_id = _seed_queued_container_scan("evil.example.com/app:1")

    from tasks.scan_container import scan_container_task

    scan_container_task.apply(args=[str(scan_id)])

    db_session.expire_all()
    scan = db_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "failed"
    # The operator's fix is naming the registry, so both it and the setting
    # have to be in the message.
    assert "evil.example.com" in (scan.error_message or "")
    assert "CONTAINER_SCAN_ALLOWED_REGISTRIES" in (scan.error_message or "")


def test_the_worker_refuses_the_path_bypass(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`evil.example.com/ghcr.io/app` must not satisfy an entry of `ghcr.io`."""
    monkeypatch.setenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", "ghcr.io")
    scan_id = _seed_queued_container_scan("evil.example.com/ghcr.io/app:1")

    from tasks.scan_container import scan_container_task

    scan_container_task.apply(args=[str(scan_id)])

    db_session.expire_all()
    scan = db_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "failed"
    # Asserted on the SETTING name, not just the hostname. Without the check
    # the task goes on to pull, that pull fails, and the hostname appears in
    # THAT error too, so a hostname-only assertion passes for the wrong reason
    # and the test proves nothing. Only the allow-list refusal names the knob.
    assert "CONTAINER_SCAN_ALLOWED_REGISTRIES" in (scan.error_message or "")
