"""
X1 SLA — ``first_detected_at`` carry-forward lifecycle (real Postgres).

Hardening rule #5 (lifecycle sequences): single-action tests cannot catch a
clock that resets across a sequence, so these tests drive the two real-world
sequences end to end through the SAME chokepoint the pipelines use
(``services.vulnerability_matching.persist_trivy_findings``):

  1. Re-scan:   persist(scan1) → persist(scan2)   — scan2's findings must
     inherit scan1's first detection via the project-wide GROUP BY query.
  2. Re-match:  persist → capture → DELETE → persist(prior) — the rematch
     task's wipe-and-replace on a SINGLE-scan project must preserve the clock
     via the ``prior_first_detected`` overlay (the group query cannot see the
     deleted rows).

Hardening rule #3 (recorded fixtures): the Trivy report is the recorded
realistic-density fixture (lodash carries THREE CVEs; two ecosystems), not a
synthetic 1-CVE blob — ``tests/fixtures/sbom_ingest/realistic-trivy-sbom.json``,
the same document the ingest-pipeline integration replays.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from models import (
    Component,
    ComponentVersion,
    ScanComponent,
    VulnerabilityFinding,
)
from services.vulnerability_matching import persist_trivy_findings

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
FIXTURES = BACKEND_ROOT / "tests" / "fixtures" / "sbom_ingest"

pytestmark = pytest.mark.integration

# PURLs matching the recorded fixture's Results — the same mapping
# ``_build_purl`` derives from (Type, PkgName, InstalledVersion).
_FIXTURE_PURLS = (
    ("npm", "lodash", "4.17.19", "pkg:npm/lodash@4.17.19"),
    ("npm", "minimist", "1.2.5", "pkg:npm/minimist@1.2.5"),
    ("pypi", "jinja2", "2.11.2", "pkg:pypi/jinja2@2.11.2"),
)
# The recorded report carries 5 findings total: lodash×3 CVEs, minimist×1,
# jinja2×1 — realistic density (multiple CVEs on one package).
_EXPECTED_FINDINGS = 5


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip first_detected SLA integration")
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
            "alembic upgrade head failed; first_detected SLA integration cannot run\n"
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


def _trivy_report() -> dict[str, object]:
    report: dict[str, object] = json.loads(
        (FIXTURES / "realistic-trivy-sbom.json").read_text()
    )
    return report


def _seed_project_with_scans(n_scans: int) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Seed org/team/project + ``n_scans`` succeeded scans (async helpers)."""
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
                    s, project=project, requested_by=user, status="succeeded"
                )
                scan_ids.append(scan.id)
            project_id = project.id
        await engine.dispose()
        return project_id, scan_ids

    return asyncio.run(_build())


def _seed_fixture_component_versions(
    session: Session, scan_ids: list[uuid.UUID]
) -> None:
    """Insert Component/ComponentVersion rows matching the fixture's PURLs and
    link each ``scan_id`` to them via ``ScanComponent``.

    ``persist_trivy_findings`` matches by ``purl_with_version`` AND scopes the
    lookup to the scan's ``ScanComponent`` set (so a qualifier-less PURL match
    can never bind a finding to another project's component). cdxgen owns the
    component graph in production and writes ScanComponent before matching; here
    we stand both up directly so the recorded Trivy report resolves. The re-scan
    SLA tests reuse the same versions across ``scan1``/``scan2``, so every scan
    that is expected to match must get its own ScanComponent link.
    """
    for package_type, name, version, purl_with_version in _FIXTURE_PURLS:
        purl = purl_with_version.rsplit("@", 1)[0]
        cv = session.execute(
            select(ComponentVersion).where(
                ComponentVersion.purl_with_version == purl_with_version
            )
        ).scalar_one_or_none()
        if cv is None:
            component = session.execute(
                select(Component).where(Component.purl == purl)
            ).scalar_one_or_none()
            if component is None:
                component = Component(
                    purl=purl, package_type=package_type, name=name
                )
                session.add(component)
                session.flush()
            cv = ComponentVersion(
                component_id=component.id,
                version=version,
                purl_with_version=purl_with_version,
            )
            session.add(cv)
            session.flush()
        for scan_id in scan_ids:
            linked = session.execute(
                select(ScanComponent).where(
                    ScanComponent.scan_id == scan_id,
                    ScanComponent.component_version_id == cv.id,
                )
            ).scalar_one_or_none()
            if linked is None:
                session.add(
                    ScanComponent(
                        scan_id=scan_id, component_version_id=cv.id
                    )
                )
    session.commit()


def _findings(session: Session, scan_id: uuid.UUID) -> list[VulnerabilityFinding]:
    return list(
        session.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.scan_id == scan_id
            )
        ).scalars()
    )


def _pair_map(
    rows: list[VulnerabilityFinding],
) -> dict[tuple[uuid.UUID, uuid.UUID], VulnerabilityFinding]:
    return {(f.component_version_id, f.vulnerability_id): f for f in rows}


def _capture_first_detected(
    session: Session, scan_id: uuid.UUID
) -> dict[tuple[uuid.UUID, uuid.UUID], datetime]:
    """The exact capture the rematch task performs before its DELETE."""
    return {
        (row[0], row[1]): row[2]
        for row in session.execute(
            select(
                VulnerabilityFinding.component_version_id,
                VulnerabilityFinding.vulnerability_id,
                func.coalesce(
                    VulnerabilityFinding.first_detected_at,
                    VulnerabilityFinding.created_at,
                ),
            ).where(VulnerabilityFinding.scan_id == scan_id)
        ).all()
        if row[2] is not None
    }


