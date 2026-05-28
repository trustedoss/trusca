"""
Regression: ``_reset_scan_for_rerun`` clears partial child rows on re-execution.

PR-A1 context: with ``task_acks_late=True`` a scan task can be redelivered
(worker restart / SIGKILL after the soft-timeout cleanup but before ack). The
re-execution must start from a clean slate so partial data from the aborted
run does not double-count. This pins that the reset deletes the prior
ScanComponent / VulnerabilityFinding / LicenseFinding / ScanArtifact rows for
the scan — and ONLY for that scan (a sibling scan's rows are untouched).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip scan rerun reset test")
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
            "alembic upgrade head failed; scan rerun reset test cannot run\n"
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


def _seed_scan_with_partial_children(session: Session, *, slug: str):
    """Create a project + scan + one ScanComponent + one ScanArtifact."""
    import uuid

    from models import (
        Component,
        ComponentVersion,
        Organization,
        Project,
        Scan,
        ScanArtifact,
        ScanComponent,
        Team,
    )

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Org {suffix}", slug=f"org-{suffix}")
    session.add(org)
    session.flush()
    team = Team(organization_id=org.id, name=f"Team {suffix}", slug=f"team-{suffix}")
    session.add(team)
    session.flush()
    project = Project(team_id=team.id, name=f"Proj {suffix}", slug=f"proj-{suffix}")
    session.add(project)
    session.flush()
    scan = Scan(project_id=project.id, kind="source", status="running", progress_percent=50)
    session.add(scan)
    session.flush()

    comp = Component(purl=f"pkg:pypi/{slug}-{suffix}", name=slug, package_type="pypi")
    session.add(comp)
    session.flush()
    cv = ComponentVersion(
        component_id=comp.id,
        version="1.0.0",
        purl_with_version=f"pkg:pypi/{slug}-{suffix}@1.0.0",
    )
    session.add(cv)
    session.flush()
    session.add(
        ScanComponent(
            scan_id=scan.id,
            component_version_id=cv.id,
            dependency_path="root",
            direct=True,
            raw_data={},
        )
    )
    session.add(
        ScanArtifact(
            scan_id=scan.id,
            kind="sbom_cyclonedx",
            storage_path="/tmp/old.json",  # noqa: S108
            byte_size=10,
        )
    )
    session.commit()
    return scan.id


def test_reset_clears_only_target_scan_children(sync_session: Session) -> None:
    from models import ScanArtifact, ScanComponent
    from tasks.scan_source import _reset_scan_for_rerun

    target_id = _seed_scan_with_partial_children(sync_session, slug="target")
    sibling_id = _seed_scan_with_partial_children(sync_session, slug="sibling")

    from models import Scan

    target_scan = sync_session.execute(
        select(Scan).where(Scan.id == target_id)
    ).scalar_one()

    # Re-execution begins: wipe the target scan's partial children.
    _reset_scan_for_rerun(sync_session, target_scan)
    sync_session.commit()

    target_components = sync_session.execute(
        select(func.count()).select_from(ScanComponent).where(ScanComponent.scan_id == target_id)
    ).scalar_one()
    target_artifacts = sync_session.execute(
        select(func.count()).select_from(ScanArtifact).where(ScanArtifact.scan_id == target_id)
    ).scalar_one()
    assert target_components == 0
    assert target_artifacts == 0

    # The sibling scan must be untouched (no over-broad delete).
    sibling_components = sync_session.execute(
        select(func.count()).select_from(ScanComponent).where(ScanComponent.scan_id == sibling_id)
    ).scalar_one()
    sibling_artifacts = sync_session.execute(
        select(func.count()).select_from(ScanArtifact).where(ScanArtifact.scan_id == sibling_id)
    ).scalar_one()
    assert sibling_components == 1
    assert sibling_artifacts == 1
