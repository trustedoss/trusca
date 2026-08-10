# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
The dependency manifests a scan had in front of it.

A scan that reports fewer components than expected raises one question — did it
not find the package, or did it never see the file that declares it? Nothing in
the record answered that. The scan log says which stages ran, the preserved
tarball holds the tree, but the tarball is retained latest-succeeded-per-project
and is gone for every earlier scan, which is exactly when the question gets
asked.

So each source scan records the manifests and lockfiles it had available. Not
the whole tree: the question is about what could be read as a declaration of
dependencies, and a list of every source file answers it no better while being
thousands of times larger. A missing ``package-lock.json`` explains a version
that resolved loosely; a ``pom.xml`` under a subdirectory nobody expected
explains components from a module the reader forgot about.

What is deliberately NOT here
-----------------------------
File contents. The preserved tarball carries those while it exists, and copying
them into a database column would put a project's source into a row that the
retention model expects to be small.

Vendored trees. ``node_modules`` and its equivalents contain a manifest per
installed package — thousands of files that say what a dependency declares
about itself, not what the project declares. Including them would bury the
handful of files that answer the question, and would put the inventory's size
in the hands of whatever the project happens to have installed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

import structlog

log = structlog.get_logger("services.scan_inputs")

#: Exact file names that declare dependencies, by ecosystem. A lockfile counts
#: as much as a manifest — its absence is itself an answer, since it is what
#: pins a version rather than a range.
_MANIFEST_NAMES: Final[frozenset[str]] = frozenset(
    {
        # Node
        "package.json",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        # Python
        "requirements.txt",
        "pyproject.toml",
        "poetry.lock",
        "Pipfile",
        "Pipfile.lock",
        "setup.py",
        "setup.cfg",
        # Java / Kotlin
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "gradle.lockfile",
        # Go
        "go.mod",
        "go.sum",
        # Ruby
        "Gemfile",
        "Gemfile.lock",
        # Rust
        "Cargo.toml",
        "Cargo.lock",
        # PHP
        "composer.json",
        "composer.lock",
        # .NET
        "packages.config",
        "packages.lock.json",
        # C / C++
        "conanfile.txt",
        "conanfile.py",
        "vcpkg.json",
        # Swift / CocoaPods
        "Podfile",
        "Podfile.lock",
        "Package.swift",
        "Package.resolved",
        # Dart
        "pubspec.yaml",
        "pubspec.lock",
    }
)

#: Suffixes that identify a manifest whose name varies (.NET projects).
_MANIFEST_SUFFIXES: Final[tuple[str, ...]] = (".csproj", ".fsproj", ".vbproj", ".sln")

#: Directory names never descended into. These hold dependencies that were
#: installed rather than declared — each carries its own manifests, which say
#: what a dependency states about itself and not what this project states.
_SKIP_DIRS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".venv",
        "venv",
        "node_modules",
        "bower_components",
        "vendor",
        "Pods",
        "__pycache__",
        ".gradle",
        ".mvn",
        "target",
        "build",
        "dist",
        "out",
        ".next",
        ".nuxt",
        ".terraform",
        ".trustedoss",
    }
)

#: Depth ceiling, counted from the scanned root. A manifest deeper than this in
#: a real repository is inside something generated.
MAX_DEPTH: Final = 12

#: Ceiling on recorded entries. A monorepo with hundreds of modules is normal;
#: tens of thousands means the walk found a tree it should not have. The
#: inventory records that it was truncated rather than silently shortening —
#: a list that stops without saying so reads as a complete answer.
MAX_ENTRIES: Final = 2000

#: Per-file size ceiling for hashing. A manifest is small; something named
#: ``requirements.txt`` at 100 MB is not one, and hashing it would spend the
#: worker's time to describe a file nobody will look up.
MAX_HASH_BYTES: Final = 16 * 1024 * 1024


def _is_manifest(name: str) -> bool:
    if name in _MANIFEST_NAMES:
        return True
    return name.endswith(_MANIFEST_SUFFIXES)


def _sha256(path: Path) -> str | None:
    """Hash a file in chunks, or None when it cannot be read or is too large."""
    try:
        if path.stat().st_size > MAX_HASH_BYTES:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _walk(source_dir: Path) -> Iterable[Path]:
    """Yield manifest paths under ``source_dir``, skipping vendored trees.

    Symlinks are not followed and not reported. A symlinked directory can point
    back up the tree, and a symlinked file describes something outside the tree
    that was scanned — neither answers "what did this scan have in front of it".
    """
    stack: list[tuple[Path, int]] = [(source_dir, 0)]
    while stack:
        directory, depth = stack.pop()
        if depth > MAX_DEPTH:
            continue
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name not in _SKIP_DIRS:
                        stack.append((entry, depth + 1))
                elif entry.is_file() and _is_manifest(entry.name):
                    yield entry
            except OSError:
                continue


def collect_manifest_inventory(source_dir: Path) -> dict[str, Any] | None:
    """Return what this scan had available to read, or None if nothing did.

    The result is deterministic for a given tree: entries are sorted by path and
    nothing time-dependent is recorded, so re-scanning the same commit produces
    the same inventory and a diff between two scans means the tree changed.

    Never raises. This is a description of a scan, not a stage of it, and a
    scan must not fail because its description could not be written.
    """
    try:
        root = Path(source_dir).resolve()
        if not root.is_dir():
            return None

        found: list[dict[str, Any]] = []
        truncated = False
        for path in _walk(root):
            if len(found) >= MAX_ENTRIES:
                truncated = True
                break
            try:
                relative = path.relative_to(root).as_posix()
                size = path.stat().st_size
            except (OSError, ValueError):
                continue
            found.append(
                {"path": relative, "size": size, "sha256": _sha256(path)}
            )

        if not found:
            return None

        found.sort(key=lambda entry: entry["path"])
        return {
            "files": found,
            "count": len(found),
            "truncated": truncated,
        }
    except Exception:  # pragma: no cover — defensive, see docstring
        log.warning("scan_inputs_collect_failed", exc_info=True)
        return None


__all__ = ["MAX_DEPTH", "MAX_ENTRIES", "collect_manifest_inventory"]
