"""
X1 SLA — container-scan ``first_detected_at`` carry-forward (real Postgres).

Container scans persist through their OWN boundary
(``tasks/scan_container.py::_persist_trivy_report``), not
``services.vulnerability_matching.persist_trivy_findings`` — so the source-path
SLA tests (``test_first_detected_sla_db.py``) prove nothing about images.
Before the fix, every container re-scan reset the SLA clock to "now".

Hardening rule #5 (lifecycle sequences): the defect only shows across a
persist(scan1) → persist(scan2) sequence, so both tests drive that sequence.

Hardening rule #3 (recorded fixtures): the report is the RECORDED real
``trivy image`` output — ``tests/fixtures/trivy/alpine-3.19-image-report.json``
(5 packages, 10 CVEs, every package multi-CVE) — the same document the H-1
regression suite (``test_container_multi_cve.py``) replays. The "new pair"
case derives a subset of that recording (one CVE dropped), never hand-built
minimal JSON.

Containers have no rematch path, so there is no ``prior_first_detected``
overlay sequence to cover here — the project-wide inherited map is the whole
mechanism (rationale in ``_persist_trivy_report``).
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import ComponentVersion, Vulnerability, VulnerabilityFinding
from tests._db_required import migrate_to_head

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
FIXTURE = BACKEND_ROOT / "tests" / "fixtures" / "trivy" / "alpine-3.19-image-report.json"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


def _report() -> dict[str, Any]:
    report: dict[str, Any] = json.loads(FIXTURE.read_text())
    return report


def _seed_project_with_container_scans(
    n_scans: int,
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Seed org/team/project + ``n_scans`` container scans (async helpers)."""
    import asyncio

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

    async def _build() -> tuple[uuid.UUID, list[uuid.UUID]]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            project = await make_project(s, team=team, git_url=None)
            scan_ids = []
            for _ in range(n_scans):
                scan = await make_scan(
                    s,
                    project=project,
                    requested_by=user,
                    kind="container",
                    status="succeeded",
                )
                scan_ids.append(scan.id)
            project_id = project.id
        await engine.dispose()
        return project_id, scan_ids

    return asyncio.run(_build())


def _findings(session: Session, scan_id: uuid.UUID) -> list[VulnerabilityFinding]:
    return list(
        session.execute(
            select(VulnerabilityFinding).where(VulnerabilityFinding.scan_id == scan_id)
        ).scalars()
    )


def _pair_map(
    rows: list[VulnerabilityFinding],
) -> dict[tuple[uuid.UUID, uuid.UUID], VulnerabilityFinding]:
    return {(f.component_version_id, f.vulnerability_id): f for f in rows}


def _resolve_pair(
    session: Session, *, vuln: dict[str, Any]
) -> tuple[uuid.UUID, uuid.UUID]:
    """Resolve one recorded vulnerability entry to its catalog ids.

    Mirrors the persist path's key derivation: the component version is named
    by the PURL Trivy attached to the finding, with qualifiers dropped, and the
    vulnerability is matched by external_id. Deriving the PURL from the report
    rather than rebuilding it here keeps this test honest — a hardcoded
    ``pkg:apk/{name}@{version}`` was how the persist path's own wrong PURL went
    unnoticed.
    """
    purl = str(vuln["PkgIdentifier"]["PURL"]).split("?", 1)[0]
    cv_id = session.execute(
        select(ComponentVersion.id).where(
            ComponentVersion.purl_with_version == purl
        )
    ).scalar_one()
    vuln_id = session.execute(
        select(Vulnerability.id).where(
            Vulnerability.external_id == vuln["VulnerabilityID"]
        )
    ).scalar_one()
    return cv_id, vuln_id


def test_container_rescan_carries_first_detected_forward(
    sync_session: Session,
) -> None:
    """persist(scan1) → persist(scan2): every pair inherits scan1's clock."""
    project_id, (scan1, scan2) = _seed_project_with_container_scans(2)
    backdated = datetime.now(UTC).replace(microsecond=0) - timedelta(days=30)

    from tasks.scan_container import _persist_trivy_report

    # Scan 1: fresh persist — every finding stamps a non-NULL clock start.
    _persist_trivy_report(sync_session, scan_uuid=scan1, report=_report())
    sync_session.commit()
    scan1_rows = _findings(sync_session, scan1)
    assert len(scan1_rows) == 10  # recorded fixture: 5 packages × 2 CVEs
    assert all(f.first_detected_at is not None for f in scan1_rows)

    # Backdate scan1's clock so inheritance is distinguishable from "now".
    for f in scan1_rows:
        f.first_detected_at = backdated
    sync_session.commit()

    # Scan 2 (the container re-scan): same image report → inherit, not reset.
    _persist_trivy_report(sync_session, scan_uuid=scan2, report=_report())
    sync_session.commit()

    scan2_map = _pair_map(_findings(sync_session, scan2))
    scan1_map = _pair_map(scan1_rows)
    assert set(scan2_map) == set(scan1_map)  # same (cv × vuln) pairs
    for key, finding in scan2_map.items():
        assert finding.first_detected_at == backdated, key


def test_container_rescan_stamps_now_for_new_pairs_only(
    sync_session: Session,
) -> None:
    """A CVE unseen by scan1 stamps "now" on scan2; known pairs still inherit.

    scan1 replays the recording MINUS one CVE (a subset of real output, kept
    multi-CVE dense); scan2 replays the full recording — the dropped pair is
    the "newly detected" vulnerability.
    """
    project_id, (scan1, scan2) = _seed_project_with_container_scans(2)
    backdated = datetime.now(UTC).replace(microsecond=0) - timedelta(days=30)

    full_report = _report()
    reduced_report = copy.deepcopy(full_report)
    dropped = reduced_report["Results"][0]["Vulnerabilities"].pop(0)
    assert reduced_report["Results"][0]["Vulnerabilities"], (
        "fixture lost its multi-CVE density — re-record from a real image"
    )

    from tasks.scan_container import _persist_trivy_report

    _persist_trivy_report(sync_session, scan_uuid=scan1, report=reduced_report)
    sync_session.commit()
    scan1_rows = _findings(sync_session, scan1)
    assert len(scan1_rows) == 9
    for f in scan1_rows:
        f.first_detected_at = backdated
    sync_session.commit()

    stamp_floor = datetime.now(UTC)
    _persist_trivy_report(sync_session, scan_uuid=scan2, report=full_report)
    sync_session.commit()

    scan2_map = _pair_map(_findings(sync_session, scan2))
    assert len(scan2_map) == 10

    new_pair = _resolve_pair(sync_session, vuln=dropped)
    for key, finding in scan2_map.items():
        assert finding.first_detected_at is not None, key
        if key == new_pair:
            # Never observed before this scan → fresh "now" stamp.
            assert finding.first_detected_at >= stamp_floor, key
        else:
            assert finding.first_detected_at == backdated, key
