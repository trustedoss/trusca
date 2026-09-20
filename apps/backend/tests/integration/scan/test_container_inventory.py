"""Container scans store the whole image inventory, not only vulnerable packages (U3-F).

Driven by a real ``trivy image --list-all-pkgs`` capture (see
``tests/fixtures/trivy/PROVENANCE.md``): 286 packages, of which far fewer carry
a CVE. Before U3-F the ScanComponent rows equalled the vulnerable packages, so
the SBOM export and the obligation views saw a fraction of the image.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import (
    ComponentDependencyEdge,
    LicenseFinding,
    Scan,
    ScanComponent,
    VulnerabilityFinding,
)
from tests._db_required import migrate_to_head
from tests.integration.scan.test_container_multi_cve import _seed_queued_container_scan

pytestmark = pytest.mark.integration

REPORT = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "trivy"
    / "node-22-bookworm-slim-list-all-pkgs-report.json"
)


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


def _report() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(REPORT.read_text(encoding="utf-8")))


def _package_count(report: dict[str, Any]) -> int:
    """Distinct (target, PURL without qualifiers) pairs.

    A component row is unique on (component version, target), and the same
    package installed at two paths under one Trivy target (11 of the 286 here)
    is one component.
    """
    return len(
        {
            (r["Target"], p["Identifier"]["PURL"].split("?", 1)[0])
            for r in report["Results"]
            for p in r.get("Packages") or []
        }
    )


def _vulnerable_package_count(report: dict[str, Any]) -> int:
    return len(
        {
            (r["Target"], v["PkgName"], v["InstalledVersion"])
            for r in report["Results"]
            for v in r.get("Vulnerabilities") or []
        }
    )


def _count(session: Session, model: Any, scan_id: uuid.UUID) -> int:
    return session.execute(
        select(func.count()).select_from(model).where(model.scan_id == scan_id)
    ).scalar_one()


def _persist(session: Session, scan_id: uuid.UUID, report: dict[str, Any]) -> None:
    from tasks.scan_container import _persist_trivy_report

    _persist_trivy_report(session, scan_uuid=scan_id, report=report)
    session.commit()


def test_every_listed_package_becomes_a_component(sync_session: Session) -> None:
    report = _report()
    scan_id = _seed_queued_container_scan()

    _persist(sync_session, scan_id, report)

    components = _count(sync_session, ScanComponent, scan_id)
    assert components == _package_count(report)
    # The point of U3-F: the inventory is larger than the vulnerable subset.
    assert components > _vulnerable_package_count(report) * 2
    assert _count(sync_session, VulnerabilityFinding, scan_id) == sum(
        len(r.get("Vulnerabilities") or []) for r in report["Results"]
    )


def test_declared_licenses_and_dependency_edges_are_stored(sync_session: Session) -> None:
    scan_id = _seed_queued_container_scan()
    _persist(sync_session, scan_id, _report())

    kinds = set(
        sync_session.execute(
            select(LicenseFinding.kind).where(LicenseFinding.scan_id == scan_id)
        ).scalars()
    )
    assert kinds == {"declared"}
    assert _count(sync_session, LicenseFinding, scan_id) > _package_count(_report())
    assert _count(sync_session, ComponentDependencyEdge, scan_id) > 50


def test_rerun_after_reset_gives_the_same_inventory(sync_session: Session) -> None:
    """Lifecycle: persist, reset for a re-run, persist again.

    ``_reset_for_rerun`` must clear the edges too; otherwise the second pass
    trips the edge unique constraint or doubles the graph.
    """
    from tasks.scan_container import _reset_for_rerun

    report = _report()
    scan_id = _seed_queued_container_scan()
    _persist(sync_session, scan_id, report)
    first = {m: _count(sync_session, m, scan_id) for m in (ScanComponent, ComponentDependencyEdge)}

    scan = sync_session.get(Scan, scan_id)
    assert scan is not None
    _reset_for_rerun(sync_session, scan)
    sync_session.commit()
    assert _count(sync_session, ComponentDependencyEdge, scan_id) == 0

    _persist(sync_session, scan_id, report)
    second = {m: _count(sync_session, m, scan_id) for m in (ScanComponent, ComponentDependencyEdge)}
    assert second == first


def test_sbom_export_lists_the_whole_image(sync_session: Session) -> None:
    """The export reads the stored inventory, so it now has more than the CVE packages."""
    import asyncio
    from datetime import UTC, datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url
    from services.sbom_export import export_sbom

    report = _report()
    scan_id = _seed_queued_container_scan()
    _persist(sync_session, scan_id, report)
    scan = sync_session.get(Scan, scan_id)
    assert scan is not None
    scan.status = "succeeded"
    scan.completed_at = datetime.now(UTC)
    sync_session.commit()
    project_id = scan.project_id

    async def _export() -> str:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        try:
            async with async_sessionmaker(engine, class_=AsyncSession)() as session:
                content, _ctype, _name = await export_sbom(
                    session, project_id=project_id, fmt="cyclonedx-json", scan_id=scan_id
                )
                return content
        finally:
            await engine.dispose()

    content = asyncio.run(_export())
    exported = len(json.loads(content)["components"])
    assert exported == _package_count(report)
    assert exported > _vulnerable_package_count(report) * 2


def test_a_package_with_many_licenses_keeps_all_of_them(sync_session: Session) -> None:
    """The recorded image has packages whose license list is a dozen entries.

    Joined with ``OR`` they overflow the 64-character id column and every
    license was dropped (51 packages). Each must stay a finding of its own.
    """
    from models import ComponentVersion, License

    report = _report()
    scan_id = _seed_queued_container_scan()
    _persist(sync_session, scan_id, report)

    wide = max(
        (p for r in report["Results"] for p in r["Packages"] if p.get("Licenses")),
        key=lambda p: len(p["Licenses"]),
    )
    assert len(wide["Licenses"]) >= 10
    stored = set(
        sync_session.execute(
            select(License.spdx_id)
            .join(LicenseFinding, LicenseFinding.license_id == License.id)
            .join(ComponentVersion, ComponentVersion.id == LicenseFinding.component_version_id)
            .where(
                LicenseFinding.scan_id == scan_id,
                ComponentVersion.purl_with_version == wide["Identifier"]["PURL"].split("?", 1)[0],
            )
        ).scalars()
    )
    # Ids Trivy wrote that are plain SPDX tokens must all be there; free text
    # such as "GPL-2+ with Texinfo exception" is not asserted either way.
    plain = {x for x in wide["Licenses"] if " " not in x}
    assert plain <= stored
