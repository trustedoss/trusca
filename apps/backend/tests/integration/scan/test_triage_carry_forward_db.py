"""
Triage carry-forward across re-scans and re-matches (real Postgres).

The product's guide promises this in two places
(``docs-site/docs/user-guide/triage.md``):

    VEX triage feeds condition 1 directly: moving a finding to an excluded
    state removes it from the count on the NEXT SCAN.

    A triage decision you record now shows in the UI immediately but only
    changes the gate verdict on the next scan.

Both sentences only hold if a verdict outlives the scan it was recorded
against, and findings are per-scan rows: a new scan inserts new rows. Until
this test existed ``persist_trivy_findings`` stamped every one of them
``status="new"``, so a re-scan silently discarded the analyst's work — forty
findings triaged as "not affected" came back open, counting against the build
gate again, with the justification and the analyst's identity gone. The clock
beside it (``first_detected_at``) was carried forward deliberately; the verdict
was not, which is what made it read as an oversight rather than a decision.

Hardening rule #5 (lifecycle sequences): a single-action test cannot catch a
verdict that survives one write and dies on the next, so these drive the two
real sequences end to end through the SAME chokepoint the pipelines use, as
``test_first_detected_sla_db.py`` does for the SLA clock:

  1. Re-scan:   persist(scan1) → triage → persist(scan2)
  2. Re-match:  persist → triage → capture → DELETE → persist(prior)

Hardening rule #3 (recorded fixtures): the report is the recorded
realistic-density document (lodash carries THREE CVEs; two ecosystems), so the
per-pair mapping is exercised at a density a synthetic single-CVE blob would
not reach — inheriting "the project's verdict" instead of "this pair's verdict"
passes on one CVE and corrupts five.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from models import (
    Component,
    ComponentVersion,
    ScanComponent,
    VulnerabilityFinding,
)
from services.vulnerability_matching import (
    capture_triage_state,
    persist_trivy_findings,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
FIXTURES = BACKEND_ROOT / "tests" / "fixtures" / "sbom_ingest"

pytestmark = pytest.mark.integration

_FIXTURE_PURLS = (
    ("npm", "lodash", "4.17.19", "pkg:npm/lodash@4.17.19"),
    ("npm", "minimist", "1.2.5", "pkg:npm/minimist@1.2.5"),
    ("pypi", "jinja2", "2.11.2", "pkg:pypi/jinja2@2.11.2"),
)
_EXPECTED_FINDINGS = 5


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip triage carry-forward integration")
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
            "alembic upgrade head failed; triage carry-forward cannot run\n"
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


def _seed_project_with_scans(n_scans: int) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """Seed org/team/project + ``n_scans`` succeeded scans. Returns the user id
    too — the analyst identity has to survive the carry-forward as well."""
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

    async def _build() -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
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
            project_id, user_id = project.id, user.id
        await engine.dispose()
        return project_id, user_id, scan_ids

    return asyncio.run(_build())


def _seed_fixture_component_versions(
    session: Session, scan_ids: list[uuid.UUID]
) -> None:
    """Stand up the component graph the recorded report resolves against.

    Same helper as the SLA sibling: ``persist_trivy_findings`` matches on
    ``purl_with_version`` scoped to the scan's ``ScanComponent`` set, so every
    scan expected to match needs its own link rows.
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
                component = Component(purl=purl, package_type=package_type, name=name)
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
                    ScanComponent(scan_id=scan_id, component_version_id=cv.id)
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


def _triage_one(
    session: Session,
    row: VulnerabilityFinding,
    *,
    analyst_id: uuid.UUID,
    when: datetime,
) -> None:
    """Record a full manual verdict, the way the triage endpoint does."""
    # Exactly the fields `services.vulnerability_service` writes on a manual
    # transition. `analysis_response` is deliberately NOT among them — despite
    # the name it holds the raw Trivy entry for the scan, written by the
    # persist path, and is not part of the verdict.
    row.status = "not_affected"
    row.analysis_state = "not_affected"
    row.analysis_justification = "vulnerable_code_not_in_execute_path"
    row.analysis_source = "manual"
    row.analyst_user_id = analyst_id
    row.analyzed_at = when
    session.commit()


