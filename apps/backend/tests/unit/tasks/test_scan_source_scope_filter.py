"""Unit tests — runtime-scope filter pipeline integration (Phase K).

Three layers are pinned here, each against REAL captured tool output (the
persist-boundary rule — hand-rolled minimal JSON hides the defect habitat):

  * **Fixture-scale filter behaviour** — ``real_cyclonedx_maven_scoped.json``
    (cdxgen 12.3.3 over a Spring Boot 3.2 Maven project: 108 components,
    required/optional/excluded/unscoped all present) and
    ``real_cyclonedx_node_dev.json`` + ``npm/package-lock.devdeps.json``
    (cdxgen + npm lockfile for an Express app with 5 devDependency roots).
  * **Persist boundary** — the filtered document flows through
    ``persist_sbom_components`` and the row set matches the kept set.
  * **Wiring** — ``_apply_scope_filter``'s copy-then-commit contract (disk and
    memory can never diverge) and the pipeline ordering invariant (filter
    BEFORE artifact persist and signing — a refactor that moves signing
    earlier would silently sign the unfiltered document).
"""

from __future__ import annotations

import ast
import copy
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest

from integrations.cdxgen import CdxgenResult
from integrations.npm_lockfile import read_lockfile
from integrations.sbom_scope_filter import filter_sbom_to_runtime_scope

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
MAVEN_SBOM = FIXTURES / "sbom" / "real_cyclonedx_maven_scoped.json"
NODE_SBOM = FIXTURES / "sbom" / "real_cyclonedx_node_dev.json"
NODE_LOCK = FIXTURES / "npm" / "package-lock.devdeps.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _refs(components: list[dict[str, Any]]) -> set[str]:
    return {
        c.get("bom-ref") or c.get("purl")
        for c in components
        if isinstance(c, dict) and (c.get("bom-ref") or c.get("purl"))
    }


# ---------------------------------------------------------------------------
# Real maven fixture
# ---------------------------------------------------------------------------


def test_maven_fixture_drops_exactly_the_non_deployable_set() -> None:
    sbom = _load(MAVEN_SBOM)
    components = sbom["components"]
    expected_dropped = sum(
        1
        for c in components
        if isinstance(c.get("purl"), str)
        and c["purl"].startswith("pkg:maven/")
        and c.get("scope") in {"optional", "excluded"}
    )
    assert expected_dropped > 0  # fixture must exercise the drop path
    total_before = len(components)

    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)

    assert result.applied is True
    assert result.dropped == {"maven": expected_dropped}
    assert len(sbom["components"]) == total_before - expected_dropped
    # Survivors carry no droppable scope.
    assert not any(
        c.get("scope") in {"optional", "excluded"}
        for c in sbom["components"]
        if isinstance(c.get("purl"), str) and c["purl"].startswith("pkg:maven/")
    )


def test_maven_fixture_graph_has_no_dangling_refs_after_filter() -> None:
    sbom = _load(MAVEN_SBOM)
    filter_sbom_to_runtime_scope(sbom, npm_lock=None)

    kept = _refs(sbom["components"])
    metadata_component = sbom.get("metadata", {}).get("component", {})
    root_ref = metadata_component.get("bom-ref") or metadata_component.get("purl")
    if root_ref:
        kept.add(root_ref)

    for entry in sbom.get("dependencies", []):
        assert entry["ref"] in kept, f"dangling graph entry: {entry['ref']}"
        for child in entry.get("dependsOn", []):
            assert child in kept, f"dangling dependsOn: {child}"


# ---------------------------------------------------------------------------
# Real node fixture + real lockfile
# ---------------------------------------------------------------------------


@pytest.fixture
def node_source_dir(tmp_path: Path) -> Path:
    shutil.copy(NODE_LOCK, tmp_path / "package-lock.json")
    return tmp_path


