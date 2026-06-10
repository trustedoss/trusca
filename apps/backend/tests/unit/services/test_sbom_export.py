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

    assert content_type == "application/vnd.cyclonedx+json"
    assert filename == f"sbom-{project.slug}.cdx.json"

    parsed = json.loads(body)
    assert parsed["bomFormat"] == "CycloneDX"
    assert parsed["specVersion"] == "1.6"
    assert parsed["serialNumber"].startswith("urn:uuid:")
    assert parsed["version"] == 1
    assert "timestamp" in parsed["metadata"]
    assert parsed["metadata"]["component"]["type"] == "application"
    assert parsed["metadata"]["component"]["name"] == project.name
    assert parsed["components"] == []
    # H-4: the VEX key is always present so consumers can rely on it.
    assert parsed["vulnerabilities"] == []


async def test_export_cyclonedx_xml_empty_project(db_session: AsyncSession) -> None:
    from services.sbom_export import export_sbom

    _, project, _ = await _make_project_with_succeeded_scan(db_session)
    body, content_type, filename = await export_sbom(
        db_session, project_id=project.id, fmt="cyclonedx-xml"
    )

    assert content_type == "application/vnd.cyclonedx+xml"
    assert filename == f"sbom-{project.slug}.cdx.xml"
    assert body.startswith("<?xml")

    # Ensure the body is parsable XML and the namespace is the CycloneDX 1.6 schema.
    root = ET.fromstring(body)
    assert root.tag.endswith("}bom")
    # No components child entries.
    components_el = root.find("{http://cyclonedx.org/schema/bom/1.6}components")
    assert components_el is not None
    assert list(components_el) == []


async def test_export_spdx_json_empty_project(db_session: AsyncSession) -> None:
    from services.sbom_export import export_sbom

    _, project, _ = await _make_project_with_succeeded_scan(db_session)
    body, content_type, filename = await export_sbom(
        db_session, project_id=project.id, fmt="spdx-json"
    )

    assert content_type == "application/spdx+json"
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

    assert content_type == "text/spdx"
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


async def test_cyclonedx_json_emits_components_in_purl_lexical_order(
    db_session: AsyncSession,
) -> None:
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    react_name = await _seed_three_components(db_session, scan_id=scan.id)

    body, _, _ = await export_sbom(
        db_session, project_id=project.id, fmt="cyclonedx-json"
    )
    parsed = json.loads(body)
    # BUG-006: the guide guarantees purl-lexical order, not name-alphabetical.
    # (react carries a `facebook` namespace, so its purl sorts before lodash.)
    purls = [c["purl"] for c in parsed["components"]]
    assert purls == sorted(purls)
    assert len(purls) == 3
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
    ns = "{http://cyclonedx.org/schema/bom/1.6}"
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


# ---------------------------------------------------------------------------
# Byte-stability (BUG-006): re-exporting the same scan is byte-for-byte equal,
# across all four formats. The user guide promises hash-stable SBOMs.
# ---------------------------------------------------------------------------


_ALL_FORMATS = ("cyclonedx-json", "cyclonedx-xml", "spdx-json", "spdx-tv")


@pytest.mark.parametrize("fmt", _ALL_FORMATS)
async def test_reexport_same_scan_is_byte_identical(
    db_session: AsyncSession, fmt: str
) -> None:
    """Two exports of the same scan produce byte-for-byte identical output."""
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    await _seed_three_components(db_session, scan_id=scan.id)

    body1, _, _ = await export_sbom(db_session, project_id=project.id, fmt=fmt)
    body2, _, _ = await export_sbom(db_session, project_id=project.id, fmt=fmt)

    assert body1 == body2, f"{fmt} export is not byte-stable"


@pytest.mark.parametrize("fmt", _ALL_FORMATS)
async def test_reexport_empty_project_is_byte_identical(
    db_session: AsyncSession, fmt: str
) -> None:
    """Even an empty project (no components) re-exports identical bytes."""
    from services.sbom_export import export_sbom

    _, project, _ = await _make_project_with_succeeded_scan(db_session)

    body1, _, _ = await export_sbom(db_session, project_id=project.id, fmt=fmt)
    body2, _, _ = await export_sbom(db_session, project_id=project.id, fmt=fmt)

    assert body1 == body2, f"empty-project {fmt} export is not byte-stable"


