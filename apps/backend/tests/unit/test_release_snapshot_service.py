"""
DB-backed unit tests for the release-snapshot resolver + service — feature #28.

Covers:
  - services.scan_resolution.resolve_snapshot_scan_id (the shared snapshot
    anchor resolver): None → latest succeeded; valid pin → echoed; cross-project
    / non-succeeded / nonexistent pin → SnapshotScanNotFound.
  - services.release_snapshot_service.list_release_snapshots: succeeded-only,
    newest-first, per-scan severity / risk / gate / release(null), RBAC.

Run against the real Postgres (CLAUDE.md core rule #1). Mirrors the structure of
``tests/unit/test_project_detail_service.py``.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
    principal_for,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip release-snapshot service tests")
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
            f"alembic upgrade head failed; release-snapshot service tests cannot run\n"
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
# Local factories
# ---------------------------------------------------------------------------


async def _make_scan(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    status: str = "succeeded",
    created_at: datetime,
    release: str | None = None,
    n_critical: int = 0,
    n_high: int = 0,
) -> uuid.UUID:
    from models import (
        Component,
        ComponentVersion,
        Scan,
        ScanComponent,
        Vulnerability,
        VulnerabilityFinding,
    )

    metadata: dict[str, str] = {}
    if release is not None:
        metadata["release"] = release
    scan = Scan(
        project_id=project_id,
        kind="source",
        status=status,
        progress_percent=100 if status == "succeeded" else 0,
        scan_metadata=metadata,
        created_at=created_at,
    )
    session.add(scan)
    await session.commit()
    await session.refresh(scan)

    for severity, count in (("critical", n_critical), ("high", n_high)):
        for _ in range(count):
            suffix = unique_suffix()
            purl = f"pkg:npm/pkg-{suffix}"
            component = Component(purl=purl, package_type="npm", name=f"pkg-{suffix}")
            session.add(component)
            await session.commit()
            await session.refresh(component)
            cv = ComponentVersion(
                component_id=component.id,
                version="1.0.0",
                purl_with_version=f"{purl}@1.0.0",
            )
            session.add(cv)
            await session.commit()
            await session.refresh(cv)
            session.add(
                ScanComponent(
                    scan_id=scan.id,
                    component_version_id=cv.id,
                    direct=True,
                    depth=1,
                    raw_data={},
                )
            )
            vuln = Vulnerability(
                external_id=f"CVE-2024-{suffix}",
                source="NVD",
                severity=severity,
                summary=f"vuln {suffix}",
            )
            session.add(vuln)
            await session.commit()
            await session.refresh(vuln)
            session.add(
                VulnerabilityFinding(
                    scan_id=scan.id,
                    component_version_id=cv.id,
                    vulnerability_id=vuln.id,
                )
            )
            await session.commit()

    return scan.id


async def _make_project(session: AsyncSession):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role="developer")
    project = await make_project(session, team=team)
    return team, user, project


# ---------------------------------------------------------------------------
# resolve_snapshot_scan_id
# ---------------------------------------------------------------------------


async def test_resolve_none_returns_latest_succeeded(db_session: AsyncSession) -> None:
    from services.scan_resolution import resolve_snapshot_scan_id

    _, _, project = await _make_project(db_session)
    base = datetime(2026, 5, 20, tzinfo=UTC)
    await _make_scan(db_session, project_id=project.id, created_at=base)
    latest = await _make_scan(
        db_session, project_id=project.id, created_at=base + timedelta(days=1)
    )

    resolved = await resolve_snapshot_scan_id(db_session, project.id, None)
    assert resolved == latest


async def test_resolve_none_returns_none_when_no_succeeded(db_session: AsyncSession) -> None:
    from services.scan_resolution import resolve_snapshot_scan_id

    _, _, project = await _make_project(db_session)
    await _make_scan(
        db_session,
        project_id=project.id,
        status="failed",
        created_at=datetime(2026, 5, 20, tzinfo=UTC),
    )
    resolved = await resolve_snapshot_scan_id(db_session, project.id, None)
    assert resolved is None


async def test_resolve_valid_pin_is_echoed(db_session: AsyncSession) -> None:
    from services.scan_resolution import resolve_snapshot_scan_id

    _, _, project = await _make_project(db_session)
    base = datetime(2026, 5, 20, tzinfo=UTC)
    older = await _make_scan(db_session, project_id=project.id, created_at=base)
    await _make_scan(db_session, project_id=project.id, created_at=base + timedelta(days=1))

    resolved = await resolve_snapshot_scan_id(db_session, project.id, older)
    assert resolved == older


async def test_resolve_cross_project_pin_raises(db_session: AsyncSession) -> None:
    from services.scan_resolution import SnapshotScanNotFound, resolve_snapshot_scan_id

    _, _, project_a = await _make_project(db_session)
    _, _, project_b = await _make_project(db_session)
    base = datetime(2026, 5, 20, tzinfo=UTC)
    await _make_scan(db_session, project_id=project_a.id, created_at=base)
    foreign = await _make_scan(db_session, project_id=project_b.id, created_at=base)

    with pytest.raises(SnapshotScanNotFound):
        await resolve_snapshot_scan_id(db_session, project_a.id, foreign)


async def test_resolve_non_succeeded_pin_raises(db_session: AsyncSession) -> None:
    from services.scan_resolution import SnapshotScanNotFound, resolve_snapshot_scan_id

    _, _, project = await _make_project(db_session)
    failed = await _make_scan(
        db_session,
        project_id=project.id,
        status="failed",
        created_at=datetime(2026, 5, 20, tzinfo=UTC),
    )
    with pytest.raises(SnapshotScanNotFound):
        await resolve_snapshot_scan_id(db_session, project.id, failed)


async def test_resolve_nonexistent_pin_raises(db_session: AsyncSession) -> None:
    from services.scan_resolution import SnapshotScanNotFound, resolve_snapshot_scan_id

    _, _, project = await _make_project(db_session)
    with pytest.raises(SnapshotScanNotFound):
        await resolve_snapshot_scan_id(db_session, project.id, uuid.uuid4())


# ---------------------------------------------------------------------------
# list_release_snapshots
# ---------------------------------------------------------------------------


async def test_list_releases_succeeded_only_newest_first(db_session: AsyncSession) -> None:
    from services.release_snapshot_service import list_release_snapshots

    team, user, project = await _make_project(db_session)
    base = datetime(2026, 5, 20, tzinfo=UTC)
    older = await _make_scan(
        db_session, project_id=project.id, created_at=base, n_critical=2, release="v1.0.0"
    )
    newer = await _make_scan(
        db_session, project_id=project.id, created_at=base + timedelta(days=2), n_high=1
    )
    # Excluded: failed + running.
    await _make_scan(
        db_session,
        project_id=project.id,
        status="failed",
        created_at=base + timedelta(days=3),
    )
    await _make_scan(
        db_session,
        project_id=project.id,
        status="running",
        created_at=base + timedelta(days=4),
    )

    actor = principal_for(user, team_ids=[team.id], role="developer")
    items, total = await list_release_snapshots(
        db_session, project_id=project.id, actor=actor
    )

    assert total == 2
    assert [i["scan_id"] for i in items] == [newer, older]

    newer_row, older_row = items
    assert newer_row["release"] is None
    assert newer_row["severity_summary"] == {"critical": 0, "high": 1, "medium": 0, "low": 0}
    assert newer_row["component_count"] == 1
    assert newer_row["gate_status"] == "pass"
    assert newer_row["risk_score"] == 54.8  # 1 high → security band 50–74, n=1 → 54.8

    assert older_row["release"] == "v1.0.0"
    assert older_row["severity_summary"] == {"critical": 2, "high": 0, "medium": 0, "low": 0}
    assert older_row["component_count"] == 2
    assert older_row["gate_status"] == "fail"
    assert older_row["risk_score"] == 83.3  # 2 critical → security band 75–100, n=2 → 83.3


async def test_list_releases_empty_when_no_succeeded(db_session: AsyncSession) -> None:
    from services.release_snapshot_service import list_release_snapshots

    team, user, project = await _make_project(db_session)
    await _make_scan(
        db_session,
        project_id=project.id,
        status="failed",
        created_at=datetime(2026, 5, 20, tzinfo=UTC),
    )
    actor = principal_for(user, team_ids=[team.id], role="developer")
    items, total = await list_release_snapshots(
        db_session, project_id=project.id, actor=actor
    )
    assert items == []
    assert total == 0


async def test_list_releases_paginates(db_session: AsyncSession) -> None:
    from services.release_snapshot_service import list_release_snapshots

    team, user, project = await _make_project(db_session)
    base = datetime(2026, 5, 20, tzinfo=UTC)
    ids = []
    for i in range(3):
        ids.append(
            await _make_scan(
                db_session, project_id=project.id, created_at=base + timedelta(days=i), n_high=1
            )
        )
    actor = principal_for(user, team_ids=[team.id], role="developer")

    page1, total = await list_release_snapshots(
        db_session, project_id=project.id, actor=actor, page=1, size=2
    )
    assert total == 3
    assert len(page1) == 2
    # Newest-first: the two most recent.
    assert [i["scan_id"] for i in page1] == [ids[2], ids[1]]

    page2, _ = await list_release_snapshots(
        db_session, project_id=project.id, actor=actor, page=2, size=2
    )
    assert [i["scan_id"] for i in page2] == [ids[0]]


async def test_list_releases_rbac_non_member_forbidden(db_session: AsyncSession) -> None:
    from services.project_service import ProjectForbidden
    from services.release_snapshot_service import list_release_snapshots

    team, _, project = await _make_project(db_session)
    outsider = await make_user(db_session)
    await _make_scan(
        db_session, project_id=project.id, created_at=datetime(2026, 5, 20, tzinfo=UTC), n_high=1
    )
    # Outsider has NO membership in the owning team.
    actor = principal_for(outsider, team_ids=[], role="developer")
    with pytest.raises(ProjectForbidden):
        await list_release_snapshots(db_session, project_id=project.id, actor=actor)


async def test_list_releases_unknown_project_404(db_session: AsyncSession) -> None:
    from services.project_service import ProjectNotFound
    from services.release_snapshot_service import list_release_snapshots

    _, user, _ = await _make_project(db_session)
    actor = principal_for(user, team_ids=[], role="developer")
    with pytest.raises(ProjectNotFound):
        await list_release_snapshots(db_session, project_id=uuid.uuid4(), actor=actor)


async def test_list_releases_super_admin_bypasses_team(db_session: AsyncSession) -> None:
    from services.release_snapshot_service import list_release_snapshots

    _, _, project = await _make_project(db_session)
    admin = await make_user(db_session, is_superuser=True)
    await _make_scan(
        db_session, project_id=project.id, created_at=datetime(2026, 5, 20, tzinfo=UTC), n_high=1
    )
    actor = principal_for(admin, team_ids=[], role="super_admin")
    items, total = await list_release_snapshots(
        db_session, project_id=project.id, actor=actor
    )
    assert total == 1
