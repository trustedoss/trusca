"""Unit tests — cdxgen dependency graph parsing + depth BFS (v2.2 2.2-a2).

The dependency graph is UNTRUSTED input (generated from attacker-controllable
source / lockfiles). These tests pin both the happy-path semantics and the
adversarial-shape safety guarantees of :mod:`integrations.dependency_graph`:

  * ``parse_dependency_graph`` — normalize a CycloneDX ``dependencies`` array
    into an adjacency map, dropping malformed entries, self-edges, and dup edges.
  * ``compute_depths`` — multi-source cycle-safe BFS shortest-path depth.
  * ``graph_depths_from_sbom`` — convenience over a whole SBOM (uses the
    metadata.component as the forced root).

Adversarial cases are parametrized: cycles, self-refs, dangling refs, deep
nesting (thousands), duplicate edges, and giant fan-out. None may hang, OOM,
overflow the SmallInteger depth column, or raise.

Pure functions — no DB, no fixtures.
"""

from __future__ import annotations

import pytest

from integrations.dependency_graph import (
    MAX_DEPTH,
    compute_depths,
    graph_depths_from_sbom,
    parse_dependency_graph,
)

# ---------------------------------------------------------------------------
# parse_dependency_graph — happy path + malformed-input degradation
# ---------------------------------------------------------------------------


def test_parse_basic_graph() -> None:
    deps = [
        {"ref": "A", "dependsOn": ["B", "C"]},
        {"ref": "B", "dependsOn": ["C"]},
        {"ref": "C", "dependsOn": []},
    ]
    adj = parse_dependency_graph(deps)
    assert adj == {"A": ["B", "C"], "B": ["C"], "C": []}


def test_parse_declares_leaf_with_no_dependsOn() -> None:
    # A ref with only an (absent) dependsOn must still appear as a key so callers
    # can distinguish "declared leaf" from "absent".
    adj = parse_dependency_graph([{"ref": "A"}])
    assert adj == {"A": []}


def test_parse_drops_self_edge() -> None:
    adj = parse_dependency_graph([{"ref": "A", "dependsOn": ["A", "B"]}])
    assert adj == {"A": ["B"]}


def test_parse_dedups_duplicate_children() -> None:
    adj = parse_dependency_graph([{"ref": "A", "dependsOn": ["B", "B", "C", "B"]}])
    assert adj == {"A": ["B", "C"]}


def test_parse_dedups_across_repeated_ref_entries() -> None:
    # Same ref appearing twice — second batch's new children append, dups drop.
    adj = parse_dependency_graph(
        [
            {"ref": "A", "dependsOn": ["B"]},
            {"ref": "A", "dependsOn": ["B", "C"]},
        ]
    )
    assert adj == {"A": ["B", "C"]}


@pytest.mark.parametrize(
    "deps",
    [
        None,
        "not-a-list",
        123,
        {"ref": "A"},  # dict, not list
        [],
    ],
)
def test_parse_non_list_or_empty_returns_empty(deps: object) -> None:
    assert parse_dependency_graph(deps) == {}


@pytest.mark.parametrize(
    "entry",
    [
        None,
        "string",
        123,
        {},  # no ref
        {"ref": None},
        {"ref": ""},
        {"ref": 42},
        {"ref": "A", "dependsOn": "B"},  # dependsOn not a list
        {"ref": "A", "dependsOn": None},
    ],
)
def test_parse_skips_malformed_entries(entry: object) -> None:
    # A malformed entry never raises; a malformed-but-has-ref entry yields a
    # leaf, a no-ref entry yields nothing.
    adj = parse_dependency_graph([entry])
    assert isinstance(adj, dict)
    if isinstance(entry, dict) and isinstance(entry.get("ref"), str) and entry.get("ref"):
        assert adj == {entry["ref"]: []}
    else:
        assert adj == {}


def test_parse_skips_non_string_children() -> None:
    adj = parse_dependency_graph(
        [{"ref": "A", "dependsOn": ["B", None, 123, "", {"x": 1}, "C"]}]
    )
    assert adj == {"A": ["B", "C"]}


# ---------------------------------------------------------------------------
# compute_depths — happy path
# ---------------------------------------------------------------------------


