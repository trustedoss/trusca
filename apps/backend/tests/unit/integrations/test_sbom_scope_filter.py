"""Unit tests — runtime-scope SBOM post-filter (Phase K).

The filter's contract has two halves, and both are safety-critical:

  * it must drop exactly the non-deployable set (maven ``optional``/
    ``excluded``, npm lockfile-``dev``) and prune the dependency graph to the
    survivors, always preserving the ``metadata.component`` root ref;
  * it must be structurally incapable of *over*-dropping: the hasScopes /
    hasDev guards no-op the whole pass when the evidence to filter safely is
    absent, an npm purl missing from the lockfile is kept (keep-if-unknown),
    and any error leaves the document untouched with ``applied=False``.

The module is pure (no DB / network / subprocess), mirroring
``integrations.npm_lockfile`` — the SBOM dict in, mutated dict out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from integrations.npm_lockfile import NpmLockfileData
from integrations.sbom_scope_filter import (
    FILTER_PROPERTY_NAME,
    filter_sbom_to_runtime_scope,
    rewrite_sbom_file,
)

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _maven(name: str, scope: str | None = None) -> dict[str, Any]:
    purl = f"pkg:maven/com.example/{name}@1.0.0"
    comp: dict[str, Any] = {
        "type": "library",
        "name": name,
        "version": "1.0.0",
        "purl": purl,
        "bom-ref": purl,
    }
    if scope is not None:
        comp["scope"] = scope
    return comp


def _npm(name: str, version: str = "1.0.0") -> dict[str, Any]:
    purl = f"pkg:npm/{name}@{version}"
    return {
        "type": "library",
        "name": name,
        "version": version,
        "purl": purl,
        "bom-ref": purl,
    }


def _sbom(
    components: list[dict[str, Any]],
    dependencies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {
            "component": {
                "type": "application",
                "name": "app-root",
                "bom-ref": "pkg:maven/com.example/app-root@1.0.0",
                "purl": "pkg:maven/com.example/app-root@1.0.0",
            }
        },
        "components": components,
        "dependencies": dependencies if dependencies is not None else [],
    }


def _lock(scopes: dict[str, str]) -> NpmLockfileData:
    return NpmLockfileData(scope_by_purl=scopes, adjacency={})


# ---------------------------------------------------------------------------
# Maven predicate
# ---------------------------------------------------------------------------


def test_maven_drops_optional_and_excluded_keeps_required_and_unscoped() -> None:
    sbom = _sbom(
        [
            _maven("spring-core", "required"),
            _maven("junit", "optional"),
            _maven("lombok", "excluded"),
            _maven("unscoped-lib"),  # no scope tag — keep-if-unknown
        ]
    )
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert result.applied is True
    assert result.dropped == {"maven": 2}
    names = [c["name"] for c in sbom["components"]]
    assert names == ["spring-core", "unscoped-lib"]
    assert result.kept_components == 2


def test_maven_has_scopes_guard_noop_when_no_required_scope() -> None:
    # An SBOM whose producer populated no ``required`` scope (fallback
    # generators) must be left untouched even if optional tags appear.
    sbom = _sbom([_maven("a", "optional"), _maven("b"), _maven("c", "excluded")])
    before = json.loads(json.dumps(sbom))
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert result.applied is False
    assert sbom == before


def test_maven_predicate_disabled_by_flag() -> None:
    sbom = _sbom([_maven("keep", "required"), _maven("junit", "optional")])
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None, maven=False)
    assert result.applied is False
    assert len(sbom["components"]) == 2


def test_non_maven_purl_untouched_by_maven_predicate() -> None:
    pypi = {
        "type": "library",
        "name": "requests",
        "version": "2.31.0",
        "purl": "pkg:pypi/requests@2.31.0",
        "scope": "optional",  # pypi optional is NOT the maven contract — keep
    }
    sbom = _sbom([_maven("keep", "required"), _maven("junit", "optional"), pypi])
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert result.dropped == {"maven": 1}
    assert any(c["name"] == "requests" for c in sbom["components"])


# ---------------------------------------------------------------------------
# Node predicate
# ---------------------------------------------------------------------------


def test_node_drops_lockfile_dev_entries() -> None:
    lock = _lock(
        {
            "pkg:npm/express@4.18.2": "required",
            "pkg:npm/jest@29.7.0": "dev",
            "pkg:npm/eslint@8.55.0": "dev",
        }
    )
    sbom = _sbom([_npm("express", "4.18.2"), _npm("jest", "29.7.0"), _npm("eslint", "8.55.0")])
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=lock)
    assert result.applied is True
    assert result.dropped == {"npm": 2}
    assert [c["name"] for c in sbom["components"]] == ["express"]


def test_node_keep_if_unknown_purl_absent_from_lockfile() -> None:
    # A nested monorepo manifest's packages are not in the root lockfile —
    # the filter may only remove components with positive dev evidence.
    lock = _lock({"pkg:npm/jest@29.7.0": "dev"})
    sbom = _sbom([_npm("jest", "29.7.0"), _npm("nested-only", "2.0.0")])
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=lock)
    assert result.dropped == {"npm": 1}
    assert [c["name"] for c in sbom["components"]] == ["nested-only"]


def test_node_has_dev_guard_noop_without_dev_entries() -> None:
    lock = _lock({"pkg:npm/express@4.18.2": "required"})
    sbom = _sbom([_npm("express", "4.18.2")])
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=lock)
    assert result.applied is False


def test_node_noop_when_lockfile_missing() -> None:
    sbom = _sbom([_npm("express", "4.18.2")])
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert result.applied is False


def test_node_predicate_disabled_by_flag() -> None:
    lock = _lock({"pkg:npm/jest@29.7.0": "dev"})
    sbom = _sbom([_npm("jest", "29.7.0")])
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=lock, node=False)
    assert result.applied is False
    assert len(sbom["components"]) == 1


# ---------------------------------------------------------------------------
# Graph pruning + root preservation
# ---------------------------------------------------------------------------


def test_dependency_graph_pruned_to_kept_refs_and_root_preserved() -> None:
    root_ref = "pkg:maven/com.example/app-root@1.0.0"
    keep_ref = "pkg:maven/com.example/spring-core@1.0.0"
    drop_ref = "pkg:maven/com.example/junit@1.0.0"
    sbom = _sbom(
        [_maven("spring-core", "required"), _maven("junit", "optional")],
        dependencies=[
            {"ref": root_ref, "dependsOn": [keep_ref, drop_ref]},
            {"ref": keep_ref, "dependsOn": [drop_ref]},
            {"ref": drop_ref, "dependsOn": []},
        ],
    )
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert result.dropped == {"maven": 1}
    deps = {d["ref"]: d["dependsOn"] for d in sbom["dependencies"]}
    # Dropped component's own entry removed; surviving dependsOn filtered.
    assert drop_ref not in deps
    assert deps[root_ref] == [keep_ref]
    assert deps[keep_ref] == []


def test_root_ref_entry_survives_even_if_root_not_in_components() -> None:
    # metadata.component rarely appears in components[]; its graph entry must
    # survive regardless (BomLens always adds the root to keptRefs).
    root_ref = "pkg:maven/com.example/app-root@1.0.0"
    sbom = _sbom(
        [_maven("spring-core", "required"), _maven("junit", "optional")],
        dependencies=[{"ref": root_ref, "dependsOn": []}],
    )
    filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert any(d.get("ref") == root_ref for d in sbom["dependencies"])


# ---------------------------------------------------------------------------
# Transparency property
# ---------------------------------------------------------------------------


def test_filter_property_stamped_with_per_ecosystem_counts() -> None:
    lock = _lock({"pkg:npm/jest@29.7.0": "dev"})
    sbom = _sbom(
        [_maven("keep", "required"), _maven("junit", "optional"), _npm("jest", "29.7.0")]
    )
    filter_sbom_to_runtime_scope(sbom, npm_lock=lock)
    props = sbom["metadata"]["properties"]
    stamped = [p for p in props if p["name"] == FILTER_PROPERTY_NAME]
    assert stamped == [{"name": FILTER_PROPERTY_NAME, "value": "maven=1,npm=1"}]


def test_filter_property_idempotent_on_rerun() -> None:
    sbom = _sbom([_maven("keep", "required"), _maven("junit", "optional")])
    filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    # Second pass over an already-filtered document: nothing left to drop, so
    # the original stamp must survive exactly once (no duplicates).
    filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    stamps = [
        p
        for p in sbom["metadata"]["properties"]
        if p["name"] == FILTER_PROPERTY_NAME
    ]
    assert len(stamps) == 1


def test_no_property_and_no_mutation_when_nothing_dropped() -> None:
    sbom = _sbom(
        [_maven("a", "required"), _maven("b", "required")],
        dependencies=[{"ref": "x", "dependsOn": ["y"]}],
    )
    before = json.loads(json.dumps(sbom))
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert result.applied is True  # predicate ran — it just found nothing
    assert result.dropped == {}
    assert sbom == before  # zero mutation: graph + properties untouched


# ---------------------------------------------------------------------------
# Adversarial / degradation
# ---------------------------------------------------------------------------


def test_components_not_a_list_degrades_to_noop() -> None:
    sbom = {"components": {"not": "a list"}}
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert result.applied is False


def test_non_dict_component_entries_are_kept() -> None:
    sbom = _sbom([_maven("keep", "required"), _maven("junit", "optional")])
    sbom["components"].append("not-a-dict")
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert result.dropped == {"maven": 1}
    assert "not-a-dict" in sbom["components"]


def test_missing_dependencies_array_tolerated() -> None:
    sbom = _sbom([_maven("keep", "required"), _maven("junit", "optional")])
    del sbom["dependencies"]
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert result.dropped == {"maven": 1}
    assert "dependencies" not in sbom


def test_non_string_purl_kept() -> None:
    sbom = _sbom([_maven("keep", "required")])
    sbom["components"].append({"name": "weird", "purl": 42, "scope": "optional"})
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert any(c.get("name") == "weird" for c in sbom["components"] if isinstance(c, dict))
    assert result.dropped == {}


# ---------------------------------------------------------------------------
# rewrite_sbom_file
# ---------------------------------------------------------------------------


def test_rewrite_sbom_file_atomic_success(tmp_path: Path) -> None:
    target = tmp_path / "bom.json"
    target.write_text('{"old": true}', encoding="utf-8")
    assert rewrite_sbom_file(target, {"new": True}) is True
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
    # No temp litter left behind.
    assert [p.name for p in tmp_path.iterdir()] == ["bom.json"]


def test_rewrite_sbom_file_failure_leaves_original_and_returns_false(
    tmp_path: Path,
) -> None:
    target = tmp_path / "missing-dir" / "bom.json"  # parent does not exist
    assert rewrite_sbom_file(target, {"new": True}) is False


def test_rewrite_sbom_file_unserializable_payload_keeps_original(
    tmp_path: Path,
) -> None:
    target = tmp_path / "bom.json"
    target.write_text('{"old": true}', encoding="utf-8")
    assert rewrite_sbom_file(target, {"bad": object()}) is False
    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}


# ---------------------------------------------------------------------------
# Audit trail — dropped identities (security-reviewer L2)
# ---------------------------------------------------------------------------


def test_dropped_refs_record_the_removed_purls() -> None:
    lock = _lock({"pkg:npm/jest@29.7.0": "dev"})
    sbom = _sbom(
        [_maven("keep", "required"), _maven("junit", "optional"), _npm("jest", "29.7.0")]
    )
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=lock)
    assert set(result.dropped_refs) == {
        "pkg:maven/com.example/junit@1.0.0",
        "pkg:npm/jest@29.7.0",
    }


def test_dropped_refs_bounded_while_counts_stay_exact() -> None:
    from integrations.sbom_scope_filter import MAX_DROPPED_REFS_RECORDED

    total = MAX_DROPPED_REFS_RECORDED + 50
    components = [_maven("keep", "required")] + [
        _maven(f"test-dep-{i}", "optional") for i in range(total)
    ]
    result = filter_sbom_to_runtime_scope(_sbom(components), npm_lock=None)
    assert result.dropped == {"maven": total}  # exact totals survive the cap
    assert len(result.dropped_refs) == MAX_DROPPED_REFS_RECORDED
