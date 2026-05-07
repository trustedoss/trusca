"""
Integration test: `alembic upgrade head` succeeds against the configured database.

Skipped automatically when DATABASE_URL is not set (e.g. unit-only local runs).
In docker-compose dev and CI the env var is provided, so this exercises a real
Postgres + the bundled migrations end-to-end.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.integration
def test_alembic_upgrade_head_succeeds():
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set — skip alembic integration test")

    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"alembic upgrade head failed (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


@pytest.mark.integration
def test_alembic_current_reports_head_revision():
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set — skip alembic integration test")

    upgrade = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert upgrade.returncode == 0, upgrade.stderr

    current = subprocess.run(
        ["alembic", "current"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert current.returncode == 0, current.stderr
    # `alembic current` prints the head revision. Each schema-changing
    # migration advances this — chore PR #5 (license fetcher cache)
    # bumped head from 0003 → 0004. Bump again when a future
    # migration lands.
    assert "0004" in current.stdout, current.stdout


@pytest.mark.integration
async def test_scan_schema_tables_exist_after_upgrade():
    """0003 must create the 11 scan-domain tables."""
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set — skip alembic integration test")

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from core.config import database_url

    upgrade = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert upgrade.returncode == 0, upgrade.stderr

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine)
    expected = {
        "projects",
        "scans",
        "scan_artifacts",
        "components",
        "component_versions",
        "scan_components",
        "vulnerabilities",
        "vulnerability_findings",
        "licenses",
        "license_findings",
        "obligations",
    }
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            )
            .scalars()
            .all()
        )
    await engine.dispose()
    assert expected.issubset(set(rows)), f"missing scan tables: {expected - set(rows)}"


@pytest.mark.integration
async def test_scan_partial_unique_index_present_after_upgrade():
    """The partial unique index ix_scans_project_active must exist."""
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set — skip alembic integration test")

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from core.config import database_url

    upgrade = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert upgrade.returncode == 0, upgrade.stderr

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine)
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' "
                    "  AND indexname = 'ix_scans_project_active'"
                )
            )
        ).first()
    await engine.dispose()
    assert row is not None, "ix_scans_project_active not found"
    indexdef = row[0]
    # Must be UNIQUE and partial on status IN ('queued','running').
    assert "UNIQUE" in indexdef.upper()
    assert "queued" in indexdef and "running" in indexdef