async def test_serial_number_is_deterministic_uuidv5_from_scan(
    db_session: AsyncSession,
) -> None:
    """serialNumber derives from the scan id (UUIDv5), not a fresh uuid4."""
    import uuid as _uuid

    from services.sbom_export import _SBOM_UUID_NAMESPACE, export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    await _seed_three_components(db_session, scan_id=scan.id)

    body, _, _ = await export_sbom(
        db_session, project_id=project.id, fmt="cyclonedx-json"
    )
    parsed = json.loads(body)
    expected = _uuid.uuid5(_SBOM_UUID_NAMESPACE, str(scan.id))
    assert parsed["serialNumber"] == f"urn:uuid:{expected}"


async def test_timestamp_uses_scan_completion_not_wall_clock(
    db_session: AsyncSession,
) -> None:
    """metadata.timestamp reflects the scan's persisted completion time."""
    from datetime import UTC, datetime

    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    # Pin a known completion time and persist it.
    completed = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
    scan.completed_at = completed
    await db_session.commit()
    await db_session.refresh(scan)

    body, _, _ = await export_sbom(
        db_session, project_id=project.id, fmt="cyclonedx-json"
    )
    parsed = json.loads(body)
    # Deterministic, millisecond-precision Z form derived from completed_at.
    assert parsed["metadata"]["timestamp"] == "2025-01-02T03:04:05.000Z"


async def test_spdx_namespace_is_deterministic_across_reexports(
    db_session: AsyncSession,
) -> None:
    """SPDX documentNamespace is stable across re-exports (no uuid4 fallback)."""
    from services.sbom_export import export_sbom

    # Empty project exercises the no-succeeded-scan path that previously used
    # a fresh uuid4 for the namespace.
    _, project, _ = await _make_project_with_succeeded_scan(db_session)

    body1, _, _ = await export_sbom(db_session, project_id=project.id, fmt="spdx-json")
    body2, _, _ = await export_sbom(db_session, project_id=project.id, fmt="spdx-json")
    ns1 = json.loads(body1)["documentNamespace"]
    ns2 = json.loads(body2)["documentNamespace"]
    assert ns1 == ns2
    assert ns1.startswith("https://trustedoss.io/spdx/")


async def test_components_emitted_in_purl_lexical_order(
    db_session: AsyncSession,
) -> None:
    """CycloneDX components are ordered by purl (the guide's stated sort)."""
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    await _seed_three_components(db_session, scan_id=scan.id)

    body, _, _ = await export_sbom(
        db_session, project_id=project.id, fmt="cyclonedx-json"
    )
    purls = [c["purl"] for c in json.loads(body)["components"]]
    assert purls == sorted(purls)


# ---------------------------------------------------------------------------
# License export (declared / concluded / detected → CycloneDX + SPDX)
# ---------------------------------------------------------------------------


async def _get_or_create_license(
    session: AsyncSession,
    *,
    name: str,
    spdx_id: str | None = None,
    category: str = "allowed",
):
    from sqlalchemy import select

    from models import License

    if spdx_id is not None:
        existing = (
            await session.execute(select(License).where(License.spdx_id == spdx_id))
        ).scalar_one_or_none()
        if existing is not None:
            return existing
    lic = License(spdx_id=spdx_id, name=name, category=category)
    session.add(lic)
    await session.commit()
    await session.refresh(lic)
    return lic


async def _attach_license(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    kind: str,
    name: str,
    spdx_id: str | None = None,
    source_path: str | None = None,
) -> None:
    from models import LicenseFinding

    lic = await _get_or_create_license(session, name=name, spdx_id=spdx_id)
    finding = LicenseFinding(
        scan_id=scan_id,
        component_version_id=cv_id,
        license_id=lic.id,
        kind=kind,
        source_path=source_path,
    )
    session.add(finding)
    await session.commit()


async def test_cyclonedx_emits_license_id_for_spdx_license(
    db_session: AsyncSession,
) -> None:
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    _comp, cv = await _make_component_version(
        db_session, name=f"left-pad-{unique_suffix()}", version="1.0.0"
    )
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)
    await _attach_license(
        db_session, scan_id=scan.id, cv_id=cv.id, kind="concluded", name="MIT", spdx_id="MIT"
    )

    body, _, _ = await export_sbom(
        db_session, project_id=project.id, fmt="cyclonedx-json"
    )
    comp = json.loads(body)["components"][0]
    assert comp["licenses"] == [{"license": {"id": "MIT"}}]


