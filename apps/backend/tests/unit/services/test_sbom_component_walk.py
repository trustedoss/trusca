# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Nested components — the walk itself, and the two stages that read it.

The defect this closes was silent by construction: Trivy reads the uploaded
file and can match a nested component, but the finding is resolved to a
persisted ``ComponentVersion`` by PURL, and persistence had never written one.
A vulnerability a scanner found disappeared between two of our own stages, with
nothing logged and nothing on screen.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services import sbom_component_walk as walk
from services.sbom_conformance import evaluate

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sbom_ingest"


# ---------------------------------------------------------------------------
# The walk.
# ---------------------------------------------------------------------------
def test_a_parent_comes_before_what_it_contains() -> None:
    """Persistence writes rows in iteration order; keep the document's own."""
    components = [
        {"name": "a", "components": [{"name": "a1"}, {"name": "a2"}]},
        {"name": "b"},
    ]
    assert [c["name"] for c in walk.iter_components(components)] == [
        "a",
        "a1",
        "a2",
        "b",
    ]


def test_nesting_is_followed_to_any_depth_within_the_ceiling() -> None:
    deep: dict[str, Any] = {"name": "d0"}
    node = deep
    for i in range(1, 10):
        child: dict[str, Any] = {"name": f"d{i}"}
        node["components"] = [child]
        node = child
    assert len(walk.iter_components([deep])) == 10


def test_the_walk_is_depth_bounded() -> None:
    """An attacker-controllable document must not drive unbounded recursion."""
    deep: dict[str, Any] = {"name": "d0"}
    node = deep
    for i in range(1, 200):
        child: dict[str, Any] = {"name": f"d{i}"}
        node["components"] = [child]
        node = child
    found = walk.iter_components([deep])
    assert len(found) == walk.MAX_NESTING_DEPTH + 1
    assert len(found) < 200


def test_a_structure_that_reaches_itself_terminates() -> None:
    """JSON cannot express this, but a caller mutating a parsed document can."""
    node: dict[str, Any] = {"name": "loop"}
    node["components"] = [node]
    assert [c["name"] for c in walk.iter_components([node])] == ["loop"]


@pytest.mark.parametrize(
    "hostile", ["not a list", 7, None, {"name": "x"}], ids=["str", "int", "null", "dict"]
)
def test_a_hostile_components_value_yields_nothing(hostile: Any) -> None:
    assert walk.iter_components(hostile) == []


def test_non_dict_entries_are_skipped_not_raised() -> None:
    assert [c["name"] for c in walk.iter_components(["x", 1, {"name": "ok"}])] == ["ok"]


def test_nested_count_says_how_many_came_from_nesting() -> None:
    components = [{"name": "a", "components": [{"name": "a1"}]}, {"name": "b"}]
    assert walk.nested_count(components) == 1
    assert walk.nested_count([{"name": "a"}]) == 0


# ---------------------------------------------------------------------------
# What the stages do with it.
# ---------------------------------------------------------------------------
def _doc(components: list[dict[str, Any]]) -> bytes:
    return json.dumps(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "metadata": {
                "timestamp": "2026-01-01T00:00:00Z",
                "tools": [{"name": "t", "version": "1"}],
                "component": {"name": "root", "version": "1.0"},
            },
            "components": components,
            "dependencies": [{"ref": "a", "dependsOn": ["b"]}],
        }
    ).encode()


def test_an_unidentified_nested_component_is_no_longer_invisible_to_scoring() -> None:
    """The document says everything at the top level is identified. It is not."""
    raw = _doc(
        [
            {
                "type": "library",
                "name": "parent",
                "version": "1.0",
                "purl": "pkg:npm/parent@1.0",
                "components": [{"type": "library", "name": "bundled"}],
            }
        ]
    )
    checks = {c.id: c for c in evaluate(raw).checks}
    assert checks["purl"].status == "fail"
    assert "bundled" in checks["purl"].missing
    assert checks["name-version"].status == "fail"


def test_the_repository_fixture_carries_the_shape_this_guards() -> None:
    """`realistic.cdx.json` has a nested component — this is not hypothetical."""
    doc = json.loads((_FIXTURES / "realistic.cdx.json").read_text("utf-8"))
    top = [c for c in doc["components"] if isinstance(c, dict)]
    flattened = walk.iter_components(doc["components"])
    assert len(flattened) > len(top)
    assert walk.nested_count(doc["components"]) >= 1
