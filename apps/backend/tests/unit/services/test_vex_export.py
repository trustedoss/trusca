"""
Tests for ``services/vex_export.py`` — v2.1 Track A (A1).

Two tiers live here:

1. **Pure-unit** tests (no DB): the internal→VEX status maps must be total over
   ``VULN_FINDING_STATUS_VALUES`` and produce the documented targets for all 7
   states × 2 formats. These run anywhere (no ``DATABASE_URL`` required).

2. **DB-backed** tests (marked ``integration``): the exporter reads from
   PostgreSQL (Project / Scan / VulnerabilityFinding / Vulnerability /
   ComponentVersion / Component); mocking the session would test the mock
   instead of the SQL aggregation. These need the real Postgres service and are
   skipped when ``DATABASE_URL`` is unset.

Coverage targets:
- mapping correctness (7 states × 2 formats), justification → free-text field;
- empty project (no scan, or no succeeded scan) → well-formed empty doc;
- unknown format → :class:`VEXUnsupportedFormat` (422);
- byte-stability (same scan re-exported = identical bytes);
- deterministic doc id / timestamp (scan-derived, not wall clock);
- VEX document schema shape.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models.scan import VULN_FINDING_STATUS_VALUES
from services.vex_export import (
    _OPENVEX_STATUS_MAP,
    CYCLONEDX_STATE_MAP,
    SUPPORTED_FORMATS,
    VEXUnsupportedFormat,
)
from tests._helpers import (
    make_organization,
    make_project,
    make_scan,
    make_team,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# ===========================================================================
# Tier 1 — pure-unit (no DB). Status mapping + format catalogue.
# ===========================================================================


def test_supported_formats_are_openvex_and_cyclonedx() -> None:
    assert set(SUPPORTED_FORMATS) == {"openvex", "cyclonedx"}


@pytest.mark.parametrize(
    ("internal", "expected"),
    [
        ("new", "under_investigation"),
        ("analyzing", "under_investigation"),
        ("exploitable", "affected"),
        ("not_affected", "not_affected"),
        ("false_positive", "not_affected"),
        ("suppressed", "not_affected"),
        ("fixed", "fixed"),
    ],
)
def test_openvex_status_map(internal: str, expected: str) -> None:
    assert _OPENVEX_STATUS_MAP[internal] == expected


@pytest.mark.parametrize(
    ("internal", "expected"),
    [
        ("new", "in_triage"),
        ("analyzing", "in_triage"),
        ("exploitable", "exploitable"),
        ("not_affected", "not_affected"),
        ("false_positive", "false_positive"),
        ("suppressed", "not_affected"),
        ("fixed", "resolved"),
    ],
)
def test_cyclonedx_state_map(internal: str, expected: str) -> None:
    assert CYCLONEDX_STATE_MAP[internal] == expected


def test_status_maps_are_total_over_finding_enum() -> None:
    """A schema-drift guard: every DB finding status must have a VEX mapping.

    If a future migration adds an 8th finding state, this test fails loudly
    rather than the exporter throwing KeyError at runtime on real data.
    """
    enum = set(VULN_FINDING_STATUS_VALUES)
    assert set(_OPENVEX_STATUS_MAP) == enum
    assert set(CYCLONEDX_STATE_MAP) == enum


# ===========================================================================
# Tier 2 — DB-backed (PostgreSQL). Marked integration.
# ===========================================================================


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip vex export DB tests")
    return url


@pytest.fixture(scope="module")
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
            f"alembic upgrade head failed; vex export tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def db_session(_migrate_once: None) -> AsyncIterator[AsyncSession]:
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)

    async with factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


async def _make_component_version(
    session: AsyncSession,
    *,
    name: str,
    version: str,
    namespace: str | None = None,
    package_type: str = "npm",
):
    from models import Component, ComponentVersion

    purl = f"pkg:{package_type}/{namespace + '/' if namespace else ''}{name}"
    component = Component(
        purl=purl,
        package_type=package_type,
        name=name,
        namespace=namespace,
    )
    session.add(component)
    await session.commit()
    await session.refresh(component)

    cv = ComponentVersion(
        component_id=component.id,
        version=version,
        purl_with_version=f"{purl}@{version}",
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)
    return component, cv


async def _make_vulnerability(
    session: AsyncSession,
    *,
    severity: str = "high",
    cve_id: str | None = None,
    source: str = "NVD",
):
    from models import Vulnerability

    suffix = unique_suffix()
    v = Vulnerability(
        external_id=cve_id or f"CVE-2099-{suffix}",
        source=source,
        severity=severity,
        summary=f"Test vuln {suffix}",
    )
    session.add(v)
    await session.commit()
    await session.refresh(v)
    return v


async def _attach_finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    vulnerability_id: uuid.UUID,
    status: str,
    justification: str | None = None,
):
    from models import VulnerabilityFinding

    vf = VulnerabilityFinding(
        scan_id=scan_id,
        component_version_id=cv_id,
        vulnerability_id=vulnerability_id,
        status=status,
        analysis_state=status,
        analysis_justification=justification,
    )
    session.add(vf)
    await session.commit()
    await session.refresh(vf)
    return vf


async def _make_project_with_succeeded_scan(session: AsyncSession):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    project = await make_project(session, team=team)
    scan = await make_scan(session, project=project, status="succeeded")
    project.latest_scan_id = scan.id
    await session.commit()
    await session.refresh(project)
    return team, project, scan


async def _seed_one_finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    status: str,
    cve_id: str,
    justification: str | None = None,
):
    suffix = unique_suffix()
    _, cv = await _make_component_version(
        session, name=f"pkg-{suffix}", version="1.0.0"
    )
    vuln = await _make_vulnerability(session, cve_id=cve_id)
    await _attach_finding(
        session,
        scan_id=scan_id,
        cv_id=cv.id,
        vulnerability_id=vuln.id,
        status=status,
        justification=justification,
    )
    return cv, vuln


# ---------------------------------------------------------------------------
# Unknown format → 422
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_unknown_format_raises_unsupported(db_session: AsyncSession) -> None:
    from services.vex_export import export_vex

    _, project, _ = await _make_project_with_succeeded_scan(db_session)
    with pytest.raises(VEXUnsupportedFormat):
        await export_vex(db_session, project_id=project.id, fmt="not-a-format")


@pytest.mark.integration
async def test_missing_project_raises_unsupported(db_session: AsyncSession) -> None:
    """The router checks IDOR + existence first; this branch is defense-in-depth."""
    from services.vex_export import export_vex

    with pytest.raises(VEXUnsupportedFormat):
        await export_vex(db_session, project_id=uuid.uuid4(), fmt="openvex")


# ---------------------------------------------------------------------------
# Empty project — well-formed empty document
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_openvex_empty_project(db_session: AsyncSession) -> None:
    from services.vex_export import export_vex

    _, project, _ = await _make_project_with_succeeded_scan(db_session)
    body, content_type, filename = await export_vex(
        db_session, project_id=project.id, fmt="openvex"
    )

    assert content_type == "application/json"
    assert filename == f"vex-{project.slug}.openvex.json"

    parsed = json.loads(body)
    assert parsed["@context"] == "https://openvex.dev/ns/v0.2.0"
    assert parsed["@id"].startswith("https://trustedoss.io/vex/")
    assert parsed["author"] == "TrustedOSS Portal"
    assert "timestamp" in parsed
    assert parsed["version"] == 1
    assert parsed["statements"] == []


@pytest.mark.integration
async def test_cyclonedx_empty_project(db_session: AsyncSession) -> None:
    from services.vex_export import export_vex

    _, project, _ = await _make_project_with_succeeded_scan(db_session)
    body, content_type, filename = await export_vex(
        db_session, project_id=project.id, fmt="cyclonedx"
    )

    assert content_type == "application/json"
    assert filename == f"vex-{project.slug}.vex.cdx.json"

    parsed = json.loads(body)
    assert parsed["bomFormat"] == "CycloneDX"
    assert parsed["specVersion"] == "1.5"
    assert parsed["serialNumber"].startswith("urn:uuid:")
    assert parsed["metadata"]["component"]["type"] == "application"
    assert parsed["metadata"]["component"]["name"] == project.name
    assert parsed["vulnerabilities"] == []


@pytest.mark.integration
async def test_no_succeeded_scan_is_empty_not_error(db_session: AsyncSession) -> None:
    """latest_scan_id points at a running (not succeeded) scan → empty doc."""
    from services.vex_export import export_vex

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    running = await make_scan(db_session, project=project, status="running")
    project.latest_scan_id = running.id
    await db_session.commit()
    await db_session.refresh(project)

    body, _, _ = await export_vex(db_session, project_id=project.id, fmt="openvex")
    assert json.loads(body)["statements"] == []


# ---------------------------------------------------------------------------
# Mapping correctness — 7 states × 2 formats, end to end through the exporter.
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    ("internal", "openvex_status"),
    list(_OPENVEX_STATUS_MAP.items()),
)
async def test_openvex_status_mapping_end_to_end(
    db_session: AsyncSession, internal: str, openvex_status: str
) -> None:
    from services.vex_export import export_vex

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    cve_id = f"CVE-2099-{unique_suffix()}"
    await _seed_one_finding(
        db_session, scan_id=scan.id, status=internal, cve_id=cve_id
    )

    body, _, _ = await export_vex(db_session, project_id=project.id, fmt="openvex")
    parsed = json.loads(body)
    stmt = next(s for s in parsed["statements"] if s["vulnerability"]["name"] == cve_id)
    assert stmt["status"] == openvex_status
    assert stmt["products"][0]["@id"].startswith("pkg:")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("internal", "cdx_state"),
    list(CYCLONEDX_STATE_MAP.items()),
)
async def test_cyclonedx_state_mapping_end_to_end(
    db_session: AsyncSession, internal: str, cdx_state: str
) -> None:
    from services.vex_export import export_vex

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    cve_id = f"CVE-2099-{unique_suffix()}"
    await _seed_one_finding(
        db_session, scan_id=scan.id, status=internal, cve_id=cve_id
    )

    body, _, _ = await export_vex(db_session, project_id=project.id, fmt="cyclonedx")
    parsed = json.loads(body)
    vuln = next(v for v in parsed["vulnerabilities"] if v["id"] == cve_id)
    assert vuln["analysis"]["state"] == cdx_state
    assert vuln["affects"][0]["ref"].startswith("pkg:")
    assert vuln["source"]["name"] == "NVD"


@pytest.mark.integration
async def test_justification_goes_to_openvex_impact_statement(
    db_session: AsyncSession,
) -> None:
    from services.vex_export import export_vex

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    cve_id = f"CVE-2099-{unique_suffix()}"
    await _seed_one_finding(
        db_session,
        scan_id=scan.id,
        status="not_affected",
        cve_id=cve_id,
        justification="vulnerable code is never reached at runtime",
    )

    body, _, _ = await export_vex(db_session, project_id=project.id, fmt="openvex")
    stmt = next(
        s
        for s in json.loads(body)["statements"]
        if s["vulnerability"]["name"] == cve_id
    )
    assert stmt["impact_statement"] == "vulnerable code is never reached at runtime"


@pytest.mark.integration
async def test_justification_goes_to_cyclonedx_analysis_detail(
    db_session: AsyncSession,
) -> None:
    from services.vex_export import export_vex

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    cve_id = f"CVE-2099-{unique_suffix()}"
    await _seed_one_finding(
        db_session,
        scan_id=scan.id,
        status="false_positive",
        cve_id=cve_id,
        justification="scanner mis-identified the package",
    )

    body, _, _ = await export_vex(db_session, project_id=project.id, fmt="cyclonedx")
    vuln = next(
        v for v in json.loads(body)["vulnerabilities"] if v["id"] == cve_id
    )
    assert vuln["analysis"]["detail"] == "scanner mis-identified the package"


# ---------------------------------------------------------------------------
# Deterministic order + multiple findings.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_statements_sorted_by_cve_id(db_session: AsyncSession) -> None:
    from services.vex_export import export_vex

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    suffix = unique_suffix()
    # Seed out of order; exporter must sort by external_id.
    for n in ("0003", "0001", "0002"):
        await _seed_one_finding(
            db_session,
            scan_id=scan.id,
            status="exploitable",
            cve_id=f"CVE-2099-{suffix}-{n}",
        )

    body, _, _ = await export_vex(db_session, project_id=project.id, fmt="openvex")
    names = [s["vulnerability"]["name"] for s in json.loads(body)["statements"]]
    assert names == sorted(names)
    assert len(names) == 3


# ---------------------------------------------------------------------------
# Byte-stability (BUG-006-style): re-exporting is byte-for-byte identical.
# ---------------------------------------------------------------------------


_ALL_FORMATS = ("openvex", "cyclonedx")


@pytest.mark.integration
@pytest.mark.parametrize("fmt", _ALL_FORMATS)
async def test_reexport_same_scan_is_byte_identical(
    db_session: AsyncSession, fmt: str
) -> None:
    from services.vex_export import export_vex

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    suffix = unique_suffix()
    for n, status in (("a", "exploitable"), ("b", "not_affected"), ("c", "fixed")):
        await _seed_one_finding(
            db_session,
            scan_id=scan.id,
            status=status,
            cve_id=f"CVE-2099-{suffix}-{n}",
            justification=f"note {n}",
        )

    body1, _, _ = await export_vex(db_session, project_id=project.id, fmt=fmt)
    body2, _, _ = await export_vex(db_session, project_id=project.id, fmt=fmt)
    assert body1 == body2, f"{fmt} export is not byte-stable"


@pytest.mark.integration
@pytest.mark.parametrize("fmt", _ALL_FORMATS)
async def test_reexport_empty_project_is_byte_identical(
    db_session: AsyncSession, fmt: str
) -> None:
    from services.vex_export import export_vex

    _, project, _ = await _make_project_with_succeeded_scan(db_session)

    body1, _, _ = await export_vex(db_session, project_id=project.id, fmt=fmt)
    body2, _, _ = await export_vex(db_session, project_id=project.id, fmt=fmt)
    assert body1 == body2, f"empty-project {fmt} export is not byte-stable"


@pytest.mark.integration
async def test_doc_id_is_deterministic_uuidv5_from_scan(
    db_session: AsyncSession,
) -> None:
    from services.vex_export import _VEX_UUID_NAMESPACE, export_vex

    _, project, scan = await _make_project_with_succeeded_scan(db_session)

    body, _, _ = await export_vex(db_session, project_id=project.id, fmt="openvex")
    expected = uuid.uuid5(_VEX_UUID_NAMESPACE, str(scan.id))
    assert json.loads(body)["@id"].endswith(str(expected))


@pytest.mark.integration
async def test_timestamp_uses_scan_completion_not_wall_clock(
    db_session: AsyncSession,
) -> None:
    from datetime import UTC, datetime

    from services.vex_export import export_vex

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    scan.completed_at = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
    await db_session.commit()
    await db_session.refresh(scan)

    body, _, _ = await export_vex(db_session, project_id=project.id, fmt="openvex")
    assert json.loads(body)["timestamp"] == "2025-01-02T03:04:05.000Z"
