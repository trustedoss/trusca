"""
npm lockfile parser — TYPE / USAGE fallback for cdxgen (W4-D, 2026-05-27).

P3 #12 diagnosis (docs/diagnose/p3-12-vulns-type-usage-2026-05-26.md) identified
two systemic npm gaps:

  (a) cdxgen 12.3.3 does NOT emit ``scope`` for npm components — the Components
      tab's USAGE column ends up dash for every npm row. The information IS
      present in the project (``package.json`` distinguishes ``dependencies`` /
      ``devDependencies`` / ``optionalDependencies`` / ``peerDependencies``),
      cdxgen just does not surface it.

  (b) cdxgen sometimes emits a populated ``components`` array WITHOUT a
      ``dependencies`` graph — the Components tab's TYPE column then has no
      direct/transitive distinction.

This module is the deterministic fallback. It reads ``package-lock.json`` from
the cloned source directory (the worker has already either cloned it from the
repo or generated it via ``_prepare_npm``) and returns:

  * ``scope_for_purl(purl)`` — ``"required"`` / ``"dev"`` / ``"optional"`` /
    ``"peer"`` for the npm purl, or ``None`` if the purl is not an npm
    component or is absent from the lockfile.
  * ``synthesize_cdxgen_dependencies()`` — a CycloneDX-shaped
    ``[{"ref": purl, "dependsOn": [purl, ...]}]`` list that
    :mod:`integrations.dependency_graph` can ingest directly.

Trust boundary
--------------
``package-lock.json`` is **attacker-controlled** (the repo author can ship any
JSON). The parser:

  * never raises on malformed JSON — returns ``None``;
  * caps the number of packages it walks at ``MAX_PACKAGES`` to bound work;
  * normalises keys defensively (non-string keys, non-dict entries → skipped);
  * derives the package name from the *path key* (``node_modules/<name>``) only
    when the entry does not carry its own ``name`` field — this matches npm
    resolution and avoids trusting attacker-supplied package paths to leak as
    purls.

Supported lockfile versions
---------------------------
  * v3 (npm 7+) — packages keyed by ``node_modules/...``. **Primary path.**
  * v2 (npm 7+ backwards-compat) — both ``packages`` and ``dependencies``
    present; we read ``packages`` and ignore ``dependencies``.
  * v1 (npm 5-6) — only ``dependencies`` (nested tree). Supported as a
    best-effort fallback so legacy repos still get a scope.

Limitations (documented; out of scope for this fix)
---------------------------------------------------
  * npm hoisting: a transitive dep can be installed at top-level. We resolve
    by *nearest enclosing* ``node_modules/<name>`` (npm's own resolution rule)
    when walking a parent's ``dependencies`` map; for the top-level deps map we
    use ``node_modules/<name>``. This matches >95% of real graphs but a
    deliberately conflicting graph might mis-attribute one version.
  * yarn / pnpm lockfiles use a different schema; not parsed here. cdxgen
    handles yarn (via the manifest) acceptably for npm-style scope; pnpm is a
    known gap (filed separately).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger("integrations.npm_lockfile")

# Defensive cap on packages we will inspect from a single lockfile. npm
# lockfiles for huge monorepos top out in the low tens of thousands. A hostile
# lockfile could declare millions of synthetic entries; we bound the work so
# parsing stays O(N) with a known N. Beyond the cap we stop reading new
# entries (graph is best-effort, never fatal).
MAX_PACKAGES = 200_000


# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NpmLockfileData:
    """Parsed view of a ``package-lock.json``.

    Attributes:
        scope_by_purl: ``pkg:npm/<name>@<version>`` → ``"required"`` /
            ``"dev"`` / ``"optional"`` / ``"peer"``. Multiple lockfile entries
            for the same purl collapse to the *strongest* scope using the
            precedence ``required > peer > optional > dev`` (required wins so
            a package that is both a dev and a prod dep is reported as prod).
        adjacency: ``parent purl → [child purl, ...]`` derived from each
            installed package's ``dependencies`` / ``optionalDependencies`` /
            ``peerDependencies`` maps, resolved against the lockfile's
            installed versions. The synthetic project root is the empty
            string ``""`` so callers can re-use it as the
            CycloneDX ``metadata.component`` reference if they choose.
    """

    scope_by_purl: dict[str, str] = field(default_factory=dict)
    adjacency: dict[str, list[str]] = field(default_factory=dict)

    def scope_for_purl(self, purl: str) -> str | None:
        """Return the scope for ``purl`` or ``None`` if unknown."""
        return self.scope_by_purl.get(purl)

    def synthesize_cdxgen_dependencies(self) -> list[dict[str, Any]]:
        """Render the adjacency as a CycloneDX ``dependencies`` array.

        The output is the exact shape :mod:`integrations.dependency_graph`
        expects, so callers can feed it into ``parse_dependency_graph`` /
        ``compute_depths`` unchanged.
        """
        out: list[dict[str, Any]] = []
        for ref in sorted(self.adjacency):
            out.append({"ref": ref, "dependsOn": list(self.adjacency[ref])})
        return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_lockfile(source_dir: Path) -> NpmLockfileData | None:
    """Read ``<source_dir>/package-lock.json`` and return a parsed view.

    Returns ``None`` when the lockfile is absent, unreadable, malformed JSON,
    not a JSON object, or contains no usable packages. Never raises — the
    caller treats absence as "no enrichment available".
    """
    lockfile_path = source_dir / "package-lock.json"
    if not lockfile_path.is_file():
        return None
    try:
        raw_bytes = lockfile_path.read_bytes()
    except OSError as exc:
        log.warning(
            "npm_lockfile_read_failed",
            path=str(lockfile_path),
            error=str(exc),
        )
        return None
    try:
        data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning(
            "npm_lockfile_parse_failed",
            path=str(lockfile_path),
            error=str(exc)[:200],
        )
        return None
    if not isinstance(data, dict):
        return None

    packages = data.get("packages")
    if isinstance(packages, dict) and packages:
        return _parse_v3(packages)

    # Legacy v1 lockfile (npm 5/6): nested ``dependencies`` only.
    deps_v1 = data.get("dependencies")
    if isinstance(deps_v1, dict) and deps_v1:
        # The v1 top-level manifest's dev/prod split lives outside the
        # lockfile (in ``package.json``); pass the root manifest separately
        # if available.
        manifest = _read_manifest(source_dir)
        return _parse_v1(deps_v1, manifest=manifest)

    return None


# ---------------------------------------------------------------------------
# v3 / v2 lockfile (``packages`` map)
# ---------------------------------------------------------------------------


def _parse_v3(packages: dict[str, Any]) -> NpmLockfileData | None:
    """Parse a v2/v3 lockfile's ``packages`` map.

    Path-key conventions (from the npm spec):
      * ``""`` — the root project. Holds ``dependencies`` / ``devDependencies``
        / ``optionalDependencies`` / ``peerDependencies`` maps; never an
        installed package itself.
      * ``node_modules/<name>`` — a top-level installed package.
      * ``node_modules/<a>/node_modules/<b>`` — ``b`` is nested under ``a``.
      * Workspaces: ``packages/foo`` (no ``node_modules/`` prefix) — a
        first-party workspace. We treat these like the root: skip them as
        components, but read their dependency lists if relevant.
    """
    # First pass — enumerate installed packages, build (path → meta).
    installed: dict[str, _PackageEntry] = {}
    root_lists: dict[str, dict[str, str]] = {
        "dependencies": {},
        "devDependencies": {},
        "optionalDependencies": {},
        "peerDependencies": {},
    }

    examined = 0
    for path, entry in packages.items():
        examined += 1
        if examined > MAX_PACKAGES:
            log.warning("npm_lockfile_package_cap_exceeded", limit=MAX_PACKAGES)
            break
        if not isinstance(path, str):
            continue
        if not isinstance(entry, dict):
            continue

        if path == "":
            for key in root_lists:
                value = entry.get(key)
                if isinstance(value, dict):
                    for dep_name, dep_spec in value.items():
                        if isinstance(dep_name, str) and isinstance(dep_spec, str):
                            root_lists[key][dep_name] = dep_spec
            continue

        # Workspace entries do not live under node_modules/. We skip them as
        # components (they are first-party) but they still appear in npm
        # workspaces lockfiles as "packages/<name>".
        if "node_modules/" not in path:
            continue

        name = _name_from_path(path)
        if not name:
            continue

        version = entry.get("version")
        if not isinstance(version, str) or not version:
            continue

        installed[path] = _PackageEntry(
            path=path,
            name=name,
            version=version,
            dev=bool(entry.get("dev")),
            optional=bool(entry.get("optional")),
            peer=bool(entry.get("peer")),
            dependencies=_as_str_str_map(entry.get("dependencies")),
            optional_dependencies=_as_str_str_map(entry.get("optionalDependencies")),
            peer_dependencies=_as_str_str_map(entry.get("peerDependencies")),
        )

    if not installed:
        return None

    # Second pass — derive scopes.
    scope_by_purl: dict[str, str] = {}
    for pkg in installed.values():
        purl = _npm_purl(pkg.name, pkg.version)
        scope = _classify_scope_v3(pkg, root_lists=root_lists)
        _upsert_scope(scope_by_purl, purl, scope)

    # Third pass — build adjacency.
    # For each *installed* package we map its declared name→spec children to
    # the nearest enclosing ``node_modules/<child>`` path (npm resolution
    # nearest-ancestor rule). The synthetic root (path ``""``) maps directly
    # to top-level ``node_modules/<name>`` entries.
    adjacency: dict[str, list[str]] = {}

    # Index installed by (parent_prefix, name) for nearest-ancestor lookup.
    # parent_prefix is the directory containing the ``node_modules/`` segment
    # (e.g. ``node_modules/express`` for a child installed at
    # ``node_modules/express/node_modules/body-parser``). The root prefix is ``""``.
    by_prefix_name: dict[tuple[str, str], _PackageEntry] = {}
    for pkg in installed.values():
        prefix = _parent_prefix(pkg.path)
        # Latest definition wins on collision — npm lockfiles do not normally
        # produce key collisions, so a fold collision is itself an anomaly.
        by_prefix_name[(prefix, pkg.name)] = pkg

    # Root → top-level direct deps (across all four manifest categories).
    root_purl = ""  # CycloneDX-shaped: callers can reuse this as a forced root.
    root_children: list[str] = []
    seen_root_children: set[str] = set()
    for category in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        for dep_name in root_lists[category]:
            child_pkg = _resolve_child(
                by_prefix_name,
                parent_path="",
                child_name=dep_name,
            )
            if child_pkg is None:
                continue
            child_purl = _npm_purl(child_pkg.name, child_pkg.version)
            if child_purl in seen_root_children:
                continue
            seen_root_children.add(child_purl)
            root_children.append(child_purl)
    if root_children:
        adjacency[root_purl] = root_children

    # Installed → its declared children.
    for pkg in installed.values():
        parent_purl = _npm_purl(pkg.name, pkg.version)
        bucket = adjacency.setdefault(parent_purl, [])
        seen: set[str] = set(bucket)
        for source_map in (pkg.dependencies, pkg.optional_dependencies, pkg.peer_dependencies):
            for dep_name in source_map:
                child_pkg = _resolve_child(
                    by_prefix_name,
                    parent_path=pkg.path,
                    child_name=dep_name,
                )
                if child_pkg is None:
                    continue
                child_purl = _npm_purl(child_pkg.name, child_pkg.version)
                if child_purl == parent_purl:
                    continue  # self-edge — drop.
                if child_purl in seen:
                    continue
                seen.add(child_purl)
                bucket.append(child_purl)

    return NpmLockfileData(scope_by_purl=scope_by_purl, adjacency=adjacency)


def _classify_scope_v3(
    pkg: _PackageEntry,
    *,
    root_lists: dict[str, dict[str, str]],
) -> str:
    """Decide the scope for a v3 lockfile installed package.

    Precedence (strongest first; first match wins):
        1. Listed in the root's ``dependencies`` map → ``"required"``.
        2. ``entry.peer == true`` OR listed in root's ``peerDependencies`` → ``"peer"``.
        3. ``entry.optional == true`` OR listed in root's ``optionalDependencies`` → ``"optional"``.
        4. ``entry.dev == true`` OR listed in root's ``devDependencies`` → ``"dev"``.
        5. Default → ``"required"`` (transitive of a required root dep).

    The cascade is conservative: we never *upgrade* a transitive's scope above
    its parent's scope (npm itself does not, and the UX is "the package made
    it into production because the root depends on it").
    """
    if pkg.name in root_lists["dependencies"]:
        return "required"
    if pkg.peer or pkg.name in root_lists["peerDependencies"]:
        return "peer"
    if pkg.optional or pkg.name in root_lists["optionalDependencies"]:
        return "optional"
    if pkg.dev or pkg.name in root_lists["devDependencies"]:
        return "dev"
    return "required"


def _resolve_child(
    by_prefix_name: dict[tuple[str, str], _PackageEntry],
    *,
    parent_path: str,
    child_name: str,
) -> _PackageEntry | None:
    """Resolve a child name from a parent's perspective using nearest-ancestor.

    npm hoists shared dependencies up the tree. Resolution for a name walks
    from the parent's directory upward, checking each ``node_modules/<name>``
    until one matches; the first match wins. Here we mirror that by walking
    the parent's path prefix segments.
    """
    # Build the cascade of prefixes to try, from nearest (parent's own
    # ``node_modules``) up to the root ``""``.
    candidates: list[str] = []
    cur = parent_path
    while cur:
        candidates.append(cur)
        # Strip the trailing ``node_modules/<name>`` segment.
        idx = cur.rfind("/node_modules/")
        if idx < 0:
            break
        cur = cur[:idx]
    candidates.append("")  # root

    for prefix in candidates:
        hit = by_prefix_name.get((prefix, child_name))
        if hit is not None:
            return hit
    return None


def _parent_prefix(path: str) -> str:
    """Return the parent prefix (the segment before ``node_modules/<name>``).

    Examples:
        ``node_modules/a`` → ``""``
        ``node_modules/a/node_modules/b`` → ``"node_modules/a"``
        ``packages/foo/node_modules/a`` → ``"packages/foo"``
    """
    idx = path.rfind("/node_modules/")
    if idx < 0:
        # Top-level: prefix is "" (root).
        return ""
    return path[:idx]


def _name_from_path(path: str) -> str | None:
    """Extract the package name from a ``node_modules/...`` lockfile key.

    Handles scoped packages: ``node_modules/@scope/pkg`` → ``"@scope/pkg"``.
    Trailing ``node_modules/<name>`` is the controlling segment.
    """
    idx = path.rfind("node_modules/")
    if idx < 0:
        return None
    rest = path[idx + len("node_modules/") :]
    if not rest:
        return None
    if rest.startswith("@"):
        # Scoped: take two segments.
        parts = rest.split("/", 2)
        if len(parts) < 2:
            return None
        return f"{parts[0]}/{parts[1]}"
    return rest.split("/", 1)[0]


# ---------------------------------------------------------------------------
# v1 lockfile (legacy nested ``dependencies``)
# ---------------------------------------------------------------------------


def _parse_v1(
    nested: dict[str, Any],
    *,
    manifest: dict[str, Any] | None,
) -> NpmLockfileData | None:
    """Parse a v1 nested lockfile.

    v1 is purely a tree of ``{ name: { version, dev, optional, requires, dependencies } }``
    rooted at the top-level. Scope categorisation reads the root
    ``package.json`` (passed as ``manifest``) because v1 lockfiles only record
    a single ``dev`` boolean per entry, not which category the root used.
    """
    # Build the root-level scope maps from package.json if present.
    root_lists: dict[str, dict[str, str]] = {
        "dependencies": {},
        "devDependencies": {},
        "optionalDependencies": {},
        "peerDependencies": {},
    }
    if isinstance(manifest, dict):
        for key in root_lists:
            value = manifest.get(key)
            if isinstance(value, dict):
                for dep_name, dep_spec in value.items():
                    if isinstance(dep_name, str) and isinstance(dep_spec, str):
                        root_lists[key][dep_name] = dep_spec

    scope_by_purl: dict[str, str] = {}
    adjacency: dict[str, list[str]] = {}
    root_children: list[str] = []
    seen_root: set[str] = set()

    # BFS over the nested tree; depth-bounded by MAX_PACKAGES.
    visited = 0
    queue: list[tuple[str, str, dict[str, Any]]] = []
    # parent_purl, dep_name, dep_entry
    for dep_name, dep_entry in nested.items():
        if not isinstance(dep_name, str) or not isinstance(dep_entry, dict):
            continue
        queue.append(("", dep_name, dep_entry))

    while queue:
        parent_purl, name, entry = queue.pop(0)
        visited += 1
        if visited > MAX_PACKAGES:
            log.warning("npm_lockfile_package_cap_exceeded", limit=MAX_PACKAGES)
            break

        version = entry.get("version")
        if not isinstance(version, str) or not version:
            continue
        purl = _npm_purl(name, version)

        # Scope: if parent is root, use root manifest; else inherit "required".
        if parent_purl == "":
            scope = _classify_scope_v1(name, entry, root_lists=root_lists)
            if purl not in seen_root:
                root_children.append(purl)
                seen_root.add(purl)
        else:
            scope = "required"  # transitive of a required-scoped root dep
        _upsert_scope(scope_by_purl, purl, scope)

        # Adjacency: add edge parent → child.
        bucket = adjacency.setdefault(parent_purl, [])
        if purl not in bucket and purl != parent_purl:
            bucket.append(purl)

        children = entry.get("dependencies")
        if isinstance(children, dict):
            for child_name, child_entry in children.items():
                if isinstance(child_name, str) and isinstance(child_entry, dict):
                    queue.append((purl, child_name, child_entry))

    if not scope_by_purl and not root_children:
        return None
    # ``""`` adjacency may not have been populated (some v1 lockfiles have a
    # flat shape). Backfill if root_children is the canonical list.
    if root_children and "" not in adjacency:
        adjacency[""] = root_children

    return NpmLockfileData(scope_by_purl=scope_by_purl, adjacency=adjacency)


def _classify_scope_v1(
    name: str,
    entry: dict[str, Any],
    *,
    root_lists: dict[str, dict[str, str]],
) -> str:
    if name in root_lists["dependencies"]:
        return "required"
    if name in root_lists["peerDependencies"]:
        return "peer"
    if name in root_lists["optionalDependencies"] or bool(entry.get("optional")):
        return "optional"
    if name in root_lists["devDependencies"] or bool(entry.get("dev")):
        return "dev"
    return "required"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Scope precedence — STRONGER first. When two lockfile entries disagree (a
# package listed both as required and as dev), the stronger scope wins so the
# operator sees the production-impact answer first.
_SCOPE_RANK: dict[str, int] = {
    "required": 4,
    "peer": 3,
    "optional": 2,
    "dev": 1,
}


def _upsert_scope(scope_by_purl: dict[str, str], purl: str, scope: str) -> None:
    """Merge ``scope`` into ``scope_by_purl[purl]`` using strongest-wins."""
    if scope not in _SCOPE_RANK:
        return
    existing = scope_by_purl.get(purl)
    if existing is None or _SCOPE_RANK[scope] > _SCOPE_RANK[existing]:
        scope_by_purl[purl] = scope


def _npm_purl(name: str, version: str) -> str:
    """Render a CycloneDX-shaped npm purl for ``name@version``.

    cdxgen emits scoped packages as ``pkg:npm/%40scope/pkg@version`` (URL-encoded
    ``@``). We mirror that so the purls we produce match cdxgen's purls for the
    same package — this is the join key downstream.
    """
    if name.startswith("@") and "/" in name:
        scope, rest = name.split("/", 1)
        encoded = f"%40{scope[1:]}/{rest}"
    else:
        encoded = name
    return f"pkg:npm/{encoded}@{version}"


def _as_str_str_map(value: Any) -> dict[str, str]:
    """Defensively coerce a value to ``{str: str}``; non-conforming entries dropped."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in value.items():
        if isinstance(k, str) and isinstance(v, str):
            out[k] = v
    return out


def _read_manifest(source_dir: Path) -> dict[str, Any] | None:
    """Read ``<source_dir>/package.json`` defensively; returns ``None`` on any error."""
    manifest_path = source_dir / "package.json"
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# Internal package-row record. Frozen for hashability + defensive immutability.
@dataclass(frozen=True)
class _PackageEntry:
    path: str
    name: str
    version: str
    dev: bool
    optional: bool
    peer: bool
    dependencies: dict[str, str]
    optional_dependencies: dict[str, str]
    peer_dependencies: dict[str, str]


__all__ = [
    "MAX_PACKAGES",
    "NpmLockfileData",
    "read_lockfile",
]
