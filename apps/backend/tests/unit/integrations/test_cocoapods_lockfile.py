"""Unit tests — CocoaPods lockfile parser + SBOM merge (Phase L).

The fixture at ``tests/fixtures/cocoapods/Podfile.lock`` is REAL ``pod
install`` output (vendored from BomLens ``tests/fixtures/ios-cocoapods``,
the file their iOS e2e verifies against): the ``Moya → Moya/Core →
Alamofire`` chain exercises subspec entries, trailing-colon parents,
constraint-only sub-entries and the block terminator (``DEPENDENCIES:``).

Contract halves:
  * parse — names/pinned versions/edges out of the PODS: block; subspec
    purl percent-encoding; the block ends at the first non-indented line;
    the name→ref guard means an edge can never point at a phantom ref.
  * merge — union into a cdxgen SBOM; guarded no-op when pkg:cocoapods/*
    already present; provenance property + metadata stamp; never raises.

Adversarial posture mirrors test_npm_lockfile.py: Podfile.lock is
attacker-controlled, so malformed shapes degrade to None / 0 — never raise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from integrations.cocoapods_lockfile import (
    MAX_PODS,
    CocoapodsLockfileData,
    merge_into_sbom,
    read_podfile_lock,
)

FIXTURE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "cocoapods" / "Podfile.lock"
)


def _write_lock(tmp_path: Path, content: str) -> Path:
    (tmp_path / "Podfile.lock").write_text(content, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Parsing — real fixture
# ---------------------------------------------------------------------------


def test_real_fixture_pods_versions_and_edges(tmp_path: Path) -> None:
    (tmp_path / "Podfile.lock").write_text(
        FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    data = read_podfile_lock(tmp_path)
    assert data is not None
    assert data.pods == {
        "Alamofire": "5.8.1",
        "Moya": "15.0.0",
        "Moya/Core": "15.0.0",  # subspec pinned at the parent's version
    }
    # Moya -> Moya/Core (trailing-colon parent), Moya/Core -> Alamofire
    # (constraint-only sub-entry "(~> 5.0)" — the constraint is NOT a version).
    assert data.edges["Moya"] == {"Moya/Core"}
    assert data.edges["Moya/Core"] == {"Alamofire"}
    assert data.edges["Alamofire"] == set()


def test_real_fixture_components_and_graph_shapes(tmp_path: Path) -> None:
    (tmp_path / "Podfile.lock").write_text(
        FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    data = read_podfile_lock(tmp_path)
    assert data is not None

    components = data.components()
    by_name = {c["name"]: c for c in components}
    # Subspec slash percent-encoded in the purl; display name stays raw.
    assert by_name["Moya/Core"]["purl"] == "pkg:cocoapods/Moya%2FCore@15.0.0"
    assert by_name["Alamofire"]["purl"] == "pkg:cocoapods/Alamofire@5.8.1"
    for component in components:
        assert component["bom-ref"] == component["purl"]
        assert {"name": "trusca:identifiedBy", "value": "podfile_lock"} in component[
            "properties"
        ]

    graph = data.synthesize_cdxgen_dependencies()
    graph_by_ref = {entry["ref"]: entry["dependsOn"] for entry in graph}
    assert graph_by_ref["pkg:cocoapods/Moya@15.0.0"] == [
        "pkg:cocoapods/Moya%2FCore@15.0.0"
    ]
    assert graph_by_ref["pkg:cocoapods/Moya%2FCore@15.0.0"] == [
        "pkg:cocoapods/Alamofire@5.8.1"
    ]
    # Leaf pods emit no entry (BomLens prints only pods with sub-deps).
    assert "pkg:cocoapods/Alamofire@5.8.1" not in graph_by_ref


# ---------------------------------------------------------------------------
# Parsing — block boundaries + guards
# ---------------------------------------------------------------------------


def test_pods_block_ends_at_next_top_level_key(tmp_path: Path) -> None:
    # A pod-shaped line AFTER the block terminator must not be parsed.
    src = _write_lock(
        tmp_path,
        "PODS:\n  - RealPod (1.0.0)\nDEPENDENCIES:\n  - RealPod\n"
        "SPEC REPOS:\n  - GhostPod (9.9.9)\n",
    )
    data = read_podfile_lock(src)
    assert data is not None
    assert set(data.pods) == {"RealPod"}


def test_edge_to_unknown_pod_is_skipped(tmp_path: Path) -> None:
    # Sub-dependency name that has no top-level PODS entry → no phantom ref.
    src = _write_lock(
        tmp_path,
        "PODS:\n  - Parent (1.0.0):\n    - MissingChild (~> 2.0)\n",
    )
    data = read_podfile_lock(src)
    assert data is not None
    assert data.edges["Parent"] == {"MissingChild"}
    assert data.synthesize_cdxgen_dependencies() == []  # guard drops it


def test_self_edge_dropped(tmp_path: Path) -> None:
    src = _write_lock(
        tmp_path,
        "PODS:\n  - Loop (1.0.0):\n    - Loop (= 1.0.0)\n",
    )
    data = read_podfile_lock(src)
    assert data is not None
    assert data.synthesize_cdxgen_dependencies() == []


def test_versionless_pod_entry_skipped_with_its_subs(tmp_path: Path) -> None:
    src = _write_lock(
        tmp_path,
        "PODS:\n  - NoVersion:\n    - Child (1.0)\n  - Real (2.0.0)\n",
    )
    data = read_podfile_lock(src)
    assert data is not None
    assert set(data.pods) == {"Real"}


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


def test_absent_lockfile_returns_none(tmp_path: Path) -> None:
    assert read_podfile_lock(tmp_path) is None


def test_nested_only_lockfile_returns_none(tmp_path: Path) -> None:
    nested = tmp_path / "app"
    nested.mkdir()
    (nested / "Podfile.lock").write_text("PODS:\n  - A (1.0)\n", encoding="utf-8")
    assert read_podfile_lock(tmp_path) is None  # root-only by design


def test_pods_dir_copy_not_treated_as_nested_signal(tmp_path: Path) -> None:
    pods_dir = tmp_path / "Pods"
    pods_dir.mkdir()
    (pods_dir / "Podfile.lock").write_text("PODS:\n  - A (1.0)\n", encoding="utf-8")
    assert read_podfile_lock(tmp_path) is None


def test_no_pods_block_returns_none(tmp_path: Path) -> None:
    src = _write_lock(tmp_path, "DEPENDENCIES:\n  - Something\n")
    assert read_podfile_lock(src) is None


def test_garbage_content_returns_none(tmp_path: Path) -> None:
    src = _write_lock(tmp_path, "\x00\x01 not yaml at all {{{{")
    assert read_podfile_lock(src) is None


def test_pod_cap_bounds_hostile_lockfile(tmp_path: Path) -> None:
    lines = ["PODS:"] + [f"  - P{i} (1.0.{i})" for i in range(MAX_PODS + 10)]
    src = _write_lock(tmp_path, "\n".join(lines) + "\n")
    data = read_podfile_lock(src)
    assert data is not None
    assert len(data.pods) == MAX_PODS


# ---------------------------------------------------------------------------
# merge_into_sbom
# ---------------------------------------------------------------------------


def _data() -> CocoapodsLockfileData:
    return CocoapodsLockfileData(
        pods={"Alamofire": "5.8.1", "Moya": "15.0.0"},
        edges={"Moya": {"Alamofire"}, "Alamofire": set()},
    )


def test_merge_appends_components_edges_and_stamp() -> None:
    sbom: dict[str, Any] = {
        "components": [{"purl": "pkg:swift/github.com/apple/swift-log@1.5.3"}],
        "dependencies": [],
        "metadata": {},
    }
    merged = merge_into_sbom(sbom, _data())
    assert merged == 2
    purls = {c["purl"] for c in sbom["components"] if isinstance(c, dict)}
    assert "pkg:cocoapods/Alamofire@5.8.1" in purls
    assert "pkg:cocoapods/Moya@15.0.0" in purls
    assert {
        "ref": "pkg:cocoapods/Moya@15.0.0",
        "dependsOn": ["pkg:cocoapods/Alamofire@5.8.1"],
    } in sbom["dependencies"]
    assert {"name": "trusca:cocoapods", "value": "podfile_lock:2"} in sbom[
        "metadata"
    ]["properties"]


def test_merge_noop_when_pods_already_present() -> None:
    sbom: dict[str, Any] = {
        "components": [{"purl": "pkg:cocoapods/Existing@1.0.0"}],
        "dependencies": [],
    }
    assert merge_into_sbom(sbom, _data()) == 0
    assert len(sbom["components"]) == 1  # untouched


def test_merge_tolerates_missing_arrays() -> None:
    sbom: dict = {}
    merged = merge_into_sbom(sbom, _data())
    assert merged == 2
    assert len(sbom["components"]) == 2
    assert isinstance(sbom["dependencies"], list)


def test_merge_never_raises_on_hostile_document() -> None:
    assert merge_into_sbom({"components": "not-a-list"}, _data()) == 0
