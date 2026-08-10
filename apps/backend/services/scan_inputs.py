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


# ---------------------------------------------------------------------------
# The document an ingest scan was handed
# ---------------------------------------------------------------------------
#
# An uploaded scan has no tree to inventory; what it has is one document that
# somebody else produced. The equivalent question is therefore not "which files
# were available" but "what was I given" — which generator wrote it, at which
# version, when, and how much did it claim to describe. Answering it later
# needs the answer recorded now: the upload is kept, but reading a 30 MB
# document to recall its spec version is not what a scan-detail page should do.
#
# Everything here is what the document SAYS about itself. A generator can write
# a timestamp that is wrong and a supplier that is nobody; recording the claim
# is still the point, because the claim is what a reader is comparing against.

#: Per-string ceiling. These values reach a JSONB column and a rendered page,
#: and a generator name is short — a 30 MB one is not a name.
_STRING_CAP: Final = 512

#: Ceiling on recorded tool entries. Real documents list one or two.
_MAX_TOOLS: Final = 20


def _clip(value: Any) -> str | None:
    """A bounded string, or None when there is nothing usable to record."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:_STRING_CAP]


def _cdx_tools(metadata: dict[str, Any]) -> list[dict[str, str | None]]:
    """Tool entries from either CycloneDX shape.

    Pre-1.5 documents carry ``metadata.tools`` as a list; 1.5+ carries an object
    holding ``components`` and ``services``. Both are in circulation and a
    reader does not care which one the generator chose.
    """
    tools = metadata.get("tools")
    raw: list[Any] = []
    if isinstance(tools, list):
        raw = tools
    elif isinstance(tools, dict):
        raw = _as_list_any(tools.get("components")) + _as_list_any(
            tools.get("services")
        )

    out: list[dict[str, str | None]] = []
    for entry in raw[:_MAX_TOOLS]:
        if not isinstance(entry, dict):
            continue
        name = _clip(entry.get("name"))
        if name is None:
            continue
        out.append({"name": name, "version": _clip(entry.get("version"))})
    return out


def _as_list_any(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict_any(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _cyclonedx_summary(doc: dict[str, Any]) -> dict[str, Any]:
    from services import sbom_component_walk

    metadata = _as_dict_any(doc.get("metadata"))
    component = _as_dict_any(metadata.get("component"))
    supplier = _as_dict_any(metadata.get("supplier"))
    authors = [
        name
        for name in (
            _clip(_as_dict_any(a).get("name")) for a in _as_list_any(metadata.get("authors"))
        )
        if name is not None
    ]

    return {
        "format": "cyclonedx",
        "spec_version": _clip(doc.get("specVersion")),
        "serial_number": _clip(doc.get("serialNumber")),
        "subject": _clip(component.get("name")),
        "subject_version": _clip(component.get("version")),
        "created": _clip(metadata.get("timestamp")),
        "tools": _cdx_tools(metadata),
        "authors": authors[:_MAX_TOOLS],
        "supplier": _clip(supplier.get("name")),
        "component_count": len(
            sbom_component_walk.iter_components(doc.get("components"))
        ),
    }


def _spdx_summary(doc: dict[str, Any]) -> dict[str, Any]:
    creation = _as_dict_any(doc.get("creationInfo"))
    creators = [c for c in _as_list_any(creation.get("creators")) if isinstance(c, str)]

    tools: list[dict[str, str | None]] = []
    authors: list[str] = []
    for creator in creators[:_MAX_TOOLS]:
        # SPDX states creators as "Tool: name-version", "Person: ...",
        # "Organization: ...". The prefix decides which field the value lands
        # in. It is dropped from a tool name, where it carries nothing once the
        # value sits in ``tools``, and kept on the others, where it is the only
        # thing distinguishing a person from an organisation.
        if creator.startswith("Tool:"):
            name = _clip(creator[len("Tool:") :])
            if name is not None:
                tools.append({"name": name, "version": None})
        else:
            author = _clip(creator)
            if author is not None:
                authors.append(author)

    packages = [p for p in _as_list_any(doc.get("packages")) if isinstance(p, dict)]
    return {
        "format": "spdx-json",
        "spec_version": _clip(doc.get("spdxVersion")),
        "serial_number": _clip(doc.get("documentNamespace")),
        "subject": _clip(doc.get("name")),
        "subject_version": None,
        "created": _clip(creation.get("created")),
        "tools": tools,
        "authors": authors,
        "supplier": None,
        "component_count": len(packages),
    }


def summarize_input_document(
    raw: bytes, *, original_filename: str | None = None
) -> dict[str, Any] | None:
    """Describe the document an ingest scan was handed, or None if it cannot be.

    None is returned for anything this cannot read — including SPDX Tag-Value,
    which is accepted for ingest but not summarised here. That is deliberate:
    a summary assembled from a document we did not parse would state a spec
    version and a generator that nobody read, and a reader comparing a scan
    against its input would be comparing against a guess. Not recording is an
    honest answer; the upload itself is preserved either way.

    Never raises. This describes a scan rather than performing one.
    """
    try:
        from services.sbom_conformance import (
            FORMAT_CYCLONEDX,
            FORMAT_SPDX_JSON,
            detect_format,
        )

        fmt, doc = detect_format(raw)
        if doc is None:
            return None
        if fmt == FORMAT_CYCLONEDX:
            summary = _cyclonedx_summary(doc)
        elif fmt == FORMAT_SPDX_JSON:
            summary = _spdx_summary(doc)
        else:
            return None

        summary["byte_size"] = len(raw)
        summary["original_filename"] = _clip(original_filename)
        return summary
    except Exception:  # pragma: no cover — defensive, see docstring
        log.warning("scan_inputs_summarize_failed", exc_info=True)
        return None


__all__ = [
    "MAX_DEPTH",
    "MAX_ENTRIES",
    "collect_manifest_inventory",
    "summarize_input_document",
]