async def test_cyclonedx_prefers_concluded_over_declared(
    db_session: AsyncSession,
) -> None:
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    _comp, cv = await _make_component_version(
        db_session, name=f"dual-{unique_suffix()}", version="1.0.0"
    )
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)
    await _attach_license(
        db_session, scan_id=scan.id, cv_id=cv.id, kind="declared",
        name="Apache-2.0", spdx_id="Apache-2.0",
    )
    await _attach_license(
        db_session, scan_id=scan.id, cv_id=cv.id, kind="concluded", name="MIT", spdx_id="MIT"
    )

    body, _, _ = await export_sbom(
        db_session, project_id=project.id, fmt="cyclonedx-json"
    )
    comp = json.loads(body)["components"][0]
    # concluded (MIT) wins over declared (Apache-2.0) in CycloneDX's single array.
    assert comp["licenses"] == [{"license": {"id": "MIT"}}]


async def test_cyclonedx_xml_emits_license(db_session: AsyncSession) -> None:
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    _comp, cv = await _make_component_version(
        db_session, name=f"xmllic-{unique_suffix()}", version="1.0.0"
    )
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)
    await _attach_license(
        db_session, scan_id=scan.id, cv_id=cv.id, kind="concluded", name="MIT", spdx_id="MIT"
    )

    body, _, _ = await export_sbom(
        db_session, project_id=project.id, fmt="cyclonedx-xml"
    )
    root = ET.fromstring(body)
    ns = {"cdx": "http://cyclonedx.org/schema/bom/1.6"}
    ids = [el.text for el in root.findall(".//cdx:components//cdx:license/cdx:id", ns)]
    assert ids == ["MIT"]


async def test_spdx_emits_declared_and_concluded_expressions(
    db_session: AsyncSession,
) -> None:
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    _comp, cv = await _make_component_version(
        db_session, name=f"spdxlic-{unique_suffix()}", version="1.0.0"
    )
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)
    await _attach_license(
        db_session, scan_id=scan.id, cv_id=cv.id, kind="declared", name="MIT", spdx_id="MIT"
    )
    await _attach_license(
        db_session, scan_id=scan.id, cv_id=cv.id, kind="concluded",
        name="Apache-2.0", spdx_id="Apache-2.0",
    )

    body, _, _ = await export_sbom(db_session, project_id=project.id, fmt="spdx-json")
    pkg = json.loads(body)["packages"][0]
    assert pkg["licenseDeclared"] == "MIT"
    assert pkg["licenseConcluded"] == "Apache-2.0"


async def test_spdx_concluded_falls_back_to_declared(
    db_session: AsyncSession,
) -> None:
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    _comp, cv = await _make_component_version(
        db_session, name=f"onlydecl-{unique_suffix()}", version="1.0.0"
    )
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)
    await _attach_license(
        db_session, scan_id=scan.id, cv_id=cv.id, kind="declared", name="MIT", spdx_id="MIT"
    )

    body, _, _ = await export_sbom(db_session, project_id=project.id, fmt="spdx-json")
    pkg = json.loads(body)["packages"][0]
    assert pkg["licenseDeclared"] == "MIT"
    assert pkg["licenseConcluded"] == "MIT"  # falls back to declared


async def test_spdx_multiple_licenses_anded_in_sorted_order(
    db_session: AsyncSession,
) -> None:
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    _comp, cv = await _make_component_version(
        db_session, name=f"multi-{unique_suffix()}", version="1.0.0"
    )
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)
    await _attach_license(
        db_session, scan_id=scan.id, cv_id=cv.id, kind="concluded", name="MIT",
        spdx_id="MIT", source_path="LICENSE",
    )
    await _attach_license(
        db_session, scan_id=scan.id, cv_id=cv.id, kind="concluded", name="Apache-2.0",
        spdx_id="Apache-2.0", source_path="README",
    )

    body, _, _ = await export_sbom(db_session, project_id=project.id, fmt="spdx-json")
    pkg = json.loads(body)["packages"][0]
    assert pkg["licenseConcluded"] == "Apache-2.0 AND MIT"  # sorted


async def test_license_without_spdx_id(db_session: AsyncSession) -> None:
    """A LicenseRef-style license (no SPDX id) → CycloneDX name, SPDX NOASSERTION."""
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    _comp, cv = await _make_component_version(
        db_session, name=f"custom-{unique_suffix()}", version="1.0.0"
    )
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)
    await _attach_license(
        db_session, scan_id=scan.id, cv_id=cv.id, kind="concluded",
        name="LicenseRef-Proprietary", spdx_id=None,
    )

    cdx, _, _ = await export_sbom(db_session, project_id=project.id, fmt="cyclonedx-json")
    comp = json.loads(cdx)["components"][0]
    assert comp["licenses"] == [{"license": {"name": "LicenseRef-Proprietary"}}]

    spdx, _, _ = await export_sbom(db_session, project_id=project.id, fmt="spdx-json")
    pkg = json.loads(spdx)["packages"][0]
    # No SPDX id → expression cannot be built → spec sentinel.
    assert pkg["licenseConcluded"] == "NOASSERTION"


