"""
Container scan persistence with multi-CVE packages — H-1 regression.

`tasks/scan_container.py` `_persist_trivy_report` writes a ScanComponent row
keyed on (scan_id, component_version_id, dependency_path) and a
VulnerabilityFinding per CVE. A single OS package routinely carries several
CVEs; the dependency_path is the shared Trivy *target* string, so creating one
ScanComponent per vulnerability violates ``uq_scan_components_scan_version_path``
and fails the whole scan with a UniqueViolation (the reported H-1 defect:
alpine:3.19 fails because openssl has >1 CVE).

This runs against the real Postgres because the bug *is* a DB unique-constraint
violation — a mock session would never surface it. We drive the persistence
helper directly with a fabricated Trivy report.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from models import ScanComponent, VulnerabilityFinding
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
        pytest.skip("DATABASE_URL not set — skip container multi-CVE integration")
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
            f"alembic upgrade head failed; container multi-CVE integration cannot run\n"
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


def _seed_queued_container_scan() -> uuid.UUID:
    """Seed a queued container scan and return its id."""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url

    async def _build() -> uuid.UUID:
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
                kind="container",
                status="queued",
                progress_percent=0,
                requested_by_user_id=user.id,
                scan_metadata={},
            )
            s.add(scan)
            await s.commit()
            await s.refresh(scan)
            scan_id = scan.id
        await engine.dispose()
        return scan_id

    return asyncio.run(_build())


def _vuln(cve_id: str) -> dict[str, object]:
    """A Trivy vulnerability entry for the openssl apk package."""
    return {
        "PkgName": "openssl",
        "InstalledVersion": "3.1.4-r5",
        "VulnerabilityID": cve_id,
        "Severity": "HIGH",
        "Title": f"{cve_id} in openssl",
        "Description": "fabricated for the H-1 regression test",
        "References": ["https://example.test/" + cve_id],
    }


def test_multi_cve_package_persists_one_component_and_all_findings(
    sync_session: Session,
) -> None:
    """openssl with three CVEs must not trip uq_scan_components_scan_version_path."""
    scan_id = _seed_queued_container_scan()

    report = {
        "Results": [
            {
                "Target": "alpine:3.19 (alpine 3.19.9)",
                "Vulnerabilities": [
                    _vuln("CVE-2026-0001"),
                    _vuln("CVE-2026-0002"),
                    _vuln("CVE-2026-0003"),
                ],
            }
        ]
    }

    from tasks.scan_container import _persist_trivy_report

    # Before the H-1 fix this raised sqlalchemy.exc.IntegrityError
    # (UniqueViolation) on commit.
    _persist_trivy_report(sync_session, scan_uuid=scan_id, report=report)
    sync_session.commit()

    component_count = sync_session.execute(
        select(func.count())
        .select_from(ScanComponent)
        .where(ScanComponent.scan_id == scan_id)
    ).scalar_one()
    finding_count = sync_session.execute(
        select(func.count())
        .select_from(VulnerabilityFinding)
        .where(VulnerabilityFinding.scan_id == scan_id)
    ).scalar_one()

    # One ScanComponent for the shared (openssl, target) pair...
    assert component_count == 1
    # ...but one finding per CVE.
    assert finding_count == 3


def test_real_alpine_report_persists_without_unique_violation(
    sync_session: Session,
) -> None:
    """Drive the persistence layer with a RECORDED real `trivy image` report.

    Testing-standards rule (realistic fixtures): H-1 survived our synthetic
    fixtures because they carried one CVE per package — real images carry
    several per package as the NORM (this recording: 5 packages, 10 CVEs,
    every package multi-CVE). Persist-boundary tests must use recorded real
    tool output, not hand-built minimal JSON.

    Fixture: tests/fixtures/trivy/alpine-3.19-image-report.json — recorded
    from the dev worker's `trivy image --format json alpine:3.19` (2026-06,
    public CVE data only). Assertions derive expected counts from the fixture
    itself so re-recording with a newer DB never breaks them.
    """
    import json

    fixture = (
        Path(__file__).resolve().parent.parent.parent
        / "fixtures"
        / "trivy"
        / "alpine-3.19-image-report.json"
    )
    report = json.loads(fixture.read_text())

    expected_components: set[tuple[str, str]] = set()
    expected_findings = 0
    for result in report.get("Results", []):
        for vuln in result.get("Vulnerabilities") or []:
            expected_components.add((vuln["PkgName"], vuln["InstalledVersion"]))
            expected_findings += 1
    assert expected_findings > len(expected_components), (
        "fixture lost its multi-CVE density — re-record from a real image"
    )

    scan_id = _seed_queued_container_scan()

    from tasks.scan_container import _persist_trivy_report

    _persist_trivy_report(sync_session, scan_uuid=scan_id, report=report)
    sync_session.commit()

    component_count = sync_session.execute(
        select(func.count())
        .select_from(ScanComponent)
        .where(ScanComponent.scan_id == scan_id)
    ).scalar_one()
    finding_count = sync_session.execute(
        select(func.count())
        .select_from(VulnerabilityFinding)
        .where(VulnerabilityFinding.scan_id == scan_id)
    ).scalar_one()

    assert component_count == len(expected_components)
    assert finding_count == expected_findings


def test_os_eosl_persisted_to_scan_metadata_from_real_report(
    sync_session: Session,
) -> None:
    """K-f1: the image OS / EOSL block lands in scan_metadata['os'] (no migration).

    Uses the recorded real report whose base image (alpine 3.19) is past EOL,
    so eosl is True — the case a badge must surface.
    """
    import json

    from models import Scan

    fixture = (
        Path(__file__).resolve().parent.parent.parent
        / "fixtures"
        / "trivy"
        / "alpine-3.19-image-report.json"
    )
    report = json.loads(fixture.read_text())

    scan_id = _seed_queued_container_scan()

    from tasks.scan_container import _persist_os_metadata

    # Opens and commits its own best-effort transaction.
    _persist_os_metadata(scan_uuid=scan_id, report=report)

    sync_session.expire_all()
    scan = sync_session.get(Scan, scan_id)
    assert scan is not None
    assert scan.scan_metadata.get("os") == {
        "family": "alpine",
        "name": "3.19.9",
        "eosl": True,
    }


def test_distinct_packages_get_distinct_components(sync_session: Session) -> None:
    """Two packages on the same target still get their own ScanComponent rows."""
    scan_id = _seed_queued_container_scan()

    report = {
        "Results": [
            {
                "Target": "alpine:3.19 (alpine 3.19.9)",
                "Vulnerabilities": [
                    _vuln("CVE-2026-0010"),
                    {
                        "PkgName": "musl",
                        "InstalledVersion": "1.2.4-r2",
                        "VulnerabilityID": "CVE-2026-0011",
                        "Severity": "MEDIUM",
                        "Title": "CVE-2026-0011 in musl",
                        "Description": "fabricated",
                        "References": [],
                    },
                ],
            }
        ]
    }

    from tasks.scan_container import _persist_trivy_report

    _persist_trivy_report(sync_session, scan_uuid=scan_id, report=report)
    sync_session.commit()

    component_count = sync_session.execute(
        select(func.count())
        .select_from(ScanComponent)
        .where(ScanComponent.scan_id == scan_id)
    ).scalar_one()

    assert component_count == 2
