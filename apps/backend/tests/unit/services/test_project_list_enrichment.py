"""
#25 — project-list row enrichment (latest scan status + severity summary) and
the overview's ``last_succeeded_scan_at``.

These cover the two read-only additions:

TASK 1 — ``services.project_list_enrichment.enrich_project_rows`` returns, for a
page of project rows, two BATCHED maps:
  * ``latest_scan_status`` from the project's latest scan *attempt*
    (``Project.latest_scan_id``) — drives the status badge (None ⇒ "Idle"),
  * ``severity_summary`` from the project's latest *succeeded* scan
    (``services.scan_resolution.latest_succeeded_scan_id``) — drives the risk
    indicator (None ⇒ no succeeded scan).

The headline case is the verified ``ci-vulns`` shape: an EARLIER succeeded scan
with findings (incl. critical) followed by a LATER FAILED attempt, with
``latest_scan_id`` pointing at the failed attempt. The row must show
``latest_scan_status="failed"`` AND a NON-null severity summary from the
succeeded scan.

TASK 2 — ``get_project_overview`` returns ``last_succeeded_scan_at`` = the
latest succeeded scan's ``created_at`` (the same anchor sbom_export uses), or null
when there is no succeeded scan. ``last_scan_at`` (the last *attempt*) is kept.

Batched-ness: ``enrich_project_rows`` issues exactly 3 statements regardless of
page size (status map / succeeded-id map / one grouped severity aggregation). The
batching test asserts the statement count does NOT grow with the number of
projects (no per-row query explosion), via a SQLAlchemy ``before_cursor_execute``
counter.

Runs against the real Postgres (CLAUDE.md core rule #1) — the severity
aggregation depends on live ENUM / CASE behaviour; mocking would test the mock.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event
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

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip project-list enrichment tests")
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
            "alembic upgrade head failed; enrichment tests cannot run\n"
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
# Inline factories (repo convention — no shared finding helpers exist).
# ---------------------------------------------------------------------------


async def _make_scan_at(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    status: str,
    created_at: datetime,
):
    from models import Scan

    scan = Scan(
        project_id=project_id,
        kind="source",
        status=status,
        progress_percent=100 if status == "succeeded" else 0,
        scan_metadata={},
        created_at=created_at,
        completed_at=created_at if status == "succeeded" else None,
    )
    session.add(scan)
    await session.commit()
    await session.refresh(scan)
    return scan


async def _make_component_version(session: AsyncSession):
    from models import Component, ComponentVersion

    suffix = unique_suffix()
    cname = f"enrich-pkg-{suffix}"
    purl = f"pkg:npm/{cname}"
    component = Component(purl=purl, package_type="npm", name=cname)
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
    return component, cv


async def _attach_component(session: AsyncSession, *, scan_id: uuid.UUID, cv_id: uuid.UUID):
    from models import ScanComponent

    sc = ScanComponent(
        scan_id=scan_id,
        component_version_id=cv_id,
        direct=True,
        depth=1,
        raw_data={},
    )
    session.add(sc)
    await session.commit()


async def _make_vulnerability(session: AsyncSession, *, severity: str):
    from models import Vulnerability

    v = Vulnerability(
        external_id=f"CVE-2024-{unique_suffix()}",
        source="NVD",
        severity=severity,
        summary=f"{severity} finding {unique_suffix()}",
    )
    session.add(v)
    await session.commit()
    await session.refresh(v)
    return v


async def _attach_vuln_finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    vulnerability_id: uuid.UUID,
):
    from models import VulnerabilityFinding

    vf = VulnerabilityFinding(
        scan_id=scan_id,
        component_version_id=cv_id,
        vulnerability_id=vulnerability_id,
        status="new",
        analysis_state="new",
    )
    session.add(vf)
    await session.commit()


async def _set_latest_scan(session: AsyncSession, *, project, scan_id: uuid.UUID) -> None:
    project.latest_scan_id = scan_id
    project.updated_at = datetime.now(tz=UTC)
    await session.commit()
    await session.refresh(project)


async def _ci_vulns_like_project(db_session: AsyncSession):
    """The verified ``ci-vulns`` shape: earlier SUCCEEDED scan (10 critical) then
    a later FAILED attempt; ``latest_scan_id`` → the failed attempt.

    Returns ``(team, user, project, succeeded, failed)``.
    """
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)

    base = datetime.now(tz=UTC) - timedelta(hours=3)
    succeeded = await _make_scan_at(
        db_session, project_id=project.id, status="succeeded", created_at=base
    )
    failed = await _make_scan_at(
        db_session,
        project_id=project.id,
        status="failed",
        created_at=base + timedelta(hours=1),
    )
    # The denormalized pointer tracks the LAST ATTEMPT (the failed scan).
    await _set_latest_scan(db_session, project=project, scan_id=failed.id)

    # 10 critical-CVE components + 1 high-CVE component on the SUCCEEDED scan.
    for _ in range(10):
        _, cv = await _make_component_version(db_session)
        await _attach_component(db_session, scan_id=succeeded.id, cv_id=cv.id)
        crit = await _make_vulnerability(db_session, severity="critical")
        await _attach_vuln_finding(
            db_session, scan_id=succeeded.id, cv_id=cv.id, vulnerability_id=crit.id
        )
    _, cv_high = await _make_component_version(db_session)
    await _attach_component(db_session, scan_id=succeeded.id, cv_id=cv_high.id)
    high = await _make_vulnerability(db_session, severity="high")
    await _attach_vuln_finding(
        db_session, scan_id=succeeded.id, cv_id=cv_high.id, vulnerability_id=high.id
    )

    return team, user, project, succeeded, failed


# ---------------------------------------------------------------------------
# TASK 1 — enrich_project_rows
# ---------------------------------------------------------------------------


async def test_failed_latest_attempt_keeps_severity_from_earlier_succeeded_scan(
    db_session: AsyncSession,
) -> None:
    """Headline case: latest attempt FAILED but an earlier scan SUCCEEDED →
    ``latest_scan_status="failed"`` AND non-null severity summary (10 critical)."""
    from services.project_list_enrichment import enrich_project_rows

    _team, _user, project, _succeeded, _failed = await _ci_vulns_like_project(db_session)

    status_map, sev_map = await enrich_project_rows(db_session, projects=[project])

    assert status_map[project.id] == "failed"
    assert project.id in sev_map
    summary = sev_map[project.id]
    assert summary["critical"] == 10
    assert summary["high"] == 1
    assert summary["medium"] == 0
    assert summary["low"] == 0


async def test_never_scanned_project_both_null(db_session: AsyncSession) -> None:
    """A project with no scan at all: status absent (→ Idle) and no severity."""
    from services.project_list_enrichment import enrich_project_rows

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)

    status_map, sev_map = await enrich_project_rows(db_session, projects=[project])

    assert project.id not in status_map  # caller maps absence → None ("Idle")
    assert project.id not in sev_map  # caller maps absence → null severity_summary


async def test_succeeded_scan_with_no_cves_is_all_zero_not_null(
    db_session: AsyncSession,
) -> None:
    """A succeeded scan with no CVE findings yields a non-null all-zero summary —
    distinguishable from 'never succeeded' (absent)."""
    from services.project_list_enrichment import enrich_project_rows

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    base = datetime.now(tz=UTC) - timedelta(hours=1)
    succeeded = await _make_scan_at(
        db_session, project_id=project.id, status="succeeded", created_at=base
    )
    _, cv = await _make_component_version(db_session)
    await _attach_component(db_session, scan_id=succeeded.id, cv_id=cv.id)
    await _set_latest_scan(db_session, project=project, scan_id=succeeded.id)

    status_map, sev_map = await enrich_project_rows(db_session, projects=[project])

    assert status_map[project.id] == "succeeded"
    assert project.id in sev_map
    assert sev_map[project.id] == {"critical": 0, "high": 0, "medium": 0, "low": 0}


async def test_info_severity_findings_excluded_from_summary_buckets(
    db_session: AsyncSession,
) -> None:
    """An ``info`` (and ``unknown``→info-rank) CVE on the succeeded scan does NOT
    contribute to the four risk buckets — the list-row indicator only surfaces
    critical/high/medium/low."""
    from services.project_list_enrichment import enrich_project_rows

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    base = datetime.now(tz=UTC) - timedelta(hours=1)
    succeeded = await _make_scan_at(
        db_session, project_id=project.id, status="succeeded", created_at=base
    )
    # One info-severity finding + one low-severity finding.
    _, cv_info = await _make_component_version(db_session)
    await _attach_component(db_session, scan_id=succeeded.id, cv_id=cv_info.id)
    info = await _make_vulnerability(db_session, severity="info")
    await _attach_vuln_finding(
        db_session, scan_id=succeeded.id, cv_id=cv_info.id, vulnerability_id=info.id
    )
    _, cv_low = await _make_component_version(db_session)
    await _attach_component(db_session, scan_id=succeeded.id, cv_id=cv_low.id)
    low = await _make_vulnerability(db_session, severity="low")
    await _attach_vuln_finding(
        db_session, scan_id=succeeded.id, cv_id=cv_low.id, vulnerability_id=low.id
    )
    await _set_latest_scan(db_session, project=project, scan_id=succeeded.id)

    _status_map, sev_map = await enrich_project_rows(db_session, projects=[project])

    # The info component is excluded; only the low one counts.
    assert sev_map[project.id] == {"critical": 0, "high": 0, "medium": 0, "low": 1}


async def test_only_failed_scan_status_failed_but_no_severity(
    db_session: AsyncSession,
) -> None:
    """A project whose ONLY scan failed: status 'failed', no severity summary."""
    from services.project_list_enrichment import enrich_project_rows

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    base = datetime.now(tz=UTC) - timedelta(hours=1)
    failed = await _make_scan_at(
        db_session, project_id=project.id, status="failed", created_at=base
    )
    await _set_latest_scan(db_session, project=project, scan_id=failed.id)

    status_map, sev_map = await enrich_project_rows(db_session, projects=[project])

    assert status_map[project.id] == "failed"
    assert project.id not in sev_map


async def test_empty_page_issues_no_sql(db_session: AsyncSession) -> None:
    from services.project_list_enrichment import enrich_project_rows

    status_map, sev_map = await enrich_project_rows(db_session, projects=[])
    assert (status_map, sev_map) == ({}, {})


async def test_enrichment_is_batched_not_per_row(db_session: AsyncSession) -> None:
    """The statement count must NOT grow with the number of projects.

    We enrich a 1-project page and a 5-project page and assert both issue the
    SAME (constant) number of SQL statements — proving there is no per-row query
    explosion (DoD §2).
    """
    from services.project_list_enrichment import enrich_project_rows

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)

    # Build 5 projects, each with a succeeded scan carrying one critical finding.
    projects = []
    for _ in range(5):
        project = await make_project(db_session, team=team)
        base = datetime.now(tz=UTC) - timedelta(hours=1)
        succeeded = await _make_scan_at(
            db_session, project_id=project.id, status="succeeded", created_at=base
        )
        _, cv = await _make_component_version(db_session)
        await _attach_component(db_session, scan_id=succeeded.id, cv_id=cv.id)
        crit = await _make_vulnerability(db_session, severity="critical")
        await _attach_vuln_finding(
            db_session, scan_id=succeeded.id, cv_id=cv.id, vulnerability_id=crit.id
        )
        await _set_latest_scan(db_session, project=project, scan_id=succeeded.id)
        projects.append(project)

    engine = db_session.get_bind()
    sync_engine = getattr(engine, "sync_engine", engine)

    counter = {"n": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        counter["n"] += 1

    event.listen(sync_engine, "before_cursor_execute", _count)
    try:
        counter["n"] = 0
        await enrich_project_rows(db_session, projects=projects[:1])
        one_row_stmts = counter["n"]

        counter["n"] = 0
        await enrich_project_rows(db_session, projects=projects)
        five_row_stmts = counter["n"]
    finally:
        event.remove(sync_engine, "before_cursor_execute", _count)

    # Constant statement count regardless of page size — no N+1.
    assert one_row_stmts == five_row_stmts
    # And it is a small constant (status map + succeeded-id map + severity agg).
    assert five_row_stmts <= 3

    # Correctness over the 5-project page: each has 1 critical.
    _status_map, sev_map = await enrich_project_rows(db_session, projects=projects)
    for project in projects:
        assert sev_map[project.id]["critical"] == 1


# ---------------------------------------------------------------------------
# TASK 2 — overview last_succeeded_scan_at
# ---------------------------------------------------------------------------


async def test_overview_last_succeeded_scan_at_is_the_succeeded_scan_time(
    db_session: AsyncSession,
) -> None:
    """``last_succeeded_scan_at`` is the succeeded scan's created_at (NOT the later
    failed attempt's), while ``last_scan_at`` stays the latest attempt's time."""
    from services.project_detail_service import get_project_overview

    team, user, project, succeeded, failed = await _ci_vulns_like_project(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    overview = await get_project_overview(db_session, project_id=project.id, actor=actor)

    assert overview["last_succeeded_scan_at"] == succeeded.created_at
    # last_scan_at tracks the latest ATTEMPT (the failed scan), which is newer.
    assert overview["last_scan_at"] == failed.created_at
    assert overview["last_succeeded_scan_at"] < overview["last_scan_at"]


async def test_overview_last_succeeded_scan_at_null_when_no_succeeded_scan(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import get_project_overview

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    base = datetime.now(tz=UTC) - timedelta(hours=1)
    failed = await _make_scan_at(
        db_session, project_id=project.id, status="failed", created_at=base
    )
    await _set_latest_scan(db_session, project=project, scan_id=failed.id)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    overview = await get_project_overview(db_session, project_id=project.id, actor=actor)

    assert overview["last_succeeded_scan_at"] is None
    # A project with only a failed attempt still surfaces last_scan_at — it tracks
    # the latest *attempt* (#29), regardless of status. Only last_succeeded_scan_at
    # gates on a succeeded scan existing.
    assert overview["last_scan_at"] == failed.created_at
