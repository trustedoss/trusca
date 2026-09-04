# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""One organization's registry login must never reach another's scan (ER3).

`credentials_for_image` is the whole of that boundary. It narrows a lookup to
one organization and one registry, and everything downstream trusts it: what it
returns is written into a file that Trivy reads while parsing an image the
scan's own team chose. A missing `organization_id` filter here would not fail
any other test, because every other test uses one organization.

Two organizations holding a credential for the SAME host is the case that
matters. A filter that is wrong but present still returns a row, so a test with
one organization passes either way.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set: skip registry credential isolation test")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")


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


@pytest.fixture(scope="module")
def two_orgs_sharing_a_registry() -> tuple[uuid.UUID, uuid.UUID]:
    """Both organizations hold a `ghcr.io` login, with different passwords.

    Module-scoped on purpose. An admin API test elsewhere pages organizations
    at 200 per page and looks for its own, so every organization a test leaves
    behind eats into that margin. Seeding once costs nothing here: these tests
    only read.
    """
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url
    from services.registry_credential_service import upsert_credential
    from tests._helpers import make_organization

    async def _build() -> tuple[uuid.UUID, uuid.UUID]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            org_a = await make_organization(session)
            org_b = await make_organization(session)
            await upsert_credential(
                session,
                organization_id=org_a.id,
                registry_host="ghcr.io",
                username="org-a-bot",
                password="org-a-password",
            )
            await upsert_credential(
                session,
                organization_id=org_b.id,
                registry_host="ghcr.io",
                username="org-b-bot",
                password="org-b-password",
            )
            ids = (org_a.id, org_b.id)
        await engine.dispose()
        return ids

    return asyncio.run(_build())


def test_a_scan_only_ever_sees_its_own_organizations_credential(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    two_orgs_sharing_a_registry: tuple[uuid.UUID, uuid.UUID],
) -> None:
    from services.registry_credential_service import credentials_for_image

    monkeypatch.delenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", raising=False)
    org_a, org_b = two_orgs_sharing_a_registry

    for organization_id, username, password in (
        (org_a, "org-a-bot", "org-a-password"),
        (org_b, "org-b-bot", "org-b-password"),
    ):
        found = credentials_for_image(
            db_session,
            organization_id=organization_id,
            image_ref="ghcr.io/someone/app:1",
        )
        # Exactly one entry, and it is this organization's. Asserting only that
        # the right credential is present would pass while the other one rode
        # along in the same config.json.
        assert found == {"ghcr.io": (username, password)}


def test_an_organization_with_no_credential_gets_nothing_from_a_neighbour(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    two_orgs_sharing_a_registry: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """The scan proceeds anonymously rather than borrowing someone's login."""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url
    from services.registry_credential_service import credentials_for_image
    from tests._helpers import make_organization

    monkeypatch.delenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", raising=False)
    assert two_orgs_sharing_a_registry  # the neighbours whose logins must not leak

    async def _bare_org() -> uuid.UUID:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            org = await make_organization(session)
            org_id = org.id
        await engine.dispose()
        return org_id

    found = credentials_for_image(
        db_session,
        organization_id=asyncio.run(_bare_org()),
        image_ref="ghcr.io/someone/app:1",
    )
    assert found == {}


def test_a_credential_for_another_registry_is_not_offered(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    two_orgs_sharing_a_registry: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """Narrowing by registry as well as by organization: a config.json read
    while Trivy parses an attacker-supplied image should hold the one login
    that pull needs and no other."""
    from services.registry_credential_service import credentials_for_image

    monkeypatch.delenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", raising=False)
    org_a, _org_b = two_orgs_sharing_a_registry

    found = credentials_for_image(
        db_session,
        organization_id=org_a,
        image_ref="registry.example.com/someone/app:1",
    )
    assert found == {}
