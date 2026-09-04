"""
Integration test for GET /health/ready against a real Postgres (B1).

Exercises the un-mocked readiness path — ``fetch_db_revisions`` runs a real
``SELECT version_num FROM alembic_version`` and ``compute_expected_heads`` reads
the in-repo alembic script tree — so the happy path (schema at HEAD → 200) is
verified end-to-end, complementing the unit tests that inject both sources.

Skipped automatically when ``DATABASE_URL`` is unset (unit-only local runs).
In docker-compose dev + CI the env var is provided, so this hits the real DB.

CLAUDE.md core rule #1: PostgreSQL only — no SQLite, even in tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests._db_required import migrate_to_head

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
async def client():
    from main import app

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.mark.integration
async def test_health_ready_returns_200_when_schema_at_head(client) -> None:
    """With the schema migrated to HEAD, the probe returns 200 ready."""
    migrate_to_head()

    resp = await client.get("/health/ready")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "ready"}


@pytest.mark.integration
async def test_health_ready_is_unauthenticated(client) -> None:
    """The probe answers with NO Authorization header (CLAUDE.md #12 public)."""
    migrate_to_head()

    resp = await client.get("/health/ready")  # no auth header
    assert resp.status_code == 200


@pytest.mark.integration
async def test_health_ready_503_when_db_revision_behind_head(client) -> None:
    """Rewinding alembic_version one step makes the probe report 503 not-ready.

    Drives the real un-mocked comparison: ``fetch_db_revisions`` reads the
    (rewound) DB revision and ``compute_expected_heads`` reads the script HEAD;
    they differ, so the route returns the RFC 7807 problem+json 503 envelope.
    The version pointer is restored to HEAD in ``finally`` so downstream tests
    see a migrated schema.
    """
    migrate_to_head()

    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine)
    saved: str | None = None
    try:
        async with factory() as session:
            saved = (
                await session.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
            # Stamp a bogus older-looking revision so DB != script HEAD.
            await session.execute(
                text("UPDATE alembic_version SET version_num = :v"),
                {"v": "0000_not_a_real_head"},
            )
            await session.commit()

        resp = await client.get("/health/ready")
        assert resp.status_code == 503, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        for key in ("type", "title", "status", "detail", "instance"):
            assert key in body, f"problem response missing required field: {key}"
        assert body["status"] == 503
        assert body["instance"] == "/health/ready"
        assert body["ready"] is False
        # The bogus current revision is named in the (non-sensitive) detail.
        assert "0000_not_a_real_head" in body["detail"]
    finally:
        # Restore the real HEAD revision so the suite leaves the DB consistent.
        if saved is not None:
            async with factory() as session:
                await session.execute(
                    text("UPDATE alembic_version SET version_num = :v"),
                    {"v": saved},
                )
                await session.commit()
        await engine.dispose()