async def test_component_without_license_stays_noassertion(
    db_session: AsyncSession,
) -> None:
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    _comp, cv = await _make_component_version(
        db_session, name=f"nolic-{unique_suffix()}", version="1.0.0"
    )
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)

    cdx, _, _ = await export_sbom(db_session, project_id=project.id, fmt="cyclonedx-json")
    assert "licenses" not in json.loads(cdx)["components"][0]

    spdx, _, _ = await export_sbom(db_session, project_id=project.id, fmt="spdx-json")
    pkg = json.loads(spdx)["packages"][0]
    assert pkg["licenseConcluded"] == "NOASSERTION"
    assert pkg["licenseDeclared"] == "NOASSERTION"


async def test_reexport_with_licenses_is_byte_identical(
    db_session: AsyncSession,
) -> None:
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    _comp, cv = await _make_component_version(
        db_session, name=f"stable-{unique_suffix()}", version="1.0.0"
    )
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)
    await _attach_license(
        db_session, scan_id=scan.id, cv_id=cv.id, kind="concluded", name="MIT",
        spdx_id="MIT", source_path="a",
    )
    await _attach_license(
        db_session, scan_id=scan.id, cv_id=cv.id, kind="concluded", name="Apache-2.0",
        spdx_id="Apache-2.0", source_path="b",
    )

    for fmt in ("cyclonedx-json", "cyclonedx-xml", "spdx-json", "spdx-tv"):
        b1, _, _ = await export_sbom(db_session, project_id=project.id, fmt=fmt)
        b2, _, _ = await export_sbom(db_session, project_id=project.id, fmt=fmt)
        assert b1 == b2, f"{fmt} is not byte-stable with licenses"


# ---------------------------------------------------------------------------
# Top-level component version (scan_metadata.release → metadata.component)
# ---------------------------------------------------------------------------


async def test_top_component_version_uses_release_label(
    db_session: AsyncSession,
) -> None:
    from services.sbom_export import export_sbom

    # make_scan() does not accept scan_metadata; set the release label after
    # creation (Feature #18 stores it under scan_metadata['release']).
    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    scan.scan_metadata = {"release": "v1.2.3"}
    await db_session.commit()
    await db_session.refresh(scan)

    body, _, _ = await export_sbom(
        db_session, project_id=project.id, fmt="cyclonedx-json"
    )
    assert json.loads(body)["metadata"]["component"]["version"] == "v1.2.3"


async def test_top_component_version_falls_back_to_scan_id(
    db_session: AsyncSession,
) -> None:
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)

    body, _, _ = await export_sbom(
        db_session, project_id=project.id, fmt="cyclonedx-json"
    )
    assert json.loads(body)["metadata"]["component"]["version"] == str(scan.id)

# ---------------------------------------------------------------------------
# VEX in CycloneDX (H-4) — vulnerabilities[] with analysis.state mapping
# ---------------------------------------------------------------------------


async def _make_vulnerability(session: AsyncSession, *, cve_id: str, severity: str = "high"):
    from models import Vulnerability

    v = Vulnerability(external_id=cve_id, source="NVD", severity=severity)
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
    status: str = "new",
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


async def test_cyclonedx_json_emits_vulnerabilities_with_vex_state(
    db_session: AsyncSession,
) -> None:
    """H-4: the SBOM alone carries the VEX triage for every finding."""
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session, name=f"vexed-{suffix}", version="1.0.0")
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)

    v1 = await _make_vulnerability(db_session, cve_id=f"CVE-2099-1{suffix[:4]}")
    v2 = await _make_vulnerability(db_session, cve_id=f"CVE-2099-2{suffix[:4]}")
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vulnerability_id=v1.id, status="new"
    )
    await _attach_finding(
        db_session,
        scan_id=scan.id,
        cv_id=cv.id,
        vulnerability_id=v2.id,
        status="not_affected",
        justification="vulnerable symbol is not on the project's call graph",
    )

    body, _, _ = await export_sbom(db_session, project_id=project.id, fmt="cyclonedx-json")
    parsed = json.loads(body)

    vulns = parsed["vulnerabilities"]
    assert [v["id"] for v in vulns] == [v1.external_id, v2.external_id]

    triaged_new, triaged_na = vulns
    # Hardcoded expected states — guards the shared map's values, not just
    # its wiring (the map itself lives in services.vex_export).
    assert triaged_new["analysis"]["state"] == "in_triage"
    assert "detail" not in triaged_new["analysis"]
    assert triaged_na["analysis"]["state"] == "not_affected"
    assert (
        triaged_na["analysis"]["detail"]
        == "vulnerable symbol is not on the project's call graph"
    )
    # Free text must NEVER land on the closed CycloneDX justification enum.
    assert "justification" not in triaged_new["analysis"]
    assert "justification" not in triaged_na["analysis"]
    for v in vulns:
        assert v["source"] == {"name": "NVD"}