def test_depths_linear_chain_implicit_root() -> None:
    # No forced root → in-degree-0 node (A) is the root at depth 1 (it IS the
    # direct dependency; there is no synthetic project node above it).
    adj = {"A": ["B"], "B": ["C"], "C": []}
    assert compute_depths(adj) == {"A": 1, "B": 2, "C": 3}


def test_depths_forced_root_is_zero() -> None:
    # A forced root (the project's metadata component) is depth 0; its direct
    # deps are depth 1.
    adj = {"app": ["A", "B"], "A": ["C"], "B": [], "C": []}
    assert compute_depths(adj, root_refs=["app"]) == {
        "app": 0,
        "A": 1,
        "B": 1,
        "C": 2,
    }


def test_depths_diamond_takes_shortest_path() -> None:
    # app → A → D and app → B → C → D ; D's shortest path is via A (depth 2).
    adj = {
        "app": ["A", "B"],
        "A": ["D"],
        "B": ["C"],
        "C": ["D"],
        "D": [],
    }
    depths = compute_depths(adj, root_refs=["app"])
    assert depths["D"] == 2  # not 3 — BFS picks the shortest


def test_depths_multiple_implicit_roots() -> None:
    # Two in-degree-0 roots, both depth 1.
    adj = {"A": ["C"], "B": ["C"], "C": []}
    assert compute_depths(adj) == {"A": 1, "B": 1, "C": 2}


def test_depths_empty_graph() -> None:
    assert compute_depths({}) == {}


def test_depths_forced_root_not_in_graph_falls_back() -> None:
    # A root_ref that is not a node is ignored → fall back to in-degree-0 roots.
    adj = {"A": ["B"], "B": []}
    assert compute_depths(adj, root_refs=["ghost"]) == {"A": 1, "B": 2}


# ---------------------------------------------------------------------------
# compute_depths — ADVERSARIAL shapes (must not hang / OOM / overflow / raise)
# ---------------------------------------------------------------------------


def test_adversarial_two_node_cycle() -> None:
    # A→B→A : cycle must terminate. With implicit roots both have a parent, so
    # the orphan-island seeder assigns deterministic depths starting at A=1.
    adj = parse_dependency_graph(
        [{"ref": "A", "dependsOn": ["B"]}, {"ref": "B", "dependsOn": ["A"]}]
    )
    depths = compute_depths(adj)
    assert set(depths) == {"A", "B"}
    assert all(0 <= d <= MAX_DEPTH for d in depths.values())


def test_adversarial_self_reference() -> None:
    # A→A is dropped at parse time; A is a lone in-degree-0 root.
    adj = parse_dependency_graph([{"ref": "A", "dependsOn": ["A"]}])
    assert adj == {"A": []}
    assert compute_depths(adj) == {"A": 1}


def test_adversarial_three_node_cycle_with_entry() -> None:
    # root → A → B → C → A (cycle among A,B,C, entered from root).
    adj = parse_dependency_graph(
        [
            {"ref": "root", "dependsOn": ["A"]},
            {"ref": "A", "dependsOn": ["B"]},
            {"ref": "B", "dependsOn": ["C"]},
            {"ref": "C", "dependsOn": ["A"]},
        ]
    )
    depths = compute_depths(adj, root_refs=["root"])
    assert depths == {"root": 0, "A": 1, "B": 2, "C": 3}  # cycle edge C→A ignored


def test_adversarial_dangling_child_ref() -> None:
    # B is referenced but never declared as a node → no depth invented for it,
    # and A still gets its depth.
    adj = parse_dependency_graph([{"ref": "A", "dependsOn": ["B"]}])
    assert adj == {"A": ["B"]}  # edge recorded
    depths = compute_depths(adj)
    assert depths == {"A": 1}  # B (dangling) has no node → no depth


def test_adversarial_deep_chain_is_clamped() -> None:
    # A multi-thousand-deep chain must (a) terminate and (b) never exceed the
    # MAX_DEPTH clamp (so the SmallInteger column can never overflow).
    n = 5000
    deps = [{"ref": f"n{i}", "dependsOn": [f"n{i + 1}"]} for i in range(n)]
    deps.append({"ref": f"n{n}", "dependsOn": []})
    adj = parse_dependency_graph(deps)
    depths = compute_depths(adj, root_refs=["n0"])
    assert max(depths.values()) == MAX_DEPTH  # clamped, not 5000
    assert all(0 <= d <= MAX_DEPTH for d in depths.values())


