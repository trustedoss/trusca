"""
JSONB size guard at the persistence boundary — I-1 integration.

`tasks/scan_source.py` and `tasks/scan_container.py` route every JSONB
write through `enforce_jsonb_row_size_limit` before the row reaches the
ORM. We don't drive the full Celery pipeline here — we drive the
`_persist_components` helper directly with a fabricated cdxgen SBOM whose
single component carries a large free-form blob. The guard must replace
the blob with the truncation marker before persistence.

This test runs against the real Postgres because it asserts that the JSONB
column actually accepts the marker dict (the `_truncated` shape is
intentionally JSONB-friendly — primitives + nested str + numbers).
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from models import ScanComponent
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip jsonb size guard integration")
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
            f"alembic upgrade head failed; jsonb guard integration cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def sync_session() -> Iterator[Session]:
    from core.config import database_url_sync

    engine = create_engine(database_url_sync(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed_queued_scan() -> tuple[uuid.UUID, uuid.UUID]:
    """Return (scan_id, project_id), seeding via the async helpers."""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url

    async def _build() -> tuple[uuid.UUID, uuid.UUID]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team)
            from models import Scan

            scan = Scan(
                project_id=project.id,
                kind="source",
                status="queued",
                progress_percent=0,
                requested_by_user_id=user.id,
                scan_metadata={},
            )
            s.add(scan)
            await s.commit()
            await s.refresh(scan)
            scan_id = scan.id
            project_id = project.id
        await engine.dispose()
        return scan_id, project_id

    return asyncio.run(_build())


# ---------------------------------------------------------------------------
# Truncation through the persistence helper
# ---------------------------------------------------------------------------


def test_oversized_component_raw_data_is_truncated_at_persist(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    """A 512 KiB component must land as the truncation marker, not as-is."""
    monkeypatch.setenv("JSONB_ROW_SIZE_LIMIT_BYTES", str(256 * 1024))

    scan_id, _ = _seed_queued_scan()

    # Build a synthetic SBOM whose component carries an oversized blob.
    big_blob = "z" * 512 * 1024
    sbom = {
        "components": [
            {
                "purl": "pkg:npm/oversized@1.0.0",
                "name": "oversized",
                "version": "1.0.0",
                "summary": "a real package whose blob is too large for JSONB",
                "junk": big_blob,
            }
        ]
    }

    from tasks.scan_source import _persist_components

    _persist_components(sync_session, scan_uuid=scan_id, sbom=sbom)
    sync_session.commit()

    rows = (
        sync_session.execute(
            select(ScanComponent).where(ScanComponent.scan_id == scan_id)
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    raw = rows[0].raw_data
    # Marker shape — see integrations/_size_guard.py
    assert raw.get("_truncated") is True
    assert raw.get("_limit") == 256 * 1024
    assert raw.get("_original_size", 0) > 256 * 1024
    # Summary is preserved on the marker so the UI can still label the row.
    assert raw.get("summary", "").startswith("a real package")


def test_under_limit_component_is_persisted_intact(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    monkeypatch.setenv("JSONB_ROW_SIZE_LIMIT_BYTES", str(256 * 1024))

    scan_id, _ = _seed_queued_scan()

    sbom = {
        "components": [
            {
                "purl": "pkg:npm/normal@1.2.3",
                "name": "normal",
                "version": "1.2.3",
                "summary": "small component",
                "licenses": [{"license": {"id": "MIT"}}],
            }
        ]
    }

    from tasks.scan_source import _persist_components

    _persist_components(sync_session, scan_uuid=scan_id, sbom=sbom)
    sync_session.commit()

    rows = (
        sync_session.execute(
            select(ScanComponent).where(ScanComponent.scan_id == scan_id)
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    raw = rows[0].raw_data
    assert "_truncated" not in raw
    assert raw["name"] == "normal"
    assert raw["version"] == "1.2.3"
