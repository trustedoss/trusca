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

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import ScanComponent, VulnerabilityFinding
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


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
    """A Trivy vulnerability entry for the openssl apk package.

    ``PkgIdentifier`` carries the shape a real ``trivy image`` writes — it is
    what persistence reads to name the package's ecosystem, and a fixture
    without it exercises a report Trivy does not produce.
    """
    return {
        "PkgName": "openssl",
        "InstalledVersion": "3.1.4-r5",
        "VulnerabilityID": cve_id,
        "Severity": "HIGH",
        "Title": f"{cve_id} in openssl",
        "Description": "fabricated for the H-1 regression test",
        "References": ["https://example.test/" + cve_id],
        "PkgIdentifier": {
            "PURL": "pkg:apk/alpine/openssl@3.1.4-r5?arch=x86_64&distro=3.19.9",
            "UID": "e1f2a3b4c5d60718",
        },
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
                "Class": "os-pkgs",
                "Type": "alpine",
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
                "Class": "os-pkgs",
                "Type": "alpine",
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
                        "PkgIdentifier": {
                            "PURL": (
                                "pkg:apk/alpine/musl@1.2.4-r2"
                                "?arch=x86_64&distro=3.19.9"
                            ),
                            "UID": "0a1b2c3d4e5f6071",
                        },
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


# ---------------------------------------------------------------------------
# Ecosystem identity — the package type a component is persisted under
# ---------------------------------------------------------------------------


def _persisted_identity(
    session: Session, scan_id: uuid.UUID
) -> set[tuple[str, str, str]]:
    """(component purl, purl_with_version, package_type) for one scan."""
    from models import Component, ComponentVersion

    rows = session.execute(
        select(
            Component.purl,
            ComponentVersion.purl_with_version,
            Component.package_type,
        )
        .join(ComponentVersion, ComponentVersion.component_id == Component.id)
        .join(
            ScanComponent,
            ScanComponent.component_version_id == ComponentVersion.id,
        )
        .where(ScanComponent.scan_id == scan_id)
    ).all()
    return {(r[0], r[1], r[2]) for r in rows}


def test_rpm_image_persists_rpm_components(sync_session: Session) -> None:
    """A Rocky image's packages are rpms, and must be stored as rpms.

    Persistence hardcoded ``pkg:apk/{name}@{version}`` and ``package_type
    "apk"`` for every package in every image, so an rpm image's inventory
    claimed an ecosystem the image does not have. Nothing failed — the counts
    were right and only the identity was wrong, which is why it survived.

    Fixture: recorded from ``trivy image rockylinux:9-minimal`` (2026-08),
    trimmed to the six most-affected packages and three CVEs each; each entry
    is otherwise the tool's own output. ``Packages`` is narrowed to the same
    six so the file stays reviewable — persistence reads ``Vulnerabilities``.
    """
    import json

    fixture = (
        BACKEND_ROOT / "tests" / "fixtures" / "trivy" / "rocky-9-image-report.json"
    )
    report = json.loads(fixture.read_text())
    scan_id = _seed_queued_container_scan()

    from tasks.scan_container import _persist_trivy_report

    _persist_trivy_report(sync_session, scan_uuid=scan_id, report=report)
    sync_session.commit()

    identities = _persisted_identity(sync_session, scan_id)
    assert identities, "the recorded report must persist components"
    for component_purl, purl_with_version, package_type in identities:
        assert package_type == "rpm", (component_purl, package_type)
        # The distro namespace Trivy states is kept: it is what distinguishes
        # a Rocky rpm from a RHEL one of the same name and version.
        assert component_purl.startswith("pkg:rpm/rocky/"), component_purl
        assert purl_with_version.startswith(component_purl + "@")
        # Qualifiers are dropped — ``distro=rocky-9.3`` would make every point
        # release of the base image a new component version and reset the
        # first-detected clock for the whole image.
        assert "?" not in purl_with_version, purl_with_version


def test_mixed_image_keeps_each_ecosystem_apart(sync_session: Session) -> None:
    """One image, two ecosystems: debian packages and a pip package.

    The old hardcoding collapsed both into apk, so ``pip`` inside a python
    image was inventoried as an alpine package.

    Fixture: recorded from ``trivy image python:3.12-slim`` (2026-08), trimmed
    the same way as the Rocky one.
    """
    import json

    fixture = (
        BACKEND_ROOT
        / "tests"
        / "fixtures"
        / "trivy"
        / "debian-python-image-report.json"
    )
    report = json.loads(fixture.read_text())
    scan_id = _seed_queued_container_scan()

    from tasks.scan_container import _persist_trivy_report

    _persist_trivy_report(sync_session, scan_uuid=scan_id, report=report)
    sync_session.commit()

    identities = _persisted_identity(sync_session, scan_id)
    types = {package_type for _, _, package_type in identities}
    assert types == {"deb", "pypi"}, types

    by_type = {
        package_type: component_purl for component_purl, _, package_type in identities
    }
    assert by_type["deb"].startswith("pkg:deb/debian/")
    assert by_type["pypi"].startswith("pkg:pypi/")


def test_a_finding_without_a_usable_identity_is_skipped(
    sync_session: Session,
) -> None:
    """No PURL and no mappable Type means the package cannot be named.

    Calling it apk is what this change exists to stop, so the finding is
    skipped and logged instead. Trivy always writes one of the two, making
    this a malformed-report guard rather than a live path.
    """
    scan_id = _seed_queued_container_scan()

    report = {
        "Results": [
            {
                "Target": "mystery:latest",
                "Class": "os-pkgs",
                "Type": "an-ecosystem-nobody-maps",
                "Vulnerabilities": [
                    {
                        "PkgName": "mystery-pkg",
                        "InstalledVersion": "1.0",
                        "VulnerabilityID": "CVE-2026-9999",
                        "Severity": "HIGH",
                    }
                ],
            }
        ]
    }

    from tasks.scan_container import _persist_trivy_report

    _persist_trivy_report(sync_session, scan_uuid=scan_id, report=report)
    sync_session.commit()

    assert _persisted_identity(sync_session, scan_id) == set()
    finding_count = sync_session.execute(
        select(func.count())
        .select_from(VulnerabilityFinding)
        .where(VulnerabilityFinding.scan_id == scan_id)
    ).scalar_one()
    assert finding_count == 0
