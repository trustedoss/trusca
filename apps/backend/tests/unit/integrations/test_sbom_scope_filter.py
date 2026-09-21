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

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from integrations.npm_lockfile import NpmLockfileData
from integrations.sbom_scope_filter import (
    FILTER_PROPERTY_NAME,
    NON_DEPLOYABLE_DIR_NAMES,
    NON_DEPLOYABLE_KEY,
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
# Audit trail — dropped identities (security review finding)
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


# ---------------------------------------------------------------------------
# Non-deployable path predicate (U2-A)
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _at(name: str, *manifests: str, ecosystem: str = "npm") -> dict[str, Any]:
    """A component whose cdxgen ``SrcFile`` property lists ``manifests``."""
    purl = f"pkg:{ecosystem}/{name}@1.0.0"
    comp: dict[str, Any] = {
        "type": "library",
        "name": name,
        "version": "1.0.0",
        "purl": purl,
        "bom-ref": purl,
    }
    if manifests:
        comp["properties"] = [{"name": "SrcFile", "value": "\n".join(manifests)}]
    return comp


_ROOT = "package-lock.json"


def _names_after(components: list[dict[str, Any]], **kwargs: Any) -> set[str]:
    sbom = _sbom([_at("root-dep", _ROOT), *components])
    filter_sbom_to_runtime_scope(sbom, npm_lock=None, **kwargs)
    return {c["name"] for c in sbom["components"]}


def test_non_deployable_dir_names_are_exactly_the_documented_set() -> None:
    # Literal, so widening the set is a deliberate edit of this test too.
    assert NON_DEPLOYABLE_DIR_NAMES == {
        "test", "tests", "__tests__", "testdata", "fixtures", "e2e",
        "example", "examples", "sample", "samples", "demo", "demos",
        "benchmark", "benchmarks", "bench",
    }  # fmt: skip


@pytest.mark.parametrize(
    "directory",
    [
        "test",
        "tests",
        "__tests__",
        "testdata",
        "fixtures",
        "e2e",
        "example",
        "examples",
        "sample",
        "samples",
        "demo",
        "demos",
        "benchmark",
        "benchmarks",
        "bench",
    ],  # fmt: skip
)
def test_each_listed_directory_name_drops_its_component(directory: str) -> None:
    kept = _names_after([_at("victim", f"pkgs/{directory}/app/package-lock.json")])
    assert "victim" not in kept
    assert "root-dep" in kept


@pytest.mark.parametrize(
    "manifest",
    [
        "Tests/app/package-lock.json",  # case
        "TEST/package.json",
        "EXAMPLES\\app\\package.json",  # windows separators
        "./examples/app/package.json",  # leading ./
        "a//examples//package.json",  # doubled separator
        "src/test/resources/pom.xml",  # nested Maven layout
        " examples/app/package.json ",  # stray whitespace
    ],
)
def test_path_variants_are_recognised(manifest: str) -> None:
    assert "victim" not in _names_after([_at("victim", manifest)])


@pytest.mark.parametrize(
    "manifest",
    [
        "contest/app/package.json",  # substring, not a segment
        "latest/app/package.json",
        "packages/lint-examples/package.json",
        "src/testing/package.json",
        "app/tests",  # a FILE named like a listed directory
        "app/demo",
        "tools/package.json",
        "docs/package.json",
        "tests.json",  # file NAME, at the scan root
        "examples",  # bare name, no directory part
        "package.json",
    ],
)
def test_non_matching_paths_are_kept(manifest: str) -> None:
    assert "victim" in _names_after([_at("victim", manifest)])


@pytest.mark.parametrize(
    "manifest",
    [
        "/abs/tests/package.json",  # absolute
        "C:/work/tests/package.json",  # drive letter
        "c:\\work\\tests\\package.json",
        "../tests/package.json",  # escapes the root
        "tests/../package.json",  # traversal hides the real location
        "",
        "   ",
    ],
)
def test_unclassifiable_paths_keep_the_component(manifest: str) -> None:
    assert "victim" in _names_after([_at("victim", manifest)])


def test_component_without_source_file_evidence_is_kept() -> None:
    assert "victim" in _names_after([_at("victim")])
    odd = _at("odd")
    odd["properties"] = [
        {"name": "SrcFile", "value": 7},
        "x",
        {"name": "Other", "value": "tests/x"},
    ]
    assert "odd" in _names_after([odd])


def test_component_also_in_a_deployable_manifest_is_kept() -> None:
    both = _at("shared", "examples/app/package-lock.json", "package-lock.json")
    only = _at("only", "examples/app/package-lock.json", "tests/package-lock.json")
    kept = _names_after([both, only])
    assert "shared" in kept
    assert "only" not in kept


def test_one_unclassifiable_manifest_among_excluded_ones_keeps_the_component() -> None:
    mixed = _at("mixed", "tests/a/package.json", "/abs/b/package.json")
    assert "mixed" in _names_after([mixed])


def test_newline_variants_of_the_source_file_list_are_split() -> None:
    real = _at("real-nl", "tests/a/package.json", "examples/b/package.json")
    literal = _at("literal-nl")
    literal["properties"] = [
        {"name": "SrcFile", "value": "tests/a/package.json\\nexamples/b/package.json"}
    ]
    crlf = _at("crlf")
    crlf["properties"] = [
        {"name": "SrcFile", "value": "tests/a/package.json\r\nexamples/b/package.json"}
    ]
    kept = _names_after([real, literal, crlf])
    assert kept == {"root-dep"}


def test_literal_backslash_n_separator_exposes_a_deployable_manifest() -> None:
    # Unsplit, the whole value would classify as one path under ``tests/``.
    comp = _at("joined")
    comp["properties"] = [{"name": "SrcFile", "value": "tests/a/package.json\\npackage.json"}]
    assert "joined" in _names_after([comp])


def test_repeated_source_file_properties_are_all_considered() -> None:
    comp = _at("twice", "tests/a/package.json")
    comp["properties"].append({"name": "SrcFile", "value": "package.json"})
    assert "twice" in _names_after([comp])


def test_has_deployable_guard_leaves_an_examples_only_document_untouched() -> None:
    sbom = _sbom([_at("a", "examples/one/package.json"), _at("b", "tests/two/package.json")])
    before = json.dumps(sbom, sort_keys=True)
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert result.applied is False
    assert json.dumps(sbom, sort_keys=True) == before


def test_guard_needs_evidence_of_a_deployable_manifest_not_just_a_survivor() -> None:
    # A component with no path evidence survives but proves nothing about
    # where the product's real manifests are.
    sbom = _sbom([_at("nopath"), _at("a", "examples/one/package.json")])
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert result.applied is False
    assert {c["name"] for c in sbom["components"]} == {"nopath", "a"}


def test_non_deployable_predicate_disabled_by_flag() -> None:
    kept = _names_after([_at("victim", "tests/app/package.json")], non_deployable=False)
    assert "victim" in kept


def test_non_deployable_drops_are_counted_recorded_and_pruned_from_the_graph() -> None:
    sbom = _sbom(
        [_at("root-dep", _ROOT), _at("victim", "examples/app/package.json")],
        dependencies=[
            {
                "ref": "pkg:maven/com.example/app-root@1.0.0",
                "dependsOn": ["pkg:npm/root-dep@1.0.0", "pkg:npm/victim@1.0.0"],
            },
            {"ref": "pkg:npm/victim@1.0.0", "dependsOn": []},
        ],
    )
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert result.applied is True
    assert result.dropped == {NON_DEPLOYABLE_KEY: 1}
    assert result.dropped_refs == ["pkg:npm/victim@1.0.0"]
    assert result.kept_components == 1
    graph = {e["ref"]: e["dependsOn"] for e in sbom["dependencies"]}
    assert graph == {"pkg:maven/com.example/app-root@1.0.0": ["pkg:npm/root-dep@1.0.0"]}
    props = {p["name"]: p["value"] for p in sbom["metadata"]["properties"]}
    assert props[FILTER_PROPERTY_NAME] == f"{NON_DEPLOYABLE_KEY}=1"


def test_path_drop_composes_with_the_maven_and_npm_predicates() -> None:
    lock = _lock({"pkg:npm/jest@29.7.0": "dev"})
    junit = _maven("junit", "optional")
    sbom = _sbom(
        [
            _maven("keep", "required"),
            junit,
            _npm("jest", "29.7.0"),
            _at("victim", "tests/app/package.json"),
            _at("root-dep", _ROOT),
        ]
    )
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=lock)
    assert result.dropped == {"maven": 1, "npm": 1, NON_DEPLOYABLE_KEY: 1}


