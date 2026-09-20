"""Exports state their own completeness and carry the dependency graph (U3-C).

Built on the recorded ``trivy image --list-all-pkgs`` capture so the graph has
real density (275 components, 141 edges), stored through the container
persister and read back through ``export_sbom``, the path a download takes.
"""

from __future__ import annotations

import asyncio
import json
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from models import Scan
from services import sbom_completeness as sc
from services import scan_outcome as so
from tests._db_required import migrate_to_head
from tests.integration.scan.test_container_inventory import _persist, _report
from tests.integration.scan.test_container_multi_cve import _seed_queued_container_scan

pytestmark = pytest.mark.integration

NS = {"c": "http://cyclonedx.org/schema/bom/1.6"}


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


def _seed_scan(session: Session, metadata: dict[str, Any] | None) -> uuid.UUID:
    scan_id = _seed_queued_container_scan()
    _persist(session, scan_id, _report())
    scan = session.get(Scan, scan_id)
    assert scan is not None
    scan.status = "succeeded"
    scan.completed_at = datetime.now(UTC)
    scan.scan_metadata = metadata or {}
    session.commit()
    return scan_id


def _export(session: Session, scan_id: uuid.UUID, fmt: str) -> str:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url
    from services.sbom_export import export_sbom

    scan = session.get(Scan, scan_id)
    assert scan is not None
    project_id = scan.project_id

    async def _run() -> str:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        try:
            async with async_sessionmaker(engine, class_=AsyncSession)() as s:
                body, _ctype, _name = await export_sbom(
                    s, project_id=project_id, fmt=fmt, scan_id=scan_id
                )
                return body
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _recorded(**extra: Any) -> dict[str, Any]:
    return {so.STAGE_TIMINGS_KEY: {"fetch": {"started_at": "x", "ended_at": "y"}}, **extra}


def _aggregates(doc: dict[str, Any]) -> dict[str, str]:
    """``{"assemblies": verdict, "dependencies": verdict}`` from the document."""
    out: dict[str, str] = {}
    for composition in doc["compositions"]:
        for kind in ("assemblies", "dependencies"):
            if kind in composition:
                out[kind] = composition["aggregate"]
    return out


def test_a_recorded_clean_scan_exports_its_graph_and_says_complete(
    sync_session: Session,
) -> None:
    scan_id = _seed_scan(sync_session, _recorded())
    doc = json.loads(_export(sync_session, scan_id, "cyclonedx-json"))

    assert _aggregates(doc) == {"assemblies": sc.COMPLETE, "dependencies": sc.COMPLETE}
    refs = {c["bom-ref"] for c in doc["components"]}
    project_ref = doc["metadata"]["component"]["bom-ref"]
    by_ref = {d["ref"]: d["dependsOn"] for d in doc["dependencies"]}
    # One entry per component plus the project, and no reference leaves the document.
    assert set(by_ref) == refs | {project_ref}
    assert all(set(children) <= refs for children in by_ref.values())
    assert sum(len(children) for ref, children in by_ref.items() if ref != project_ref) > 50
    # The compositions name exactly the components that are listed.
    assemblies = next(c for c in doc["compositions"] if "assemblies" in c)["assemblies"]
    assert set(assemblies) == refs
    basis = {p["name"]: p["value"] for p in doc["metadata"]["properties"]}
    assert basis[sc.BASIS_PROPERTY] == "no_known_gap"


def test_a_degraded_build_prep_exports_incomplete_with_the_reason(
    sync_session: Session,
) -> None:
    meta = so.add_stage_outcome(_recorded(), stage="prep", reason="timeout")
    scan_id = _seed_scan(sync_session, meta)
    doc = json.loads(_export(sync_session, scan_id, "cyclonedx-json"))

    assert _aggregates(doc) == {"assemblies": sc.INCOMPLETE, "dependencies": sc.INCOMPLETE}
    basis = {p["name"]: p["value"] for p in doc["metadata"]["properties"]}
    assert basis[sc.BASIS_PROPERTY] == "stage_degraded:prep"


def test_a_scan_without_the_stage_record_exports_unknown(sync_session: Session) -> None:
    """A scan from before U3-B cannot vouch for itself."""
    scan_id = _seed_scan(sync_session, {"detected_env": "node"})
    doc = json.loads(_export(sync_session, scan_id, "cyclonedx-json"))
    assert _aggregates(doc) == {"assemblies": sc.UNKNOWN, "dependencies": sc.UNKNOWN}


def test_the_xml_export_carries_the_same_statements(sync_session: Session) -> None:
    scan_id = _seed_scan(sync_session, _recorded())
    as_json = json.loads(_export(sync_session, scan_id, "cyclonedx-json"))
    root = ET.fromstring(_export(sync_session, scan_id, "cyclonedx-xml").split("\n", 1)[1])

    children = [el.tag.split("}")[1] for el in root]
    # CycloneDX schema order: components, dependencies, compositions, vulnerabilities.
    assert children.index("components") < children.index("dependencies")
    assert children.index("dependencies") < children.index("compositions")
    assert children.index("compositions") < children.index("vulnerabilities")

    xml_deps = {
        str(d.get("ref")): sorted(str(x.get("ref")) for x in d.findall("c:dependency", NS))
        for d in root.findall("c:dependencies/c:dependency", NS)
    }
    assert xml_deps == {d["ref"]: sorted(d["dependsOn"]) for d in as_json["dependencies"]}
    aggregates = [a.text for a in root.findall("c:compositions/c:composition/c:aggregate", NS)]
    assert aggregates == [c["aggregate"] for c in as_json["compositions"]]


def test_the_export_is_byte_identical_on_repeat(sync_session: Session) -> None:
    scan_id = _seed_scan(sync_session, _recorded())
    assert _export(sync_session, scan_id, "cyclonedx-json") == _export(
        sync_session, scan_id, "cyclonedx-json"
    )


def test_spdx_is_unchanged_by_the_graph(sync_session: Session) -> None:
    scan_id = _seed_scan(sync_session, _recorded())
    doc = json.loads(_export(sync_session, scan_id, "spdx-json"))
    assert "compositions" not in doc and "dependencies" not in doc