def test_adversarial_giant_fan_out() -> None:
    # One root with a huge fan-out — must complete; all children at depth 1.
    children = [f"c{i}" for i in range(20_000)]
    deps = [{"ref": "root", "dependsOn": children}]
    deps += [{"ref": c, "dependsOn": []} for c in children]
    adj = parse_dependency_graph(deps)
    depths = compute_depths(adj, root_refs=["root"])
    assert depths["root"] == 0
    assert all(depths[c] == 1 for c in children)
    assert len(depths) == 20_001


def test_adversarial_duplicate_edges_collapse() -> None:
    deps = [
        {"ref": "A", "dependsOn": ["B", "B", "B"]},
        {"ref": "B", "dependsOn": []},
    ]
    adj = parse_dependency_graph(deps)
    assert adj == {"A": ["B"], "B": []}
    assert compute_depths(adj, root_refs=["A"]) == {"A": 0, "B": 1}


def test_adversarial_pure_orphan_cycle_island_gets_depth() -> None:
    # A→B→C→A with no external root: every node has a parent, so there is no
    # in-degree-0 entry. The orphan-island seeder still assigns each a bounded
    # depth (no node left NULL, no infinite loop).
    adj = parse_dependency_graph(
        [
            {"ref": "A", "dependsOn": ["B"]},
            {"ref": "B", "dependsOn": ["C"]},
            {"ref": "C", "dependsOn": ["A"]},
        ]
    )
    depths = compute_depths(adj)
    assert set(depths) == {"A", "B", "C"}
    assert all(1 <= d <= MAX_DEPTH for d in depths.values())


def test_adversarial_node_cap_stops_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    # A hostile SBOM that declares more nodes than MAX_NODES must stop parsing
    # at the cap rather than churning unbounded. We shrink the cap to 3 so the
    # test stays fast; the parse must yield at most cap+1 keys and never raise.
    import integrations.dependency_graph as dg

    monkeypatch.setattr(dg, "MAX_NODES", 3)
    deps = [{"ref": f"n{i}", "dependsOn": []} for i in range(50)]
    adj = dg.parse_dependency_graph(deps)
    assert 0 < len(adj) <= 4  # cap (3) + the entry that tripped it


def test_adversarial_orphan_island_internal_diamond() -> None:
    # An orphan island (no in-degree-0 entry) with an internal diamond exercises
    # the re-seed loop's "already settled" short-circuit (a node reachable two
    # ways inside the seeded island). Must terminate with every node depth-bound.
    adj = parse_dependency_graph(
        [
            {"ref": "A", "dependsOn": ["B", "C"]},
            {"ref": "B", "dependsOn": ["D"]},
            {"ref": "C", "dependsOn": ["D"]},
            {"ref": "D", "dependsOn": ["A"]},  # back-edge → no in-degree-0 root
        ]
    )
    depths = compute_depths(adj)
    assert set(depths) == {"A", "B", "C", "D"}
    assert all(1 <= d <= MAX_DEPTH for d in depths.values())


def test_adversarial_disconnected_components() -> None:
    # Two independent subgraphs — both get depths from their own roots.
    adj = parse_dependency_graph(
        [
            {"ref": "r1", "dependsOn": ["a"]},
            {"ref": "a", "dependsOn": []},
            {"ref": "r2", "dependsOn": ["b"]},
            {"ref": "b", "dependsOn": []},
        ]
    )
    depths = compute_depths(adj)
    assert depths == {"r1": 1, "a": 2, "r2": 1, "b": 2}


# ---------------------------------------------------------------------------
# graph_depths_from_sbom — convenience over a full SBOM
# ---------------------------------------------------------------------------


def test_from_sbom_uses_metadata_component_as_root() -> None:
    sbom = {
        "metadata": {"component": {"bom-ref": "pkg:app@1"}},
        "dependencies": [
            {"ref": "pkg:app@1", "dependsOn": ["pkg:a@1", "pkg:b@1"]},
            {"ref": "pkg:a@1", "dependsOn": ["pkg:c@1"]},
            {"ref": "pkg:b@1", "dependsOn": []},
            {"ref": "pkg:c@1", "dependsOn": []},
        ],
    }
    depths = graph_depths_from_sbom(sbom)
    assert depths == {
        "pkg:app@1": 0,
        "pkg:a@1": 1,  # direct
        "pkg:b@1": 1,  # direct
        "pkg:c@1": 2,  # transitive
    }


