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
    # After `upgrade head`, `alembic current` marks the applied revision
    # with "(head)". Assert the head *marker* rather than a hardcoded
    # revision id, so a new migration never requires editing this test.
    # (This previously hardcoded the latest id and broke on every migration —
    # 0014 → 0015 EPSS was the most recent breakage.)
    assert "(head)" in current.stdout, current.stdout


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


@pytest.mark.integration
async def test_chore_pr7_data_migration_clears_reference_url():
    """Re-applying revision 0005 wipes any populated reference_url
    cells in ``license_fetch_cache`` (security-reviewer Medium #2).

    The migration is idempotent — its WHERE clause filters out rows
    that already have ``reference_url IS NULL``.
    """
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set — skip alembic integration test")

    from datetime import UTC, datetime

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

    purl_with_url = "pkg:maven/com.test.alembic-pr7/with-url@1"
    purl_without_url = "pkg:maven/com.test.alembic-pr7/no-url@1"
    rows = []
    try:
        # Stage two cache rows: one with a (would-be-phishing)
        # reference_url, one already cleared. Then re-run the data
        # migration head-only and assert both are NULL.
        async with factory() as session:
            await session.execute(
                text(
                    "DELETE FROM license_fetch_cache "
                    "WHERE purl LIKE :p"
                ),
                {"p": "pkg:maven/com.test.alembic-pr7/%"},
            )
            await session.execute(
                text(
                    "INSERT INTO license_fetch_cache "
                    "(purl, spdx_id, reference_url, source, is_negative, "
                    " fetched_at) "
                    "VALUES "
                    "(:p1, 'Apache-2.0', 'https://attacker.example/spoof', "
                    " 'maven_central', false, :ts), "
                    "(:p2, 'MIT', NULL, 'maven_central', false, :ts)"
                ),
                {"p1": purl_with_url, "p2": purl_without_url, "ts": datetime.now(UTC)},
            )
            await session.commit()

        # Re-apply the data migration. ``alembic stamp`` rewinds the
        # version pointer one step so ``upgrade 0005`` re-runs revision
        # 0005's UPDATE. We deliberately stop at 0005 (not head) because
        # later schema migrations like 0006 (password_reset_tokens) would
        # fail with "relation already exists" on re-run; only the 0005
        # data migration is idempotent by design (its WHERE clause).
        stamp = subprocess.run(
            ["alembic", "stamp", "0004"],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert stamp.returncode == 0, stamp.stderr

        rerun = subprocess.run(
            ["alembic", "upgrade", "0005"],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert rerun.returncode == 0, rerun.stderr

        # Restore the version pointer to head so the suite leaves the DB
        # in the expected state for downstream tests.
        restamp = subprocess.run(
            ["alembic", "stamp", "head"],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert restamp.returncode == 0, restamp.stderr

        async with factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT purl, reference_url FROM license_fetch_cache "
                        "WHERE purl LIKE :p ORDER BY purl"
                    ),
                    {"p": "pkg:maven/com.test.alembic-pr7/%"},
                )
            ).all()
            await session.execute(
                text("DELETE FROM license_fetch_cache WHERE purl LIKE :p"),
                {"p": "pkg:maven/com.test.alembic-pr7/%"},
            )
            await session.commit()
    finally:
        # Always restore the alembic version pointer to head, even if an
        # assertion failed mid-test. Otherwise downstream test fixtures
        # that call ``alembic upgrade head`` will trip on
        # "relation already exists" for tables created in revisions later
        # than 0005 (e.g. password_reset_tokens in 0006).
        subprocess.run(
            ["alembic", "stamp", "head"],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        await engine.dispose()

    by_purl = {row[0]: row[1] for row in rows}
    assert by_purl.get(purl_with_url) is None, "phishing reference_url not cleared"
    assert by_purl.get(purl_without_url) is None, "already-NULL row mutated unexpectedly"