# Real captured tool output. The expected sets are what a reviewer read and
# judged to be test/example fixtures, not derived from the code under test.


def _real(name: str) -> dict[str, Any]:
    path = FIXTURES / name
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)  # type: ignore[no-any-return]


def test_real_multi_repository_sbom_drops_exactly_the_reviewed_fixture_set() -> None:
    sbom = _real("sbom_ingest/real_cyclonedx_large_multi_10198.cdx.json.gz")
    before = len(sbom["components"])
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert result.dropped == {NON_DEPLOYABLE_KEY: 5}
    assert set(result.dropped_refs) == {
        "pkg:npm/%24%7B%7B%20values.name%20%7D%7D",  # create-app templates/.../examples/
        "pkg:npm/pkg-module",  # cli-module-build/src/tests/.../__fixtures__/
        "pkg:npm/pkg-default",
        "pkg:npm/pkg-commonjs",
        "pkg:gem/package@0.0.1",  # gitlab-foss spec/fixtures/
    }
    assert len(sbom["components"]) == before - 5


def test_real_python_sbom_drops_only_the_nested_test_fixture_requirement() -> None:
    sbom = _real("sbom/real_cyclonedx.json")
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert result.dropped_refs == ["pkg:pypi/requests@2.28.0"]


def test_real_monorepo_with_lint_examples_package_loses_nothing() -> None:
    # ``packages/lint-examples`` contains "examples" only as a substring.
    sbom = _real("sbom_ingest/real_cyclonedx_large_js_2544.cdx.json")
    result = filter_sbom_to_runtime_scope(sbom, npm_lock=None)
    assert result.dropped == {}
    assert result.kept_components == 2544
