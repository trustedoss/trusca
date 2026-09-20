"""
How ``persist_trivy_findings`` binds a finding to a component (real Postgres).

The component lookup used to run once per finding. It now loads the scan's
components once (``_load_scan_component_index``). The per-finding query carried
two guarantees that the batch has to keep, and a unit test with a fake session
cannot see either of them, because both live in SQL:

1. Scope. The stored PURL is globally unique, so an unscoped match could bind a
   finding to a component version that belongs to a DIFFERENT scan. The index
   is joined to ``ScanComponent`` for the scan being persisted.
2. Determinism. Trivy omits PURL qualifiers and cdxgen keeps them, so one scan
   can carry two variants of one package. The pick is the first by
   ``ORDER BY purl_with_version``. If it flipped between runs, the
   ``component_version_id`` that keys ``first_detected_at`` would flip with it
   and reset the SLA clock.

Hardening rule #5 (lifecycle sequences): the determinism test persists, deletes
and persists again, because a pick that is stable inside one call and unstable
across calls is the failure that matters.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session

from models import Component, ComponentVersion, ScanComponent, VulnerabilityFinding
from services.vulnerability_matching import persist_trivy_findings
from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


def _seed_scans(n_scans: int) -> list[uuid.UUID]:
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from core.config import database_url
    from tests._helpers import (
        make_organization,
        make_project,
        make_scan,
        make_team,
        make_user,
    )

    async def _build() -> list[uuid.UUID]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            project = await make_project(s, team=team, git_url=None)
            ids = [
                (await make_scan(s, project=project, requested_by=user, status="succeeded")).id
                for _ in range(n_scans)
            ]
        await engine.dispose()
        return ids

    return asyncio.run(_build())


def _version(session: Session, *, name: str, purl_with_version: str) -> ComponentVersion:
    """One ComponentVersion, its Component reused when the package already exists."""
    purl = f"pkg:npm/{name}"
    component = session.execute(
        select(Component).where(Component.purl == purl)
    ).scalar_one_or_none()
    if component is None:
        component = Component(purl=purl, package_type="npm", name=name)
        session.add(component)
        session.flush()
    cv = ComponentVersion(
        component_id=component.id, version="1.0.0", purl_with_version=purl_with_version
    )
    session.add(cv)
    session.flush()
    return cv


def _report(*findings: tuple[str, str]) -> dict[str, Any]:
    """A Trivy report with one npm finding per ``(package, vulnerability id)``."""
    return {
        "SchemaVersion": 2,
        "Results": [
            {
                "Target": "package-lock.json",
                "Class": "lang-pkgs",
                "Type": "npm",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": vuln_id,
                        "PkgName": package,
                        "InstalledVersion": "1.0.0",
                        "Severity": "HIGH",
                    }
                    for package, vuln_id in findings
                ],
            }
        ],
    }


def _findings(session: Session, scan_id: uuid.UUID) -> list[VulnerabilityFinding]:
    return list(
        session.execute(
            select(VulnerabilityFinding).where(VulnerabilityFinding.scan_id == scan_id)
        ).scalars()
    )


def test_a_finding_never_binds_to_a_component_of_another_scan(
    sync_session: Session,
) -> None:
    """The same PURL is linked to scan A only; scan B must not pick it up."""
    scan_a, scan_b = _seed_scans(2)
    tag = uuid.uuid4().hex[:10]
    name, vuln_id = f"scoped{tag}", f"TEST-{tag}"
    cv = _version(sync_session, name=name, purl_with_version=f"pkg:npm/{name}@1.0.0")
    sync_session.add(ScanComponent(scan_id=scan_a, component_version_id=cv.id))
    sync_session.commit()

    report = _report((name, vuln_id))

    assert persist_trivy_findings(sync_session, scan_uuid=scan_b, trivy_report=report) == 0
    sync_session.commit()
    assert _findings(sync_session, scan_b) == []

    # The control: the scan that does carry the component binds normally, so the
    # zero above is the scope at work and not a report that matches nothing.
    assert persist_trivy_findings(sync_session, scan_uuid=scan_a, trivy_report=report) == 1
    sync_session.commit()
    (bound,) = _findings(sync_session, scan_a)
    assert bound.component_version_id == cv.id


def test_qualifier_variants_bind_to_the_same_version_on_every_run(
    sync_session: Session,
) -> None:
    """Two variants of one package in one scan: the first by ordering, every time."""
    (scan_id,) = _seed_scans(1)
    tag = uuid.uuid4().hex[:10]
    name, vuln_id = f"variant{tag}", f"TEST-{tag}"
    plain_purl = f"pkg:npm/{name}@1.0.0"
    # The variant that sorts SECOND is written first, in both tables. Without
    # that, physical order is already the sorted order and a query that lost
    # its ORDER BY would pass anyway, which is how this test first survived
    # that mutation.
    native = _version(
        sync_session, name=name, purl_with_version=f"{plain_purl}?type=native"
    )
    plain = _version(sync_session, name=name, purl_with_version=plain_purl)
    for cv in (native, plain):
        sync_session.add(ScanComponent(scan_id=scan_id, component_version_id=cv.id))
    sync_session.commit()

    report = _report((name, vuln_id))
    picked: list[uuid.UUID] = []
    for _ in range(3):
        assert (
            persist_trivy_findings(sync_session, scan_uuid=scan_id, trivy_report=report) == 1
        )
        sync_session.commit()
        picked.append(_findings(sync_session, scan_id)[0].component_version_id)
        sync_session.execute(
            delete(VulnerabilityFinding).where(VulnerabilityFinding.scan_id == scan_id)
        )
        sync_session.commit()

    assert picked == [plain.id] * 3


def test_component_queries_do_not_grow_with_the_finding_count(
    sync_session: Session,
) -> None:
    """One finding and twenty findings cost the same number of component queries."""
    (scan_id,) = _seed_scans(1)
    tag = uuid.uuid4().hex[:10]
    findings: list[tuple[str, str]] = []
    for i in range(20):
        name = f"bulk{tag}x{i}"
        cv = _version(sync_session, name=name, purl_with_version=f"pkg:npm/{name}@1.0.0")
        sync_session.add(ScanComponent(scan_id=scan_id, component_version_id=cv.id))
        findings.append((name, f"TEST-{tag}-{i}"))
    sync_session.commit()

    statements: list[str] = []

    def _record(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
        if "component_versions" in statement:
            statements.append(statement)

    def _count_for(report: dict[str, Any], scan: uuid.UUID) -> int:
        statements.clear()
        engine = sync_session.get_bind()
        event.listen(engine, "before_cursor_execute", _record)
        try:
            persist_trivy_findings(sync_session, scan_uuid=scan, trivy_report=report)
        finally:
            event.remove(engine, "before_cursor_execute", _record)
        sync_session.commit()
        return len(statements)

    one = _count_for(_report(findings[0]), scan_id)
    sync_session.execute(
        delete(VulnerabilityFinding).where(VulnerabilityFinding.scan_id == scan_id)
    )
    sync_session.commit()
    twenty = _count_for(_report(*findings), scan_id)

    assert one >= 1, "the listener saw no component query, so it counts nothing"
    assert twenty == one
    assert len(_findings(sync_session, scan_id)) == 20
