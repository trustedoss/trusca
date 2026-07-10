"""
CocoaPods lockfile parser — iOS component fill-in for cdxgen (Phase L).

cdxgen's cocoapods cataloger requires the ``pod`` CLI; when a ``Podfile`` is
present and ``pod`` is not (the TRUSCA worker ships no Ruby/CocoaPods
toolchain), the cataloger throws instead of skipping and aborts the whole
SBOM stage. The crash half of the fix passes ``--exclude-type cocoapods`` to
cdxgen (see :mod:`integrations.cdxgen`); this module is the fill-in half —
it reconstructs the pod set and its dependency graph offline from the
committed ``Podfile.lock``, whose ``PODS:`` block is the fully-resolved
truth (direct + transitive, pinned versions, sub-dependency lists).

Port of BomLens ``docker/lib/parse-podfile-lock.py`` + the component/merge
conventions of ``identify-cocoapods.sh``, with one deliberate difference:
BomLens parses components out of syft output and only reconstructs edges by
hand; TRUSCA's worker ships no syft, and the ``PODS:`` block already carries
names + pinned versions, so this parser produces both halves itself (no new
tool dependency).

Why parse by hand instead of a YAML lib: Podfile.lock's ``PODS:`` block has a
fixed two-space layout — a top-level ``  - Name (ver)`` entry, optionally
followed by four-space ``    - SubName (constraint)`` children — and that is
all we need. A YAML dependency for two regexes is not worth its supply-chain
surface.

Trust boundary
--------------
``Podfile.lock`` is **attacker-controlled** (the repo author ships any text).
The parser mirrors :mod:`integrations.npm_lockfile`'s discipline:

  * never raises — absent/undecodable/empty lockfiles return ``None``;
  * caps parsed pods at ``MAX_PODS`` to bound work;
  * edges are emitted only between pods that exist in the parsed set
    (name→ref guard), self-edges dropped — a hostile sub-dependency name can
    never mint a phantom ref;
  * subspec names (``Moya/Core``) are percent-encoded into the purl name
    segment so the emitted purl stays spec-valid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import structlog

log = structlog.get_logger("integrations.cocoapods_lockfile")

# Defensive cap on PODS entries. Real Podfile.locks top out in the hundreds;
# a hostile file could declare millions of synthetic lines.
MAX_PODS = 20_000

# "  - Alamofire (5.8.1)" / "  - Moya (15.0.0):" (trailing colon when children
# follow). Version group optional — a malformed entry without one is skipped.
_POD_RE = re.compile(r"^  - (?P<name>.+?)(?: \((?P<ver>[^)]*)\))?:?\s*$")
# "    - Moya/Core (= 15.0.0)" — name only; the constraint is not a version
# (the sub-dependency's pinned version comes from its own top-level entry).
_SUB_RE = re.compile(r"^    - (?P<name>.+?)(?: \([^)]*\))?\s*$")

# Provenance property stamped on every emitted component (raw_data-visible
# after persist, mirrors the scanoss/npm_lockfile source-marker family).
IDENTIFIED_BY_PROPERTY = {"name": "trusca:identifiedBy", "value": "podfile_lock"}


@dataclass(frozen=True)
class CocoapodsLockfileData:
    """Parsed view of a ``Podfile.lock``'s ``PODS:`` block.

    Attributes:
        pods: pod name → pinned version (subspecs like ``Moya/Core`` are
            their own entries, pinned at the parent's version).
        edges: pod name → sub-dependency names (names only; resolved to purls
            at synthesis time through the ``pods`` map so every edge points
            at a real emitted component).
    """

    pods: dict[str, str] = field(default_factory=dict)
    edges: dict[str, set[str]] = field(default_factory=dict)

    def purl_for(self, name: str) -> str:
        """CycloneDX purl for a pod name (subspec ``/`` percent-encoded)."""
        version = self.pods[name]
        return f"pkg:cocoapods/{quote(name, safe='')}@{version}"

    def components(self) -> list[dict[str, Any]]:
        """cdxgen-shaped component dicts for every parsed pod."""
        out: list[dict[str, Any]] = []
        for name in sorted(self.pods):
            purl = self.purl_for(name)
            out.append(
                {
                    "type": "library",
                    "name": name,
                    "version": self.pods[name],
                    "purl": purl,
                    "bom-ref": purl,
                    "properties": [dict(IDENTIFIED_BY_PROPERTY)],
                }
            )
        return out

    def synthesize_cdxgen_dependencies(self) -> list[dict[str, Any]]:
        """CycloneDX ``dependencies`` entries for pods with sub-dependencies.

        Same output shape as
        :meth:`integrations.npm_lockfile.NpmLockfileData.synthesize_cdxgen_dependencies`
        so the graph persist path ingests it unchanged. Unknown names are
        skipped and self-edges dropped (BomLens name2ref guard).
        """
        out: list[dict[str, Any]] = []
        for name in sorted(self.edges):
            if name not in self.pods:
                continue
            ref = self.purl_for(name)
            depends = sorted(
                {
                    self.purl_for(sub)
                    for sub in self.edges[name]
                    if sub in self.pods and sub != name
                }
            )
            if depends:
                out.append({"ref": ref, "dependsOn": depends})
        return out


def read_podfile_lock(source_dir: Path) -> CocoapodsLockfileData | None:
    """Parse ``<source_dir>/Podfile.lock``; ``None`` on absence or any error.

    Root-only by design (matches the npm lockfile reader): a nested-only
    Podfile.lock still gets the cdxgen crash fix, just no pod fill-in — an
    info line flags it for future extension if real repos demand it.
    """
    lock_path = source_dir / "Podfile.lock"
    if not lock_path.is_file():
        for pattern in ("*/Podfile.lock", "*/*/Podfile.lock"):
            for nested in source_dir.glob(pattern):
                if "Pods" not in nested.parts:
                    log.info(
                        "cocoapods_lockfile_not_at_root", found=str(nested)
                    )
                    break
        return None
    try:
        lines = lock_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError as exc:
        log.warning(
            "cocoapods_lockfile_read_failed",
            path=str(lock_path),
            error=str(exc),
        )
        return None

    pods: dict[str, str] = {}
    edges: dict[str, set[str]] = {}
    in_pods = False
    current: str | None = None
    parsed = 0
    for line in lines:
        if not in_pods:
            if line.rstrip() == "PODS:":
                in_pods = True
            continue
        # The PODS block ends at the next top-level key (a non-indented,
        # non-empty line such as ``DEPENDENCIES:``).
        if line and not line.startswith(" "):
            break
        pod_match = _POD_RE.match(line)
        if pod_match:
            parsed += 1
            if parsed > MAX_PODS:
                log.warning("cocoapods_lockfile_pod_cap_exceeded", limit=MAX_PODS)
                break
            name = pod_match.group("name").strip()
            version = (pod_match.group("ver") or "").strip()
            if not name or not version:
                current = None  # malformed/versionless entry — skip its subs too
                continue
            pods[name] = version
            edges.setdefault(name, set())
            current = name
            continue
        sub_match = _SUB_RE.match(line)
        if sub_match and current is not None:
            edges[current].add(sub_match.group("name").strip())

    if not pods:
        return None
    return CocoapodsLockfileData(pods=pods, edges=edges)


def merge_into_sbom(sbom: dict[str, Any], data: CocoapodsLockfileData) -> int:
    """Union the pod components + edges into ``sbom``; returns merged count.

    Guarded no-op (returns 0) when the document already carries any
    ``pkg:cocoapods/`` component — e.g. a future pod-capable cdxgen image —
    so pods are never double-counted (BomLens ``identify-cocoapods`` guard).
    Never raises.
    """
    try:
        components = sbom.setdefault("components", [])
        if not isinstance(components, list):
            return 0
        for existing in components:
            if isinstance(existing, dict):
                purl = existing.get("purl")
                if isinstance(purl, str) and purl.startswith("pkg:cocoapods/"):
                    log.info("cocoapods_merge_skipped_existing_pods")
                    return 0
        pod_components = data.components()
        components.extend(pod_components)

        dependencies = sbom.setdefault("dependencies", [])
        if isinstance(dependencies, list):
            dependencies.extend(data.synthesize_cdxgen_dependencies())

        metadata = sbom.setdefault("metadata", {})
        if isinstance(metadata, dict):
            properties = metadata.setdefault("properties", [])
            if isinstance(properties, list):
                properties[:] = [
                    prop
                    for prop in properties
                    if not (
                        isinstance(prop, dict)
                        and prop.get("name") == "trusca:cocoapods"
                    )
                ]
                properties.append(
                    {
                        "name": "trusca:cocoapods",
                        "value": f"podfile_lock:{len(pod_components)}",
                    }
                )
        return len(pod_components)
    except Exception:  # noqa: BLE001 — fill-in must never break a scan
        log.warning("cocoapods_merge_failed", exc_info=True)
        return 0


__all__ = [
    "MAX_PODS",
    "CocoapodsLockfileData",
    "merge_into_sbom",
    "read_podfile_lock",
]