def test_from_sbom_falls_back_to_purl_root() -> None:
    # metadata.component has no bom-ref but has a purl; use it as root.
    sbom = {
        "metadata": {"component": {"purl": "pkg:app@1"}},
        "dependencies": [
            {"ref": "pkg:app@1", "dependsOn": ["pkg:a@1"]},
            {"ref": "pkg:a@1", "dependsOn": []},
        ],
    }
    assert graph_depths_from_sbom(sbom) == {"pkg:app@1": 0, "pkg:a@1": 1}


def test_from_sbom_no_metadata_uses_in_degree_zero_roots() -> None:
    sbom = {
        "dependencies": [
            {"ref": "pkg:a@1", "dependsOn": ["pkg:b@1"]},
            {"ref": "pkg:b@1", "dependsOn": []},
        ],
    }
    assert graph_depths_from_sbom(sbom) == {"pkg:a@1": 1, "pkg:b@1": 2}


def test_from_sbom_empty_root_dependson_falls_back_to_orphan_roots() -> None:
    """cdxgen sometimes emits the metadata root with an EMPTY ``dependsOn``
    and floats the real direct dependencies as nodes nothing depends on
    (observed on Maven/Gradle source scans; sibling-project fix
    sktelecom/sbom-tools#278). Trusting that root would strand every other
    node in the orphan-island fallback, whose sorted-order seeding marks
    whichever ref sorts first as depth 1 — a transitive dep that sorts
    before its parent comes out "direct". The root must only be trusted
    when it actually declares children; otherwise fall back to in-degree-0
    root detection.

    Shape: root(empty) + zeta(direct) → alpha(transitive). ``alpha`` sorts
    before ``zeta``, so the buggy path classified it depth 1.
    """
    sbom = {
        "metadata": {"component": {"bom-ref": "pkg:maven/com.example/app@1.0"}},
        "dependencies": [
            {"ref": "pkg:maven/com.example/app@1.0", "dependsOn": []},
            {"ref": "pkg:maven/z.zeta/direct@1.0", "dependsOn": ["pkg:maven/a.alpha/trans@1.0"]},
            {"ref": "pkg:maven/a.alpha/trans@1.0", "dependsOn": []},
        ],
    }
    depths = graph_depths_from_sbom(sbom)
    assert depths["pkg:maven/z.zeta/direct@1.0"] == 1  # direct
    assert depths["pkg:maven/a.alpha/trans@1.0"] == 2  # transitive, NOT direct


def test_from_sbom_empty_root_multiple_directs_stay_direct() -> None:
    # Same empty-root shape with several real directs — all of them (and only
    # them) must come out depth 1 under the in-degree-0 fallback.
    sbom = {
        "metadata": {"component": {"bom-ref": "pkg:maven/com.example/app@1.0"}},
        "dependencies": [
            {"ref": "pkg:maven/com.example/app@1.0", "dependsOn": []},
            {"ref": "pkg:maven/g.b/direct-b@1.0", "dependsOn": ["pkg:maven/g.c/shared@1.0"]},
            {"ref": "pkg:maven/g.a/direct-a@1.0", "dependsOn": ["pkg:maven/g.c/shared@1.0"]},
            {"ref": "pkg:maven/g.c/shared@1.0", "dependsOn": []},
        ],
    }
    depths = graph_depths_from_sbom(sbom)
    assert depths["pkg:maven/g.a/direct-a@1.0"] == 1
    assert depths["pkg:maven/g.b/direct-b@1.0"] == 1
    assert depths["pkg:maven/g.c/shared@1.0"] == 2


@pytest.mark.parametrize(
    "sbom",
    [
        {},
        {"dependencies": None},
        {"dependencies": []},
        {"dependencies": "nope"},
        {"metadata": "broken", "dependencies": [{"ref": "A"}]},
    ],
)
def test_from_sbom_degrades_to_empty_or_simple(sbom: dict) -> None:
    depths = graph_depths_from_sbom(sbom)
    assert isinstance(depths, dict)
    # The only case with a usable node:
    if sbom.get("dependencies") == [{"ref": "A"}]:
        assert depths == {"A": 1}
    else:
        assert depths == {}