# ---------------------------------------------------------------------------
# Sequence 1 — re-scan carries the clock forward
# ---------------------------------------------------------------------------


def test_rescan_carries_first_detected_forward(sync_session: Session) -> None:
    project_id, (scan1, scan2) = _seed_project_with_scans(2)
    _seed_fixture_component_versions(sync_session, [scan1, scan2])
    backdated = datetime.now(UTC).replace(microsecond=0) - timedelta(days=30)

    # Scan 1: fresh persist — every finding stamps a non-NULL clock start.
    inserted1 = persist_trivy_findings(
        sync_session, scan_uuid=scan1, trivy_report=_trivy_report()
    )
    sync_session.commit()
    assert inserted1 == _EXPECTED_FINDINGS
    scan1_rows = _findings(sync_session, scan1)
    assert all(f.first_detected_at is not None for f in scan1_rows)

    # Backdate scan1's clock so inheritance is distinguishable from "now".
    for f in scan1_rows:
        f.first_detected_at = backdated
    sync_session.commit()

    # Scan 2 (the re-scan): same report → every pair inherits scan1's clock.
    inserted2 = persist_trivy_findings(
        sync_session, scan_uuid=scan2, trivy_report=_trivy_report()
    )
    sync_session.commit()
    assert inserted2 == _EXPECTED_FINDINGS

    scan2_map = _pair_map(_findings(sync_session, scan2))
    scan1_map = _pair_map(scan1_rows)
    assert set(scan2_map) == set(scan1_map)  # same (cv × vuln) pairs
    for key, finding in scan2_map.items():
        assert finding.first_detected_at == backdated, key


def test_rescan_falls_back_to_created_at_for_legacy_null_rows(
    sync_session: Session,
) -> None:
    """Pre-0041 rows (NULL first_detected_at) inherit via COALESCE(created_at)."""
    project_id, (scan1, scan2) = _seed_project_with_scans(2)
    _seed_fixture_component_versions(sync_session, [scan1, scan2])
    legacy_created = datetime.now(UTC).replace(microsecond=0) - timedelta(days=90)

    persist_trivy_findings(sync_session, scan_uuid=scan1, trivy_report=_trivy_report())
    sync_session.commit()

    # Simulate legacy rows: NULL first_detected_at, backdated created_at.
    for f in _findings(sync_session, scan1):
        f.first_detected_at = None
        f.created_at = legacy_created
    sync_session.commit()

    persist_trivy_findings(sync_session, scan_uuid=scan2, trivy_report=_trivy_report())
    sync_session.commit()

    for key, finding in _pair_map(_findings(sync_session, scan2)).items():
        assert finding.first_detected_at == legacy_created, key


# ---------------------------------------------------------------------------
# Sequence 2 — re-match (capture → DELETE → persist with prior)
# ---------------------------------------------------------------------------


def test_rematch_sequence_preserves_first_detected_on_single_scan_project(
    sync_session: Session,
) -> None:
    """Single-scan project: the group query sees NOTHING after the DELETE, so
    only the captured ``prior_first_detected`` overlay can preserve the clock —
    exactly the rematch task's sequence."""
    project_id, (scan1,) = _seed_project_with_scans(1)
    _seed_fixture_component_versions(sync_session, [scan1])
    backdated = datetime.now(UTC).replace(microsecond=0) - timedelta(days=45)

    persist_trivy_findings(sync_session, scan_uuid=scan1, trivy_report=_trivy_report())
    sync_session.commit()
    for f in _findings(sync_session, scan1):
        f.first_detected_at = backdated
    sync_session.commit()

    # The rematch task's sequence: capture → DELETE → persist(prior).
    captured = _capture_first_detected(sync_session, scan1)
    assert len(captured) == _EXPECTED_FINDINGS
    sync_session.execute(
        delete(VulnerabilityFinding).where(VulnerabilityFinding.scan_id == scan1)
    )
    sync_session.flush()

    inserted = persist_trivy_findings(
        sync_session,
        scan_uuid=scan1,
        trivy_report=_trivy_report(),
        prior_first_detected=captured,
    )
    sync_session.commit()
    assert inserted == _EXPECTED_FINDINGS

    for key, finding in _pair_map(_findings(sync_session, scan1)).items():
        assert finding.first_detected_at == backdated, key


def test_rematch_without_prior_capture_resets_clock(sync_session: Session) -> None:
    """Negative control: the SAME sequence WITHOUT the prior overlay resets the
    clock — proving the capture is what carries it (not an accident of the
    group query)."""
    project_id, (scan1,) = _seed_project_with_scans(1)
    _seed_fixture_component_versions(sync_session, [scan1])
    backdated = datetime.now(UTC).replace(microsecond=0) - timedelta(days=45)

    persist_trivy_findings(sync_session, scan_uuid=scan1, trivy_report=_trivy_report())
    sync_session.commit()
    for f in _findings(sync_session, scan1):
        f.first_detected_at = backdated
    sync_session.commit()

    sync_session.execute(
        delete(VulnerabilityFinding).where(VulnerabilityFinding.scan_id == scan1)
    )
    sync_session.flush()
    persist_trivy_findings(sync_session, scan_uuid=scan1, trivy_report=_trivy_report())
    sync_session.commit()

    for f in _findings(sync_session, scan1):
        assert f.first_detected_at is not None
        assert f.first_detected_at > backdated  # reset to "now", not preserved
