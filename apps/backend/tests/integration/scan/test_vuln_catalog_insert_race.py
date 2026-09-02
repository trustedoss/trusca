"""Catalog INSERT race must not take the caller's findings with it (ER8).

``services/vulnerability_matching._upsert_vulnerability_from_trivy`` creates the
``vulnerabilities`` row a finding points at. Two workers scanning at once both
miss the same ``external_id`` and both INSERT it; the loser's flush raises a
unique violation. Postgres leaves an errored transaction aborted, so the loser
has to roll something back before it can continue, and rolling back the whole
session transaction discards every finding already staged in it. Nothing raises,
so the scan still reports success with fewer findings than it counted.

These run against the real Postgres because the defect IS a unique-constraint
violation across two live connections; no mock reproduces it. The second session
holds an UNCOMMITTED insert while the first one runs, which is what makes the
first one's own INSERT block on the unique index and then fail. Committing the
second session first would make the first session's SELECT find the row and take
the ordinary path instead of the race.

Fixture: ``tests/fixtures/trivy/centos7-rpm-sbom-report.json``, a real
``trivy sbom`` recording: twelve findings over three packages, four CVEs each.
The density is the point: the race is armed on a CVE in the middle so findings
are already staged when it fires, which is exactly what a hand-written
one-CVE report cannot express.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker
from structlog.testing import capture_logs

from models import (
    AuditLog,
    Component,
    ComponentVersion,
    Scan,
    ScanComponent,
    Vulnerability,
    VulnerabilityFinding,
)
from services.vulnerability_matching import (
    emit_finding_create_audits,
    persist_trivy_findings,
)
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
FIXTURE = BACKEND_ROOT / "tests" / "fixtures" / "trivy" / "centos7-rpm-sbom-report.json"

pytestmark = pytest.mark.integration

# The CVE the second session steals. Sixth of twelve in the recording, so five
# findings are already staged in the caller's transaction when the race fires.
RACED_CVE_BASE = "CVE-2024-33599"
TOTAL_FINDINGS = 12
PER_PACKAGE = {"curl": 4, "glibc": 4, "openssl": 4}
# How long the second session keeps its uncommitted INSERT open. The first
# session blocks on the unique index for this long, so it has to outlast the
# scheduling jitter of handing control back to the main thread.
HOLD_SECONDS = 3.0


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping catalog-race integration")
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
            f"alembic upgrade head failed; catalog-race integration cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    from core.config import database_url_sync

    engine = create_engine(database_url_sync(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    try:
        yield factory
    finally:
        engine.dispose()


def _report() -> dict[str, Any]:
    report: dict[str, Any] = json.loads(FIXTURE.read_text())
    return report


def _fixture_packages(report: dict[str, Any]) -> list[tuple[str, str, str]]:
    """``(name, version, purl)`` for every package the recording mentions.

    Read out of the fixture rather than hard-coded so the seeded components
    always match what the recording actually says. The PURL is Trivy's own
    ``PkgIdentifier.PURL`` with qualifiers stripped, which is the form
    ``_purl_from_identifier`` hands the component lookup for os-pkgs Results.
    """
    seen: dict[tuple[str, str], str] = {}
    for result in report["Results"]:
        for vuln in result.get("Vulnerabilities") or []:
            purl = vuln["PkgIdentifier"]["PURL"].split("?", 1)[0]
            seen[(vuln["PkgName"], vuln["InstalledVersion"])] = purl
    return [(name, version, purl) for (name, version), purl in seen.items()]


def _namespace_report(report: dict[str, Any], suffix: str) -> dict[str, Any]:
    """Give this run its own PURLs and CVE ids.

    ``component_versions.purl_with_version`` and ``vulnerabilities.external_id``
    are both globally unique, and the catalog outlives any one scan. Without a
    per-run namespace the second test would find the first test's catalog row
    already committed and take the ordinary path: no race, and a green test
    that proves nothing. Only the identifiers move; the shape, the packages and
    the per-package CVE density stay as Trivy recorded them.
    """
    for result in report["Results"]:
        for vuln in result.get("Vulnerabilities") or []:
            name = vuln["PkgName"]
            purl = vuln["PkgIdentifier"]["PURL"]
            vuln["PkgIdentifier"]["PURL"] = purl.replace(f"/{name}@", f"/{name}-{suffix}@")
            vuln["VulnerabilityID"] = f"{vuln['VulnerabilityID']}-{suffix}"
    return report


@pytest.fixture
def seeded(
    session_factory: sessionmaker[Session],
) -> Iterator[tuple[uuid.UUID, dict[str, Any], str]]:
    """A scan, its components, the namespaced recording, and the raced CVE id."""
    report = _report()
    suffix = uuid.uuid4().hex[:8]
    session = session_factory()
    try:
        scan_id = _seed_scan_with_components(session, report, suffix)
        session.commit()
        yield scan_id, _namespace_report(report, suffix), f"{RACED_CVE_BASE}-{suffix}"
    finally:
        session.close()


def _seed_scan_with_components(
    session: Session, report: dict[str, Any], suffix: str
) -> uuid.UUID:
    scan_id = _seed_scan()
    for name, version, purl in _fixture_packages(report):
        unique_purl = purl.replace(f"/{name}@", f"/{name}-{suffix}@")
        component = Component(
            purl=unique_purl.split("@", 1)[0],
            package_type="rpm",
            name=f"{name}-{suffix}",
        )
        session.add(component)
        session.flush()
        cv = ComponentVersion(
            component_id=component.id,
            version=version,
            purl_with_version=unique_purl,
        )
        session.add(cv)
        session.flush()
        session.add(
            ScanComponent(
                scan_id=scan_id,
                component_version_id=cv.id,
                direct=True,
                raw_data={},
            )
        )
    session.flush()
    return scan_id


def _seed_scan() -> uuid.UUID:
    """Org / team / user / project / succeeded scan, committed on its own."""
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
            scan = Scan(
                project_id=project.id,
                kind="source",
                status="succeeded",
                progress_percent=100,
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


def _steal_catalog_row(
    factory: sessionmaker[Session], external_id: str, ready: threading.Event
) -> threading.Thread:
    """Second worker: INSERT the catalog row, hold it uncommitted, then commit.

    Holding it uncommitted is what arms the race. The first session cannot see
    the row, inserts its own, and blocks on the unique index until this commit
    turns the block into a unique violation.
    """

    def _run() -> None:
        session = factory()
        try:
            session.add(
                Vulnerability(external_id=external_id, source="trivy", severity="high")
            )
            session.flush()
            ready.set()
            time.sleep(HOLD_SECONDS)
            session.commit()
        finally:
            session.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def _findings_by_package(session: Session, scan_id: uuid.UUID) -> dict[str, int]:
    rows = session.execute(
        select(Component.name, func.count(VulnerabilityFinding.id))
        .join(ComponentVersion, ComponentVersion.component_id == Component.id)
        .join(
            VulnerabilityFinding,
            VulnerabilityFinding.component_version_id == ComponentVersion.id,
        )
        .where(VulnerabilityFinding.scan_id == scan_id)
        .group_by(Component.name)
    ).all()
    # Strip the per-run namespace suffix so the assertion reads as the fixture.
    return {name.rsplit("-", 1)[0]: count for name, count in rows}


def _assert_race_actually_fired(
    entries: list[dict[str, Any]], raced_cve: str, *, expected: int = 1
) -> None:
    """The race branch was entered, not merely survived.

    ``_upsert_vulnerability_from_trivy`` swallows the unique violation, so a
    test that only checks the finding count passes just as happily when the
    two sessions never collided: the second session commits first, the first
    session's SELECT finds the row, and the ordinary path runs. That green
    proves nothing. Count what had to be called.
    """
    raced = [
        entry["external_id"]
        for entry in entries
        if entry.get("event") == "vuln_catalog_insert_race"
    ]
    assert raced == [raced_cve] * expected, (
        f"expected {expected} catalog race(s) on {raced_cve}, saw {raced}. "
        "The two sessions did not collide, so this run exercised the ordinary "
        "path and asserts nothing about the defect."
    )
    summaries = [
        entry for entry in entries if entry.get("event") == "trivy_findings_persisted"
    ]
    assert summaries, "persist_trivy_findings did not reach its summary log"
    assert summaries[-1]["catalog_races"] == expected


def test_findings_staged_before_a_catalog_race_are_not_discarded(
    session_factory: sessionmaker[Session],
    seeded: tuple[uuid.UUID, dict[str, Any], str],
) -> None:
    """Every finding in the report persists, race or no race.

    Before ER8 this lost the five findings staged ahead of the raced CVE while
    still returning twelve, so the count the pipeline logs and the rows a user
    can see disagreed with nobody raising.
    """
    scan_id, report, raced_cve = seeded
    ready = threading.Event()
    thief = _steal_catalog_row(session_factory, raced_cve, ready)
    assert ready.wait(10), "second session never opened its insert"

    session = session_factory()
    try:
        with capture_logs() as logs:
            inserted = persist_trivy_findings(
                session, scan_uuid=scan_id, trivy_report=report
            )
        session.commit()
    finally:
        session.close()
    thief.join(10)
    _assert_race_actually_fired(logs, raced_cve)

    verify = session_factory()
    try:
        stored = verify.execute(
            select(func.count())
            .select_from(VulnerabilityFinding)
            .where(VulnerabilityFinding.scan_id == scan_id)
        ).scalar_one()
        by_package = _findings_by_package(verify, scan_id)
        raced_present = verify.execute(
            select(func.count())
            .select_from(VulnerabilityFinding)
            .join(
                Vulnerability,
                Vulnerability.id == VulnerabilityFinding.vulnerability_id,
            )
            .where(VulnerabilityFinding.scan_id == scan_id)
            .where(Vulnerability.external_id == raced_cve)
        ).scalar_one()
    finally:
        verify.close()

    assert stored == TOTAL_FINDINGS
    assert inserted == stored, "the reported count must match what committed"
    assert by_package == PER_PACKAGE
    assert raced_present == 1, "the raced CVE reuses the winner's catalog row"


def test_a_catalog_race_does_not_leave_orphan_audit_rows(
    session_factory: sessionmaker[Session],
    seeded: tuple[uuid.UUID, dict[str, Any], str],
) -> None:
    """One create audit row per finding that exists, and none for one that does not.

    A rolled-back finding keeps its assigned PK on the attribute while the
    object leaves the session, so emitting audits straight off the staged list
    recorded a detection with no finding behind it. These rows ARE the
    compliance evidence chain, so over-reporting is the failure to guard.
    """
    scan_id, report, raced_cve = seeded
    ready = threading.Event()
    thief = _steal_catalog_row(session_factory, raced_cve, ready)
    assert ready.wait(10), "second session never opened its insert"

    session = session_factory()
    try:
        with capture_logs() as logs:
            persist_trivy_findings(session, scan_uuid=scan_id, trivy_report=report)
        session.commit()
    finally:
        session.close()
    thief.join(10)
    _assert_race_actually_fired(logs, raced_cve)

    verify = session_factory()
    try:
        audit_targets = set(
            verify.execute(
                select(AuditLog.target_id)
                .where(AuditLog.target_table == "vulnerability_findings")
                .where(AuditLog.diff["scan_id"].astext == str(scan_id))
            )
            .scalars()
            .all()
        )
        finding_ids = {
            str(row)
            for row in verify.execute(
                select(VulnerabilityFinding.id).where(
                    VulnerabilityFinding.scan_id == scan_id
                )
            )
            .scalars()
            .all()
        }
    finally:
        verify.close()

    # Equality first: on the defect this reads as five audit rows naming
    # finding ids that no longer exist, which is the claim being guarded.
    assert audit_targets == finding_ids
    assert len(finding_ids) == TOTAL_FINDINGS


def test_a_catalog_race_during_rematch_does_not_trip_the_finding_constraint(
    session_factory: sessionmaker[Session],
    seeded: tuple[uuid.UUID, dict[str, Any], str],
) -> None:
    """The rematch task's wipe-and-replace survives a race in the same transaction.

    ``tasks/vulnerability_rematch`` DELETEs the scan's findings and re-persists
    them inside one transaction, under a row lock it means to hold for the whole
    task. A session-wide rollback undid the DELETE too, so re-inserting the same
    finding violated ``uq_vuln_findings_scan_version_vuln`` and the task died
    with an IntegrityError, and the rollback had already dropped the row lock.
    """
    scan_id, report, raced_cve = seeded

    first = session_factory()
    try:
        persist_trivy_findings(first, scan_uuid=scan_id, trivy_report=report)
        first.commit()
    finally:
        first.close()

    # Send the raced CVE back to "not in the catalog yet", the state a fresh
    # deployment or a catalog rebuild leaves it in.
    reset = session_factory()
    try:
        reset.execute(
            delete(Vulnerability).where(Vulnerability.external_id == raced_cve)
        )
        reset.commit()
        prior = reset.execute(
            select(func.count())
            .select_from(VulnerabilityFinding)
            .where(VulnerabilityFinding.scan_id == scan_id)
        ).scalar_one()
    finally:
        reset.close()
    assert prior == TOTAL_FINDINGS - 1, "the cascade removed only the raced finding"

    ready = threading.Event()
    thief = _steal_catalog_row(session_factory, raced_cve, ready)
    assert ready.wait(10), "second session never opened its insert"

    session = session_factory()
    try:
        locked = session.execute(
            select(Scan).where(Scan.id == scan_id).with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        assert locked is not None, "the rematch pattern starts by claiming the scan"
        session.execute(
            delete(VulnerabilityFinding).where(
                VulnerabilityFinding.scan_id == scan_id
            )
        )
        session.flush()
        with capture_logs() as logs:
            inserted = persist_trivy_findings(
                session, scan_uuid=scan_id, trivy_report=report
            )
        session.flush()
        session.commit()
    finally:
        session.close()
    thief.join(10)
    _assert_race_actually_fired(logs, raced_cve)

    verify = session_factory()
    try:
        stored = verify.execute(
            select(func.count())
            .select_from(VulnerabilityFinding)
            .where(VulnerabilityFinding.scan_id == scan_id)
        ).scalar_one()
    finally:
        verify.close()

    assert inserted == TOTAL_FINDINGS
    assert stored == TOTAL_FINDINGS


def test_the_audit_writer_ignores_a_finding_that_left_the_session(
    session_factory: sessionmaker[Session],
    seeded: tuple[uuid.UUID, dict[str, Any], str],
) -> None:
    """A detached finding gets no audit row even though its PK is still set.

    The guard on its own, without the race that produced the situation. A
    rolled-back INSERT leaves exactly this shape behind: the object is out of
    the session, its server-assigned id is still on the attribute, and the row
    is gone. ``emit_finding_create_audits`` is handed the staged list, so
    reading ``f.id`` alone would name a finding that no row backs.
    """
    scan_id, report, _raced_cve = seeded

    session = session_factory()
    try:
        persist_trivy_findings(session, scan_uuid=scan_id, trivy_report=report)
        session.commit()

        kept, dropped = (
            session.execute(
                select(VulnerabilityFinding)
                .where(VulnerabilityFinding.scan_id == scan_id)
                .order_by(VulnerabilityFinding.id)
                .limit(2)
            )
            .scalars()
            .all()
        )
        dropped_id = str(dropped.id)
        session.expunge(dropped)

        emitted = emit_finding_create_audits(
            session, scan_uuid=scan_id, findings=[kept, dropped]
        )
        session.commit()
    finally:
        session.close()

    verify = session_factory()
    try:
        targets = set(
            verify.execute(
                select(AuditLog.target_id)
                .where(AuditLog.target_table == "vulnerability_findings")
                .where(AuditLog.diff["scan_id"].astext == str(scan_id))
            )
            .scalars()
            .all()
        )
    finally:
        verify.close()

    assert emitted == 1
    # persist_trivy_findings already audited all twelve; the extra call added
    # one row for the live finding and none for the detached one.
    assert dropped_id in targets, "the detached finding's own earlier audit stays"
    assert len(targets) == TOTAL_FINDINGS