async def test_cyclonedx_vulnerability_affects_ref_joins_to_component_bom_ref(
    db_session: AsyncSession,
) -> None:
    """affects[].ref must resolve to a bom-ref present in components[]."""
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session, name=f"ref-{suffix}", version="2.0.0")
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)
    v = await _make_vulnerability(db_session, cve_id=f"CVE-2099-3{suffix[:4]}")
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vulnerability_id=v.id, status="exploitable"
    )

    body, _, _ = await export_sbom(db_session, project_id=project.id, fmt="cyclonedx-json")
    parsed = json.loads(body)

    component_refs = {c["bom-ref"] for c in parsed["components"]}
    (vuln,) = parsed["vulnerabilities"]
    assert vuln["analysis"]["state"] == "exploitable"
    assert vuln["affects"] == [{"ref": str(cv.id)}]
    assert vuln["affects"][0]["ref"] in component_refs


async def test_cyclonedx_xml_emits_vulnerabilities(db_session: AsyncSession) -> None:
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session, name=f"xmlvex-{suffix}", version="3.0.0")
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)
    v = await _make_vulnerability(db_session, cve_id=f"CVE-2099-4{suffix[:4]}")
    await _attach_finding(
        db_session,
        scan_id=scan.id,
        cv_id=cv.id,
        vulnerability_id=v.id,
        status="fixed",
        justification="patched in the 3.0.1 release",
    )

    body, _, _ = await export_sbom(db_session, project_id=project.id, fmt="cyclonedx-xml")
    root = ET.fromstring(body)
    ns = "{http://cyclonedx.org/schema/bom/1.6}"

    vulns_el = root.find(f"{ns}vulnerabilities")
    assert vulns_el is not None
    (vuln_el,) = list(vulns_el)
    assert vuln_el.findtext(f"{ns}id") == v.external_id
    assert vuln_el.findtext(f"{ns}source/{ns}name") == "NVD"
    assert vuln_el.findtext(f"{ns}analysis/{ns}state") == "resolved"
    assert vuln_el.findtext(f"{ns}analysis/{ns}detail") == "patched in the 3.0.1 release"
    assert vuln_el.findtext(f"{ns}affects/{ns}target/{ns}ref") == str(cv.id)


async def test_spdx_exports_carry_no_vulnerabilities(db_session: AsyncSession) -> None:
    """SPDX has no native VEX representation — exports stay component-only."""
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session, name=f"spdxvex-{suffix}", version="1.1.0")
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)
    v = await _make_vulnerability(db_session, cve_id=f"CVE-2099-5{suffix[:4]}")
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vulnerability_id=v.id, status="exploitable"
    )

    body, _, _ = await export_sbom(db_session, project_id=project.id, fmt="spdx-json")
    assert "vulnerabilities" not in json.loads(body)


async def test_reexport_cyclonedx_with_findings_is_byte_identical(
    db_session: AsyncSession,
) -> None:
    """BUG-006 extends to the VEX section: re-exports stay byte-stable."""
    from services.sbom_export import export_sbom

    _, project, scan = await _make_project_with_succeeded_scan(db_session)
    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session, name=f"stable-{suffix}", version="9.9.9")
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)
    v = await _make_vulnerability(db_session, cve_id=f"CVE-2099-6{suffix[:4]}")
    await _attach_finding(
        db_session,
        scan_id=scan.id,
        cv_id=cv.id,
        vulnerability_id=v.id,
        status="analyzing",
        justification="triage in progress with the upstream advisory",
    )

    first, _, _ = await export_sbom(db_session, project_id=project.id, fmt="cyclonedx-json")
    second, _, _ = await export_sbom(db_session, project_id=project.id, fmt="cyclonedx-json")
    assert first == second
    assert json.loads(first)["vulnerabilities"], "fixture must actually emit a finding"
