# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Unit tests for :mod:`services.scan_inputs`.

The inventory is read to answer "did this scan see the file that declares the
component I expected?", so the properties that matter are what it refuses to
include (a vendored tree's manifests describe dependencies, not the project),
what it cannot be talked into (an unbounded walk of an attacker-shaped tree),
and that it says the same thing twice for the same tree — a diff between two
scans has to mean the source changed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from services import scan_inputs
from services.scan_inputs import collect_manifest_inventory


def _write(root: Path, relative: str, content: str = "{}") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _paths(inventory: dict[str, object]) -> list[str]:
    files = inventory["files"]
    assert isinstance(files, list)
    return [entry["path"] for entry in files]


# ---------------------------------------------------------------------------
# What counts as a declaration
# ---------------------------------------------------------------------------


def test_collects_manifests_and_lockfiles_across_the_tree(tmp_path: Path) -> None:
    """A monorepo declares dependencies below the root, not only at it."""
    _write(tmp_path, "package.json")
    _write(tmp_path, "package-lock.json")
    _write(tmp_path, "services/api/go.mod")
    _write(tmp_path, "services/api/go.sum")
    _write(tmp_path, "apps/web/pnpm-lock.yaml")
    _write(tmp_path, "README.md", "not a manifest")
    _write(tmp_path, "src/main.py", "print()")

    inventory = collect_manifest_inventory(tmp_path)

    assert inventory is not None
    assert _paths(inventory) == [
        "apps/web/pnpm-lock.yaml",
        "package-lock.json",
        "package.json",
        "services/api/go.mod",
        "services/api/go.sum",
    ]
    assert inventory["count"] == 5
    assert inventory["truncated"] is False


def test_project_files_named_by_suffix_are_collected(tmp_path: Path) -> None:
    """.NET names its manifest after the project, so the name cannot be listed."""
    _write(tmp_path, "src/Portal.csproj")
    _write(tmp_path, "Portal.sln")

    inventory = collect_manifest_inventory(tmp_path)

    assert inventory is not None
    assert _paths(inventory) == ["Portal.sln", "src/Portal.csproj"]


def test_each_entry_carries_size_and_hash(tmp_path: Path) -> None:
    content = '{"name": "demo"}'
    _write(tmp_path, "package.json", content)

    inventory = collect_manifest_inventory(tmp_path)

    assert inventory is not None
    entry = inventory["files"][0]  # type: ignore[index]
    assert entry["size"] == len(content)
    assert entry["sha256"] == hashlib.sha256(content.encode()).hexdigest()


# ---------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------


def test_installed_dependencies_are_not_the_projects_declaration(
    tmp_path: Path,
) -> None:
    """``node_modules`` holds a manifest per installed package.

    Those state what a dependency declares about itself. Including them would
    bury the one file the reader is looking for under thousands, and would let
    whatever happens to be installed decide the inventory's size.
    """
    _write(tmp_path, "package.json")
    _write(tmp_path, "node_modules/lodash/package.json")
    _write(tmp_path, "node_modules/.pnpm/react@18/node_modules/react/package.json")
    _write(tmp_path, "vendor/github.com/foo/bar/go.mod")
    _write(tmp_path, "Pods/Alamofire/Package.swift")
    _write(tmp_path, ".git/package.json")

    inventory = collect_manifest_inventory(tmp_path)

    assert inventory is not None
    assert _paths(inventory) == ["package.json"]


def test_build_output_is_not_source(tmp_path: Path) -> None:
    _write(tmp_path, "pom.xml")
    _write(tmp_path, "target/classes/pom.xml")
    _write(tmp_path, "build/generated/package.json")
    _write(tmp_path, "dist/package.json")

    inventory = collect_manifest_inventory(tmp_path)

    assert inventory is not None
    assert _paths(inventory) == ["pom.xml"]


def test_symlinks_are_neither_followed_nor_reported(tmp_path: Path) -> None:
    """A symlink describes something outside the tree that was scanned.

    A symlinked directory can also point back up it, which is the shape that
    turns a walk into a non-terminating one.
    """
    _write(tmp_path, "package.json")
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "go.mod").write_text("module outside")

    (tmp_path / "linked-dir").symlink_to(outside, target_is_directory=True)
    (tmp_path / "linked.json").symlink_to(outside / "go.mod")
    (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)

    inventory = collect_manifest_inventory(tmp_path)

    assert inventory is not None
    assert _paths(inventory) == ["package.json"]


def test_a_tree_with_no_declarations_records_nothing(tmp_path: Path) -> None:
    """None means "nothing found"; the caller leaves the column NULL, which
    means "not recorded". The two readings must not be merged here."""
    _write(tmp_path, "README.md", "docs only")
    assert collect_manifest_inventory(tmp_path) is None


def test_a_missing_directory_is_not_an_error(tmp_path: Path) -> None:
    assert collect_manifest_inventory(tmp_path / "never-fetched") is None


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_entry_ceiling_is_reported_rather_than_silently_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A list that stops without saying so reads as a complete answer."""
    monkeypatch.setattr(scan_inputs, "MAX_ENTRIES", 3)
    for i in range(10):
        _write(tmp_path, f"module{i}/package.json")

    inventory = collect_manifest_inventory(tmp_path)

    assert inventory is not None
    assert inventory["count"] == 3
    assert inventory["truncated"] is True


def test_depth_ceiling_stops_the_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scan_inputs, "MAX_DEPTH", 2)
    _write(tmp_path, "package.json")
    _write(tmp_path, "a/b/go.mod")
    _write(tmp_path, "a/b/c/d/e/Cargo.toml")

    inventory = collect_manifest_inventory(tmp_path)

    assert inventory is not None
    assert "package.json" in _paths(inventory)
    assert not any(path.endswith("Cargo.toml") for path in _paths(inventory))


def test_a_file_too_large_to_hash_is_still_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its presence is the answer; the hash is a convenience.

    Dropping the entry would report the file as absent, which is the one thing
    this must never do.
    """
    monkeypatch.setattr(scan_inputs, "MAX_HASH_BYTES", 4)
    _write(tmp_path, "requirements.txt", "a" * 100)

    inventory = collect_manifest_inventory(tmp_path)

    assert inventory is not None
    entry = inventory["files"][0]  # type: ignore[index]
    assert entry["path"] == "requirements.txt"
    assert entry["size"] == 100
    assert entry["sha256"] is None


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_the_same_tree_yields_the_same_inventory(tmp_path: Path) -> None:
    """A diff between two scans must mean the source changed, not the walk.

    Directory iteration order is filesystem-dependent, and nothing
    time-dependent is recorded, so re-scanning the same commit compares equal.
    """
    _write(tmp_path, "b/package.json")
    _write(tmp_path, "a/go.mod")
    _write(tmp_path, "Cargo.toml")

    first = collect_manifest_inventory(tmp_path)
    second = collect_manifest_inventory(tmp_path)

    assert first == second
    assert _paths(first) == sorted(_paths(first))  # type: ignore[arg-type]
