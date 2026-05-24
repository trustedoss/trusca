"""Unit tests — dependency-graph ingest wiring in ``tasks.scan_source`` (v2.2 2.2-a2).

Covers ``_persist_dependency_graph``: given the cdxgen SBOM + the
``ref → component_version_id`` / ``ref → ScanComponent`` maps the component loop
builds, it must

  * stamp ``ScanComponent.depth`` (shortest-path) and ``direct`` (depth == 1) on
    the components it created,
  * insert one ``ComponentDependencyEdge`` per RESOLVED parent/child edge (both
    endpoints persisted), de-duplicated,
  * NEVER stamp / insert for dangling refs or the project's own metadata
    component,
  * degrade silently (no depth, no edges, no raise) when the SBOM carries no
    usable graph,
  * survive adversarial graphs (cycle / self-ref / dangling) without hanging or
    raising — the depth-clamp + cycle-safety live in the parser, this asserts the
    ingest layer passes them through correctly.

Fake-session unit tests (no DB), matching the sync-task test pattern.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from models import ComponentDependencyEdge
from tasks.scan_source import _persist_dependency_graph


class _FakeScanComponent:
    """Stand-in for a ScanComponent row that records depth/direct stamps."""

    def __init__(self) -> None:
        self.depth: int | None = None
        self.direct: bool = False
        self.component_version_id = uuid.uuid4()


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)


def _edges(session: _FakeSession) -> list[ComponentDependencyEdge]:
    return [r for r in session.added if isinstance(r, ComponentDependencyEdge)]


def _build_maps(
    refs: list[str],
) -> tuple[dict[str, uuid.UUID], dict[str, _FakeScanComponent]]:
    """One ScanComponent per ref, with a stable cv id wired into both maps."""
    ref_to_sc: dict[str, _FakeScanComponent] = {}
    ref_to_cv: dict[str, uuid.UUID] = {}
    for ref in refs:
        sc = _FakeScanComponent()
        ref_to_sc[ref] = sc
        ref_to_cv[ref] = sc.component_version_id
    return ref_to_cv, ref_to_sc


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_stamps_depth_and_direct_and_persists_edges() -> None:
    sbom = {
        "metadata": {"component": {"bom-ref": "app"}},
        "dependencies": [
            {"ref": "app", "dependsOn": ["a", "b"]},
            {"ref": "a", "dependsOn": ["c"]},
            {"ref": "b", "dependsOn": []},
            {"ref": "c", "dependsOn": []},
        ],
    }
    # The project's metadata component ("app") is NOT a persisted ScanComponent.
    ref_to_cv, ref_to_sc = _build_maps(["a", "b", "c"])
    session = _FakeSession()

    _persist_dependency_graph(
        session,
        scan_uuid=uuid.uuid4(),
        sbom=sbom,
        ref_to_cv_id=ref_to_cv,
        ref_to_scan_component=ref_to_sc,
    )

    # Depth: a,b are direct (1), c transitive (2).
    assert ref_to_sc["a"].depth == 1 and ref_to_sc["a"].direct is True
    assert ref_to_sc["b"].depth == 1 and ref_to_sc["b"].direct is True
    assert ref_to_sc["c"].depth == 2 and ref_to_sc["c"].direct is False

    # Edges: app→a, app→b are dropped (app is not a persisted component); a→c is
    # kept (both resolve). app's edges have no parent cv → skipped.
    edges = _edges(session)
    pairs = {
        (e.parent_component_version_id, e.child_component_version_id) for e in edges
    }
    assert pairs == {(ref_to_cv["a"], ref_to_cv["c"])}


def test_implicit_root_when_no_metadata_component() -> None:
    # No metadata.component → in-degree-0 root "a" is depth 1 (direct).
    sbom = {
        "dependencies": [
            {"ref": "a", "dependsOn": ["b"]},
            {"ref": "b", "dependsOn": []},
        ],
    }
    ref_to_cv, ref_to_sc = _build_maps(["a", "b"])
    session = _FakeSession()
    _persist_dependency_graph(
        session,
        scan_uuid=uuid.uuid4(),
        sbom=sbom,
        ref_to_cv_id=ref_to_cv,
        ref_to_scan_component=ref_to_sc,
    )
    assert ref_to_sc["a"].depth == 1 and ref_to_sc["a"].direct is True
    assert ref_to_sc["b"].depth == 2 and ref_to_sc["b"].direct is False
    edges = _edges(session)
    assert {(e.parent_component_version_id, e.child_component_version_id) for e in edges} == {
        (ref_to_cv["a"], ref_to_cv["b"])
    }


def test_edges_carry_scan_id() -> None:
    sbom = {
        "dependencies": [
            {"ref": "a", "dependsOn": ["b"]},
            {"ref": "b", "dependsOn": []},
        ],
    }
    ref_to_cv, ref_to_sc = _build_maps(["a", "b"])
    session = _FakeSession()
    sid = uuid.uuid4()
    _persist_dependency_graph(
        session,
        scan_uuid=sid,
        sbom=sbom,
        ref_to_cv_id=ref_to_cv,
        ref_to_scan_component=ref_to_sc,
    )
    assert all(e.scan_id == sid for e in _edges(session))


# ---------------------------------------------------------------------------
# No-graph / degradation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sbom",
    [
        {},
        {"dependencies": None},
        {"dependencies": []},
        {"dependencies": "nope"},
        {"components": [{"purl": "pkg:a@1"}]},  # flat list, no graph
    ],
)
def test_no_graph_stamps_nothing_and_adds_no_edges(sbom: dict) -> None:
    ref_to_cv, ref_to_sc = _build_maps(["a"])
    session = _FakeSession()
    _persist_dependency_graph(
        session,
        scan_uuid=uuid.uuid4(),
        sbom=sbom,
        ref_to_cv_id=ref_to_cv,
        ref_to_scan_component=ref_to_sc,
    )
    assert ref_to_sc["a"].depth is None
    assert ref_to_sc["a"].direct is False
    assert _edges(session) == []


# ---------------------------------------------------------------------------
# Adversarial — dangling refs, cycles, self-refs must not invent edges/raise
# ---------------------------------------------------------------------------


def test_dangling_child_ref_makes_no_edge() -> None:
    # "a" depends on "ghost" which is NOT a persisted component → no edge, no
    # invented node; "a" still gets its depth.
    sbom = {
        "dependencies": [
            {"ref": "a", "dependsOn": ["ghost"]},
        ],
    }
    ref_to_cv, ref_to_sc = _build_maps(["a"])
    session = _FakeSession()
    _persist_dependency_graph(
        session,
        scan_uuid=uuid.uuid4(),
        sbom=sbom,
        ref_to_cv_id=ref_to_cv,
        ref_to_scan_component=ref_to_sc,
    )
    assert ref_to_sc["a"].depth == 1
    assert _edges(session) == []


def test_cycle_terminates_and_persists_resolved_edges() -> None:
    # a→b→a cycle; both persisted. Both edges resolve; depth bounded; no hang.
    sbom = {
        "dependencies": [
            {"ref": "a", "dependsOn": ["b"]},
            {"ref": "b", "dependsOn": ["a"]},
        ],
    }
    ref_to_cv, ref_to_sc = _build_maps(["a", "b"])
    session = _FakeSession()
    _persist_dependency_graph(
        session,
        scan_uuid=uuid.uuid4(),
        sbom=sbom,
        ref_to_cv_id=ref_to_cv,
        ref_to_scan_component=ref_to_sc,
    )
    # Both nodes got a bounded depth (cycle-safe).
    assert ref_to_sc["a"].depth is not None
    assert ref_to_sc["b"].depth is not None
    # Both directed edges resolve (a→b and b→a are distinct).
    pairs = {
        (e.parent_component_version_id, e.child_component_version_id)
        for e in _edges(session)
    }
    assert pairs == {
        (ref_to_cv["a"], ref_to_cv["b"]),
        (ref_to_cv["b"], ref_to_cv["a"]),
    }


def test_duplicate_edges_collapse_to_one_row() -> None:
    sbom = {
        "dependencies": [
            {"ref": "a", "dependsOn": ["b", "b", "b"]},
            {"ref": "b", "dependsOn": []},
        ],
    }
    ref_to_cv, ref_to_sc = _build_maps(["a", "b"])
    session = _FakeSession()
    _persist_dependency_graph(
        session,
        scan_uuid=uuid.uuid4(),
        sbom=sbom,
        ref_to_cv_id=ref_to_cv,
        ref_to_scan_component=ref_to_sc,
    )
    assert len(_edges(session)) == 1


def test_two_parent_refs_to_same_cv_collapse_duplicate_edge() -> None:
    # Two DISTINCT parent refs ("p1", "p2") resolve to the SAME parent cv, each
    # depending on the same child cv. After resolution both produce the identical
    # (parent_cv, child_cv) edge — the in-memory dedup must collapse them to one
    # row (defends the DB UNIQUE constraint before it fires).
    parent_sc = _FakeScanComponent()
    child_sc = _FakeScanComponent()
    ref_to_cv = {
        "p1": parent_sc.component_version_id,
        "p2": parent_sc.component_version_id,  # alias → same parent cv
        "child": child_sc.component_version_id,
    }
    ref_to_sc = {"p1": parent_sc, "p2": parent_sc, "child": child_sc}
    sbom = {
        "dependencies": [
            {"ref": "p1", "dependsOn": ["child"]},
            {"ref": "p2", "dependsOn": ["child"]},
            {"ref": "child", "dependsOn": []},
        ],
    }
    session = _FakeSession()
    _persist_dependency_graph(
        session,
        scan_uuid=uuid.uuid4(),
        sbom=sbom,
        ref_to_cv_id=ref_to_cv,
        ref_to_scan_component=ref_to_sc,
    )
    assert len(_edges(session)) == 1  # duplicate (parent_cv, child_cv) collapsed


def test_two_refs_mapping_to_same_cv_skip_self_edge() -> None:
    # Pathological: parent and child refs resolve to the SAME cv id (e.g. a
    # bom-ref alias). The post-resolution self-edge must be dropped.
    sc = _FakeScanComponent()
    ref_to_cv = {"alias1": sc.component_version_id, "alias2": sc.component_version_id}
    ref_to_sc = {"alias1": sc, "alias2": sc}
    sbom = {"dependencies": [{"ref": "alias1", "dependsOn": ["alias2"]}]}
    session = _FakeSession()
    _persist_dependency_graph(
        session,
        scan_uuid=uuid.uuid4(),
        sbom=sbom,
        ref_to_cv_id=ref_to_cv,
        ref_to_scan_component=ref_to_sc,
    )
    assert _edges(session) == []  # resolved self-edge dropped
