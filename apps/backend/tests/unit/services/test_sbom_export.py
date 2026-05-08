"""
Unit-style DB-backed tests for ``services/sbom_export.py`` — Step 4.

These tests are marked ``integration`` because the SBOM serializer reads from
PostgreSQL (Project / Scan / ScanComponent / ComponentVersion / Component);
mocking the session would test the mock instead of the SQL aggregation.

Coverage targets:

- Empty project (no scan, or no succeeded scan) returns a well-formed,
  empty SBOM in each of the four formats.
- 4 formats serialize successfully with the expected mandatory fields.
- Components are emitted in name-then-version order (deterministic output).
- Unknown format raises :class:`SBOMUnsupportedFormat` (422).
- Filename uses the project slug + correct extension per format.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import (
    make_organization,
    make_project,
    make_scan,
    make_team,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip sbom export tests")
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
            f"alembic upgrade head failed; sbom export tests cannot run\n"
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
# Fixture builders
# ---------------------------------------------------------------------------


async def _make_component_version(
    session: AsyncSession,
    *,
    name: str,
    version: str,
    namespace: str | None = None,
    description: str | None = None,
    package_type: str = "npm",
):
    from models import Component, ComponentVersion

    purl = f"pkg:{package_type}/{namespace + '/' if namespace else ''}{name}"
    component = Component(
        purl=purl,
        package_type=package_type,
        name=name,
        namespace=namespace,
        description=description,
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


async def _attach(session: AsyncSession, *, scan_id: uuid.UUID, cv_id: uuid.UUID):
    from models import ScanComponent

    sc = ScanComponent(scan_id=scan_id, component_version_id=cv_id, direct=True)
    session.add(sc)
    await session.commit()
    await session.refresh(sc)
    return sc


async def _make_project_with_succeeded_scan(session: AsyncSession):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    project = await make_project(session, team=team)
    scan = await make_scan(session, project=project, status="succeeded")
    project.latest_scan_id = scan.id
    await session.commit()
    await session.refresh(project)
    return team, project, scan


# ---------------------------------------------------------------------------
# Format catalogue
# ---------------------------------------------------------------------------


def test_supported_formats_includes_all_four_targets() -> None:
    from services.sbom_export import SUPPORTED_FORMATS

    assert set(SUPPORTED_FORMATS) == {
        "cyclonedx-json",
        "cyclonedx-xml",
        "spdx-json",
        "spdx-tv",
    }


# ---------------------------------------------------------------------------
# Unknown format → 422
# ---------------------------------------------------------------------------


async def test_unknown_format_raises_unsupported(db_session: AsyncSession) -> None:
    from services.sbom_export import SBOMUnsupportedFormat, export_sbom

    _, project, _ = await _make_project_with_succeeded_scan(db_session)
    with pytest.raises(SBOMUnsupportedFormat):
        await export_sbom(db_session, project_id=project.id, fmt="not-a-format")


async def test_missing_project_raises_unsupported(db_session: AsyncSession) -> None:
    """The router checks IDOR + existence first; this branch is defense-in-depth."""
    from services.sbom_export import SBOMUnsupportedFormat, export_sbom

    with pytest.raises(SBOMUnsupportedFormat):
        await export_sbom(
            db_session,
            project_id=uuid.uuid4(),
            fmt="cyclonedx-json",
        )


# ---------------------------------------------------------------------------
# Empty project — happy path with no components
# ---------------------------------------------------------------------------


async def test_export_cyclonedx_json_empty_project(db_session: AsyncSession) -> None:
    from services.sbom_export import export_sbom

    _, project, _ = await _make_project_with_succeeded_scan(db_session)
    body, content_type, filename = await export_sbom(
        db_session, project_id=project.id, fmt="cyclonedx-json"
    )

    assert content_type == "application/json"
    assert filename == f"sbom-{project.slug}.cdx.json"

    parsed = json.loads(body)
    assert parsed["bomFormat"] == "CycloneDX"
    assert parsed["specVersion"] == "1.5"
    assert parsed["serialNumber"].startswith("urn:uuid:")
    assert parsed["version"] == 1
    assert "timestamp" in parsed["metadata"]
    assert parsed["metadata"]["component"]["type"] == "application"
    assert parsed["metadata"]["component"]["name"] == project.name
    assert parsed["components"] == []


async def test_export_cyclonedx_xml_empty_project(db_session: AsyncSession) -> None:
    from services.sbom_export import export_sbom

    _, project, _ = await _make_project_with_succeeded_scan(db_session)
    body, content_type, filename = await export_sbom(
        db_session, project_id=project.id, fmt="cyclonedx-xml"
    )

    assert content_type == "application/xml"
    assert filename == f"sbom-{project.slug}.cdx.xml"
    assert body.startswith("<?xml")

    # Ensure the body is parsable XML and the namespace is the CycloneDX 1.5 schema.
    root = ET.fromstring(body)
    assert root.tag.endswith("}bom")
    # No components child entries.
    components_el = root.find("{http://cyclonedx.org/schema/bom/1.5}components")
    assert components_el is not None
    assert list(components_el) == []


async def test_export_spdx_json_empty_project(db_session: AsyncSession) -> None:
    from services.sbom_export import export_sbom

    _, project, _ = await _make_project_with_succeeded_scan(db_session)
    body, content_type, filename = await export_sbom(
        db_session, project_id=project.id, fmt="spdx-json"
    )

    assert content_type == "application/json"
    assert filename == f"sbom-{project.slug}.spdx.json"

    parsed = json.loads(body)
    assert parsed["spdxVersion"] == "SPDX-2.3"
    assert parsed["dataLicense"] == "CC0-1.0"
    assert parsed["SPDXID"] == "SPDXRef-DOCUMENT"
    assert parsed["name"].endswith("SBOM")
    assert parsed["documentNamespace"].startswith("https://trustedoss.io/spdx/")
    assert "created" in parsed["creationInfo"]
    assert any(c.startswith("Tool:") for c in parsed["creationInfo"]["creators"])
    assert parsed["packages"] == []


async def test_export_spdx_tv_empty_project(db_session: AsyncSession) -> None:
    from services.sbom_export import export_sbom

    _, project, _ = await _make_project_with_succeeded_scan(db_session)
    body, content_type, filename = await export_sbom(
        db_session, project_id=project.id, fmt="spdx-tv"
    )

    assert content_type == "text/plain"
    assert filename == f"sbom-{project.slug}.spdx"

    # The header tag block is line-oriented; assert each required tag fires.
    lines = body.splitlines()
    assert "SPDXVersion: SPDX-2.3" in lines
    assert "DataLicense: CC0-1.0" in lines
    assert "SPDXID: SPDXRef-DOCUMENT" in lines
    assert any(line.startswith("DocumentName:") for line in lines)
    assert any(line.startswith("DocumentNamespace:") for line in lines)
    assert any(line.startswith("Created:") for line in lines)
    assert any(line.startswith("Creator: Tool:") for line in lines)
    # No PackageName entries for an empty project.
    assert not any(line.startswith("PackageName:") for line in lines)


# ---------------------------------------------------------------------------
# Project with components
# ---------------------------------------------------------------------------


async def _seed_three_components(session: AsyncSession, *, scan_id: uuid.UUID):
    """Seed three components: lodash, react, zod (alphabetical for ordering).

    Returns ``(react_name,)`` — only a derived value the caller actually uses
    in assertions. We avoid returning the ORM rows themselves so the tests
    don't accidentally trigger a lazy attribute load on the async session.
    """
    suffix = unique_suffix()
    lodash_name = f"lodash-{suffix}"
    react_name = f"react-{suffix}"
    zod_name = f"zod-{suffix}"
    _, cv1 = await _make_component_version(
        session,
        name=lodash_name,
        version="4.17.21",
        description="A modern JavaScript utility library",
    )
    _, cv2 = await _make_component_version(
        session,
        name=react_name,
        version="18.2.0",
        namespace="facebook",
    )
    _, cv3 = await _make_component_version(
        session, name=zod_name, version="3.22.4"
    )
    await _attach(session, scan_id=scan_id, cv_id=cv1.id)
    await _attach(session, scan_id=scan_id, cv_id=cv2.id)
    await _attach(session, scan_id=scan_id, cv_id=cv3.id)
    return react_name


async def test_cyclonedx_json_emits_components_in_alphabetical_order(
    db_session: AsyncSession,
) -> None:
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    react_name = await _seed_three_components(db_session, scan_id=scan.id)

    body, _, _ = await export_sbom(
        db_session, project_id=project.id, fmt="cyclonedx-json"
    )
    parsed = json.loads(body)
    names = [c["name"] for c in parsed["components"]]
    # Seeded names follow the pattern lodash- / react- / zod-, so alphabetical.
    assert names == sorted(names)
    assert len(names) == 3
    # Mandatory CycloneDX fields per component.
    for c in parsed["components"]:
        assert c["type"] == "library"
        assert "name" in c
        assert "version" in c
        assert "bom-ref" in c
        # purl is optional in the spec but every seeded row carries one.
        assert c["purl"].startswith("pkg:")

    # The react package has a namespace -> rendered as `group`.
    react_entry = next(c for c in parsed["components"] if c["name"] == react_name)
    assert react_entry["group"] == "facebook"


async def test_cyclonedx_xml_includes_components(db_session: AsyncSession) -> None:
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    await _seed_three_components(db_session, scan_id=scan.id)

    body, _, _ = await export_sbom(
        db_session, project_id=project.id, fmt="cyclonedx-xml"
    )
    root = ET.fromstring(body)
    ns = "{http://cyclonedx.org/schema/bom/1.5}"
    components_el = root.find(f"{ns}components")
    assert components_el is not None
    component_nodes = list(components_el.findall(f"{ns}component"))
    assert len(component_nodes) == 3
    for node in component_nodes:
        assert node.attrib["type"] == "library"
        assert node.find(f"{ns}name") is not None
        assert node.find(f"{ns}version") is not None


async def test_spdx_json_emits_packages(db_session: AsyncSession) -> None:
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    await _seed_three_components(db_session, scan_id=scan.id)

    body, _, _ = await export_sbom(
        db_session, project_id=project.id, fmt="spdx-json"
    )
    parsed = json.loads(body)
    assert len(parsed["packages"]) == 3
    for pkg in parsed["packages"]:
        # Mandatory SPDX 2.3 fields.
        assert pkg["SPDXID"].startswith("SPDXRef-Pkg-")
        assert pkg["name"]
        assert pkg["versionInfo"]
        assert pkg["downloadLocation"] == "NOASSERTION"
        assert pkg["filesAnalyzed"] is False
        assert pkg["licenseConcluded"] == "NOASSERTION"
        # purl shows up under externalRefs for every seeded component.
        assert any(
            ref["referenceType"] == "purl" for ref in pkg.get("externalRefs", [])
        )


async def test_spdx_tv_renders_one_block_per_package(db_session: AsyncSession) -> None:
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    await _seed_three_components(db_session, scan_id=scan.id)

    body, _, _ = await export_sbom(
        db_session, project_id=project.id, fmt="spdx-tv"
    )
    package_lines = [line for line in body.splitlines() if line.startswith("PackageName:")]
    assert len(package_lines) == 3
    spdxid_lines = [line for line in body.splitlines() if line.startswith("SPDXID: SPDXRef-Pkg-")]
    assert len(spdxid_lines) == 3
    # ExternalRef purl block fires for each component.
    purl_lines = [
        line
        for line in body.splitlines()
        if line.startswith("ExternalRef: PACKAGE-MANAGER purl ")
    ]
    assert len(purl_lines) == 3


# ---------------------------------------------------------------------------
# Latest succeeded scan selection
# ---------------------------------------------------------------------------


async def test_export_uses_latest_succeeded_scan_not_running(
    db_session: AsyncSession,
) -> None:
    """An older succeeded scan + a newer running scan → export uses the older one."""
    from services.sbom_export import export_sbom

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    older_succeeded = await make_scan(db_session, project=project, status="succeeded")
    await _seed_three_components(db_session, scan_id=older_succeeded.id)
    # Newer in-flight scan; should not be picked.
    await make_scan(db_session, project=project, status="running")

    body, _, _ = await export_sbom(
        db_session, project_id=project.id, fmt="cyclonedx-json"
    )
    parsed = json.loads(body)
    # The succeeded scan's three components made it into the export.
    assert len(parsed["components"]) == 3