# ---------------------------------------------------------------------------
# Sequence 1 — a re-scan keeps the verdict
# ---------------------------------------------------------------------------


def test_rescan_carries_triage_forward(sync_session: Session) -> None:
    project_id, analyst_id, (scan1, scan2) = _seed_project_with_scans(2)
    _seed_fixture_component_versions(sync_session, [scan1, scan2])
    decided_at = datetime.now(UTC).replace(microsecond=0) - timedelta(days=3)

    inserted1 = persist_trivy_findings(
        sync_session, scan_uuid=scan1, trivy_report=_trivy_report()
    )
    sync_session.commit()
    assert inserted1 == _EXPECTED_FINDINGS
    scan1_rows = _findings(sync_session, scan1)
    assert {f.status for f in scan1_rows} == {"new"}

    # Triage exactly ONE of the five. The other four are the control: a
    # carry-forward that painted the whole project with one verdict would pass
    # an all-or-nothing assertion and be badly wrong.
    triaged_pair = sorted(_pair_map(scan1_rows))[0]
    _triage_one(
        sync_session,
        _pair_map(scan1_rows)[triaged_pair],
        analyst_id=analyst_id,
        when=decided_at,
    )

    inserted2 = persist_trivy_findings(
        sync_session, scan_uuid=scan2, trivy_report=_trivy_report()
    )
    sync_session.commit()
    assert inserted2 == _EXPECTED_FINDINGS

    scan2_map = _pair_map(_findings(sync_session, scan2))
    carried = scan2_map[triaged_pair]
    assert carried.status == "not_affected", (
        "the re-scan reopened a finding the analyst had excluded — the guide "
        "promises the opposite, and the build gate counts open findings"
    )
    assert carried.analysis_state == "not_affected"
    assert (
        carried.analysis_justification == "vulnerable_code_not_in_execute_path"
    )
    assert carried.analysis_source == "manual"
    # `analysis_response` must NOT come along: it is the raw Trivy entry for
    # THIS scan, so carrying it would pin a finding to the scanner output of an
    # older run — stale severity, stale references, stale fix version. The
    # field name invites exactly this mistake, which is why it is asserted.
    assert "PkgName" in carried.analysis_response, (
        "analysis_response should hold this scan's Trivy entry, not the "
        "verdict's payload"
    )
    # The audit trail travels with the verdict: who decided, and when they
    # decided it — not when this scan happened to run.
    assert carried.analyst_user_id == analyst_id
    assert carried.analyzed_at is not None
    assert carried.analyzed_at.replace(microsecond=0) == decided_at

    untouched = [scan2_map[p] for p in scan2_map if p != triaged_pair]
    assert len(untouched) == _EXPECTED_FINDINGS - 1
    assert {f.status for f in untouched} == {"new"}
    assert all(f.analyst_user_id is None for f in untouched)


