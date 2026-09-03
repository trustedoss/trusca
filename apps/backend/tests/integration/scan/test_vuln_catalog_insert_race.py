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
from sqlalchemy import inspect as sa_inspect
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


def _scan_is_locked_elsewhere(
    factory: sessionmaker[Session], scan_id: uuid.UUID
) -> bool:
    """Does a SEPARATE session find the scan row already claimed?

    ``skip_locked=True`` never blocks: it returns no row when someone else
    holds the lock, which is exactly the question the rematch task's claim
    depends on.
    """
    probe = factory()
    try:
        row = probe.execute(
            select(Scan).where(Scan.id == scan_id).with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        probe.rollback()
        return row is None
    finally:
        probe.close()


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
    # One create audit row per finding, no more and no fewer. Before ER8 this
    # read as twelve audit rows over seven findings.
    assert audit_targets == finding_ids


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
        # The lock has to still be ours. A session-wide rollback ends the
        # transaction, and PostgreSQL releases the row lock with it, so
        # another worker could claim a scan this task believes it owns. Ask
        # from a second session while this one is still open: skip_locked
        # returns nothing when the row is held.
        still_locked = _scan_is_locked_elsewhere(session_factory, scan_id)
        session.commit()
    finally:
        session.close()
    thief.join(10)
    _assert_race_actually_fired(logs, raced_cve)
    assert still_locked, "the race released the row lock the task means to hold"

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


def test_the_audit_writer_ignores_a_rolled_back_finding(
    session_factory: sessionmaker[Session],
    seeded: tuple[uuid.UUID, dict[str, Any], str],
) -> None:
    """A finding whose INSERT was rolled back gets no audit row.

    The guard on its own, in the shape a rollback actually produces. Rolling
    a SAVEPOINT back expunges with ``to_transient=True``: the object goes
    TRANSIENT, not detached, and its attributes are never cleared, so the
    server-assigned PK is still readable on an object no row backs. Neither
    ``f.id is None`` nor ``detached`` sees that, which is why the check asks
    the session for membership instead.
    """
    scan_id, report, _raced_cve = seeded

    session = session_factory()
    try:
        persist_trivy_findings(session, scan_uuid=scan_id, trivy_report=report)
        session.commit()

        live = (
            session.execute(
                select(VulnerabilityFinding)
                .where(VulnerabilityFinding.scan_id == scan_id)
                .order_by(VulnerabilityFinding.id)
                .limit(1)
            )
            .scalars()
            .one()
        )

        # A catalog row of its own, so the rolled-back finding does not
        # collide with ``live`` on uq_vuln_findings_scan_version_vuln.
        other_vuln = Vulnerability(
            external_id=f"CVE-2097-{uuid.uuid4().hex[:8]}",
            source="trivy",
            severity="high",
        )
        session.add(other_vuln)
        session.commit()

        # Build the rolled-back shape for real: flush inside a SAVEPOINT so
        # the PK is assigned, then roll that SAVEPOINT back.
        rolled_back = VulnerabilityFinding(
            scan_id=scan_id,
            component_version_id=live.component_version_id,
            vulnerability_id=other_vuln.id,
            status="new",
        )
        nested = session.begin_nested()
        session.add(rolled_back)
        session.flush()
        rolled_back_id = str(rolled_back.id)
        nested.rollback()

        state = sa_inspect(rolled_back)
        assert state.transient, "a rolled-back INSERT leaves the object transient"
        assert not state.detached, "so a detached check would never fire"
        assert rolled_back.id is not None, "and the PK survives on the attribute"
        assert rolled_back not in session

        emitted = emit_finding_create_audits(
            session, scan_uuid=scan_id, findings=[live, rolled_back]
        )
        session.commit()
    finally:
        session.close()

    verify = session_factory()
    try:
        targets = list(
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

    assert emitted == 1, "only the finding that still has a row is audited"
    assert rolled_back_id not in targets, (
        "an audit row names a finding whose INSERT was rolled back"
    )
    # Membership, not multiplicity: how many audit rows the live finding
    # already carries from the persist above is incidental to the claim, and
    # asserting a count made this fail on a run that wrote one more.
    assert str(live.id) in targets


# ---------------------------------------------------------------------------
# Container path (ER8, second arm)
#
# The container persister hand-rolled its own catalog INSERT with no race
# handling at all, and that flush also writes the ScanComponent and
# VulnerabilityFinding rows staged earlier in the loop, so a unique violation
# aborted the whole transaction and failed the scan outright. It now goes
# through the same upsert. OS packages are the likeliest collision there is:
# glibc, openssl, musl and busybox appear in nearly every image, and source
# scans feed the same catalog table.
# ---------------------------------------------------------------------------

IMAGE_FIXTURE = (
    BACKEND_ROOT / "tests" / "fixtures" / "trivy" / "alpine-3.19-image-report.json"
)
# Fifth of ten in the recording, so four findings are already staged when the
# race fires.
CONTAINER_RACED_CVE_BASE = "CVE-2026-40200"
CONTAINER_TOTAL_FINDINGS = 10


def _seed_container_scan() -> uuid.UUID:
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
                kind="container",
                status="running",
                progress_percent=60,
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


def _namespace_image_report(report: dict[str, Any], suffix: str) -> dict[str, Any]:
    """Per-run PURLs and CVE ids, for the reason ``_namespace_report`` gives."""
    for result in report["Results"]:
        for vuln in result.get("Vulnerabilities") or []:
            name = vuln["PkgName"]
            vuln["PkgIdentifier"]["PURL"] = vuln["PkgIdentifier"]["PURL"].replace(
                f"/{name}@", f"/{name}-{suffix}@"
            )
            vuln["PkgName"] = f"{name}-{suffix}"
            vuln["VulnerabilityID"] = f"{vuln['VulnerabilityID']}-{suffix}"
    return report


def test_a_catalog_race_does_not_fail_a_container_scan(
    session_factory: sessionmaker[Session],
) -> None:
    """A concurrent catalog insert costs the container scan nothing.

    Before ER8 the losing flush aborted the transaction and the exception rose
    all the way to ``_mark_failed``: a benign collision on a package as common
    as musl took down the whole scan and every row it had staged.
    """
    from tasks.scan_container import _persist_trivy_report

    scan_id = _seed_container_scan()
    suffix = uuid.uuid4().hex[:8]
    report = _namespace_image_report(
        json.loads(IMAGE_FIXTURE.read_text()), suffix
    )
    raced_cve = f"{CONTAINER_RACED_CVE_BASE}-{suffix}"

    ready = threading.Event()
    thief = _steal_catalog_row(session_factory, raced_cve, ready)
    assert ready.wait(10), "second session never opened its insert"

    session = session_factory()
    try:
        with capture_logs() as logs:
            _persist_trivy_report(session, scan_uuid=scan_id, report=report)
        session.commit()
    finally:
        session.close()
    thief.join(10)

    raced = [
        entry["external_id"]
        for entry in logs
        if entry.get("event") == "vuln_catalog_insert_race"
    ]
    assert raced == [raced_cve], (
        f"expected one catalog race on {raced_cve}, saw {raced}. The two "
        "sessions did not collide, so this run asserts nothing about the defect."
    )
    summaries = [
        entry for entry in logs if entry.get("event") == "container_findings_persisted"
    ]
    assert summaries and summaries[-1]["catalog_races"] == 1

    verify = session_factory()
    try:
        stored = verify.execute(
            select(func.count())
            .select_from(VulnerabilityFinding)
            .where(VulnerabilityFinding.scan_id == scan_id)
        ).scalar_one()
        components = verify.execute(
            select(func.count())
            .select_from(ScanComponent)
            .where(ScanComponent.scan_id == scan_id)
        ).scalar_one()
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

    assert stored == CONTAINER_TOTAL_FINDINGS
    assert components == 5, "one ScanComponent per package, not per CVE (H-1)"
    assert audit_targets == finding_ids