def test_node_fixture_drops_lockfile_dev_set(node_source_dir: Path) -> None:
    sbom = _load(NODE_SBOM)
    npm_lock = read_lockfile(node_source_dir)
    assert npm_lock is not None

    expected_dropped = sum(
        1
        for c in sbom["components"]
        if isinstance(c.get("purl"), str)
        and c["purl"].startswith("pkg:npm/")
        and npm_lock.scope_for_purl(c["purl"]) == "dev"
    )
    assert expected_dropped > 0
    total_before = len(sbom["components"])

    result = filter_sbom_to_runtime_scope(sbom, npm_lock=npm_lock)

    assert result.applied is True
    assert result.dropped == {"npm": expected_dropped}
    assert len(sbom["components"]) == total_before - expected_dropped

    names = {c.get("name") for c in sbom["components"]}
    # devDependency roots gone, production roots kept.
    assert "jest" not in names
    assert "eslint" not in names
    assert "nodemon" not in names
    assert "express" in names
    assert "lodash" in names


# ---------------------------------------------------------------------------
# Persist boundary — filtered document through persist_sbom_components
# ---------------------------------------------------------------------------


class _FakeComponent:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class _FakeComponentVersion:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)


@pytest.fixture
def patched_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tasks.scan_source._get_or_create_component",
        lambda session, *, purl, name, package_type: _FakeComponent(),
    )
    monkeypatch.setattr(
        "tasks.scan_source._get_or_create_component_version",
        lambda session, *, component, version, purl_with_version: _FakeComponentVersion(),
    )
    monkeypatch.setattr(
        "tasks.scan_source._persist_component_licenses",
        lambda session, *, scan_uuid, component_version_id, cdxgen_component, purl: None,
    )

    def _no_graph(session: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr("tasks.scan_source._persist_dependency_graph", _no_graph)


def test_persist_after_filter_persists_exactly_the_kept_set(
    patched_helpers: None,
) -> None:
    from models import ScanComponent
    from tasks.scan_source import persist_sbom_components

    sbom = _load(MAVEN_SBOM)
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert result.dropped  # sanity: filter did something

    session = _FakeSession()
    persist_sbom_components(session, scan_uuid=uuid.uuid4(), sbom=sbom)

    rows = [r for r in session.added if isinstance(r, ScanComponent)]
    persistable = [
        c for c in sbom["components"] if isinstance(c.get("purl"), str) and c["purl"]
    ]
    assert len(rows) == len(persistable)
    # No persisted row may carry a droppable maven scope — the filter ran first.
    assert not any(r.dependency_scope in {"optional", "excluded"} for r in rows)


def test_persist_alone_does_not_filter(patched_helpers: None) -> None:
    """persist_sbom_components is filter-free by design — the ingest path
    (uploaded SBOMs, the supplier's declared truth) relies on this."""
    from models import ScanComponent
    from tasks.scan_source import persist_sbom_components

    sbom = _load(MAVEN_SBOM)  # UNfiltered — optional/excluded still present
    total = sum(
        1 for c in sbom["components"] if isinstance(c.get("purl"), str) and c["purl"]
    )
    session = _FakeSession()
    persist_sbom_components(session, scan_uuid=uuid.uuid4(), sbom=sbom)
    rows = [r for r in session.added if isinstance(r, ScanComponent)]
    assert len(rows) == total
    assert any(r.dependency_scope == "optional" for r in rows)


# ---------------------------------------------------------------------------
# _apply_scope_filter wiring — copy-then-commit contract
# ---------------------------------------------------------------------------


def _make_cdxgen_result(tmp_path: Path, sbom: dict[str, Any]) -> CdxgenResult:
    sbom_path = tmp_path / "bom.json"
    sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
    return CdxgenResult(sbom_path=sbom_path, sbom=copy.deepcopy(sbom))


def test_apply_scope_filter_rewrites_disk_and_memory_consistently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks import scan_source

    recorded: list[Any] = []
    monkeypatch.setattr(
        scan_source, "_record_scope_filter", lambda scan_uuid, result: recorded.append(result)
    )
    original = _load(MAVEN_SBOM)
    cdxgen_result = _make_cdxgen_result(tmp_path, original)

    scan_source._apply_scope_filter(
        scan_uuid=uuid.uuid4(), cdxgen_result=cdxgen_result, source_dir=tmp_path
    )

    on_disk = _load(cdxgen_result.sbom_path)
    assert on_disk == cdxgen_result.sbom  # memory and disk identical
    assert len(on_disk["components"]) < len(original["components"])
    assert recorded and recorded[0].dropped


def test_apply_scope_filter_master_switch_off_is_a_full_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks import scan_source

    monkeypatch.setattr(scan_source, "scan_scope_filter_enabled", lambda: False)
    original = _load(MAVEN_SBOM)
    cdxgen_result = _make_cdxgen_result(tmp_path, original)

    scan_source._apply_scope_filter(
        scan_uuid=uuid.uuid4(), cdxgen_result=cdxgen_result, source_dir=tmp_path
    )

    assert _load(cdxgen_result.sbom_path) == original
    assert cdxgen_result.sbom == original


def test_apply_scope_filter_rewrite_failure_keeps_memory_and_disk_unfiltered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The divergence guard: if the disk rewrite fails, the in-memory document
    must NOT be swapped — persist and Trivy keep seeing the same (unfiltered)
    SBOM, a degraded-but-consistent outcome."""
    from tasks import scan_source

    monkeypatch.setattr(
        scan_source.sbom_scope_filter, "rewrite_sbom_file", lambda path, sbom: False
    )
    recorded: list[Any] = []
    monkeypatch.setattr(
        scan_source, "_record_scope_filter", lambda scan_uuid, result: recorded.append(result)
    )
    original = _load(MAVEN_SBOM)
    cdxgen_result = _make_cdxgen_result(tmp_path, original)

    scan_source._apply_scope_filter(
        scan_uuid=uuid.uuid4(), cdxgen_result=cdxgen_result, source_dir=tmp_path
    )

    assert cdxgen_result.sbom == original  # memory untouched
    assert _load(cdxgen_result.sbom_path) == original  # disk untouched
    assert recorded == []  # no telemetry claiming a filter that didn't land


# ---------------------------------------------------------------------------
# Lifecycle-sequence pins (CLAUDE.md hardening rule 5)
# ---------------------------------------------------------------------------


def _call_order(func_name: str, module_path: Path) -> list[str]:
    """First-call order of named functions inside ``func_name`` (AST walk)."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            order: list[str] = []
            for call in ast.walk(node):
                if isinstance(call, ast.Call):
                    callee = call.func
                    name = (
                        callee.id
                        if isinstance(callee, ast.Name)
                        else callee.attr
                        if isinstance(callee, ast.Attribute)
                        else None
                    )
                    if name and name not in order:
                        order.append(name)
            return order
    raise AssertionError(f"{func_name} not found in {module_path}")


def test_pipeline_filters_before_artifact_persist_and_signing() -> None:
    """A refactor that moves ``_persist_artifact`` or ``_sign_sbom`` ahead of
    the scope filter would persist/sign the UNfiltered document while Trivy
    and the component rows see the filtered one. Pin the order."""
    import tasks.scan_source as scan_source_module

    order = _call_order("_run_pipeline", Path(scan_source_module.__file__))
    assert order.index("_apply_scope_filter") < order.index("_persist_artifact")
    assert order.index("_apply_scope_filter") < order.index("_sign_sbom")


def test_ingest_pipeline_never_references_the_scope_filter() -> None:
    """The ingest path must NOT filter (uploaded SBOMs are declared truth) —
    see the deliberate-divergence comment in tasks/ingest_sbom.py."""
    import tasks.ingest_sbom as ingest_module

    source = Path(ingest_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "_apply_scope_filter" not in names
    assert "filter_sbom_to_runtime_scope" not in names