def test_rescan_starts_a_changed_component_version_fresh(
    sync_session: Session,
) -> None:
    """A verdict belongs to a (component version × CVE) pair, not to a CVE.

    Upgrading the package produces a different pair, and the analyst's "not
    affected on 4.17.19" says nothing about the new version — inheriting it
    there would silently suppress a finding nobody has looked at.
    """
    project_id, analyst_id, (scan1, scan2) = _seed_project_with_scans(2)
    _seed_fixture_component_versions(sync_session, [scan1])
    persist_trivy_findings(
        sync_session, scan_uuid=scan1, trivy_report=_trivy_report()
    )
    sync_session.commit()
    for row in _findings(sync_session, scan1):
        _triage_one(
            sync_session, row, analyst_id=analyst_id, when=datetime.now(UTC)
        )

    # Scan 2 sees the same CVEs on BUMPED versions. The suffix is unique per
    # run because `component_versions.purl_with_version` is globally unique —
    # a fixed one collides with whatever a previous run left behind.
    bump = f"-next{uuid.uuid4().hex[:8]}"
    bumped = tuple(
        (pkg_type, name, f"{version}{bump}", f"{purl}{bump}")
        for pkg_type, name, version, purl in _FIXTURE_PURLS
    )
    for package_type, name, version, purl_with_version in bumped:
        purl = purl_with_version.rsplit("@", 1)[0]
        component = sync_session.execute(
            select(Component).where(Component.purl == purl)
        ).scalar_one()
        cv = ComponentVersion(
            component_id=component.id,
            version=version,
            purl_with_version=purl_with_version,
        )
        sync_session.add(cv)
        sync_session.flush()
        sync_session.add(ScanComponent(scan_id=scan2, component_version_id=cv.id))
    sync_session.commit()

    # The fixture is typed ``dict[str, object]``, so the nested walk needs the
    # shape spelled out rather than a bare index — ``mypy .`` (which CI runs
    # over the whole package, tests included) cannot narrow `object` for us.
    report = _trivy_report()
    results = cast(list[dict[str, Any]], report["Results"])
    for result in results:
        for vuln in cast(list[dict[str, Any]], result.get("Vulnerabilities", [])):
            vuln["InstalledVersion"] = f"{vuln['InstalledVersion']}{bump}"
    persist_trivy_findings(sync_session, scan_uuid=scan2, trivy_report=report)
    sync_session.commit()

    scan2_rows = _findings(sync_session, scan2)
    assert len(scan2_rows) == _EXPECTED_FINDINGS
    assert {f.status for f in scan2_rows} == {"new"}, (
        "a verdict recorded against one version leaked onto another"
    )


# ---------------------------------------------------------------------------
# Sequence 2 — a re-match keeps the verdict on a single-scan project
# ---------------------------------------------------------------------------


def test_rematch_wipe_and_replace_keeps_triage(sync_session: Session) -> None:
    """The rematch task DELETEs the scan's own rows and re-persists them.

    A project with one scan has no other scan for the carry-forward query to
    read, so the verdict survives only through the caller-captured overlay —
    exactly the shape the SLA clock already needed. Without it the weekly
    automatic rematch would quietly reopen every triaged finding in the
    deployment, which is worse than the re-scan case: nobody asked for it.
    """
    project_id, analyst_id, (scan1,) = _seed_project_with_scans(1)
    _seed_fixture_component_versions(sync_session, [scan1])
    persist_trivy_findings(
        sync_session, scan_uuid=scan1, trivy_report=_trivy_report()
    )
    sync_session.commit()

    rows = _findings(sync_session, scan1)
    triaged_pair = sorted(_pair_map(rows))[0]
    _triage_one(
        sync_session,
        _pair_map(rows)[triaged_pair],
        analyst_id=analyst_id,
        when=datetime.now(UTC),
    )

    # The task's own sequence: capture, delete, re-persist with the overlay.
    prior_triage = capture_triage_state(sync_session, scan_uuid=scan1)
    assert triaged_pair in prior_triage
    sync_session.execute(
        delete(VulnerabilityFinding).where(
            VulnerabilityFinding.scan_id == scan1
        )
    )
    sync_session.commit()

    persist_trivy_findings(
        sync_session,
        scan_uuid=scan1,
        trivy_report=_trivy_report(),
        prior_triage=prior_triage,
    )
    sync_session.commit()

    replaced = _pair_map(_findings(sync_session, scan1))
    assert replaced[triaged_pair].status == "not_affected"
    assert replaced[triaged_pair].analyst_user_id == analyst_id
    assert {
        f.status for p, f in replaced.items() if p != triaged_pair
    } == {"new"}
