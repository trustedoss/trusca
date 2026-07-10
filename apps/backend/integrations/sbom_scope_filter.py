"""
Runtime-scope SBOM post-filter — drop non-shipping dependencies (Phase K).

cdxgen keeps every *resolved* node, so a deployed application's SBOM also
carries its test / provided / dev toolchain (junit, lombok, devDependencies)
as if those shipped. Downstream that inflates CVE counts, license obligations
and NOTICE content for artifacts that never reach production. This module
filters the CycloneDX document down to the deployable runtime set, mirroring
BomLens ``build-prep.sh`` (#331/#335/#337/#341):

  * **Maven** — data-driven off cdxgen's own scope tags. cdxgen maps Maven
    ``compile``/``runtime`` → ``required`` (kept), ``test`` → ``optional``
    (dropped), ``provided``/``system`` → ``excluded`` (dropped). Guard: the
    filter only runs when at least one ``pkg:maven/`` component carries
    ``scope == "required"`` (the *hasScopes* guard) — an SBOM whose producer
    populated no scopes (e.g. a fallback generator) is left untouched so
    recall never regresses. Known caveat (documented in the user guide):
    cdxgen also tags ``<optional>true</optional>`` runtime deps as
    ``optional``, so those are dropped too; ``SCAN_SCOPE_FILTER_MAVEN_ENABLED=false``
    is the escape hatch.
  * **Node** — deliberate divergence from BomLens (which re-resolves the
    production set via ``npm install --omit=dev``): TRUSCA's prep stage
    already guarantees a ``package-lock.json`` (``_prepare_npm``) and
    :mod:`integrations.npm_lockfile` already classifies every installed
    package as ``required``/``dev``/``optional``/``peer``. We drop a
    ``pkg:npm/`` component only when the lockfile positively classifies it
    ``dev``. Guards: the lockfile parsed (*hasLock*) AND classified at least
    one entry ``dev`` (*hasDev*); an npm purl **absent** from the lockfile is
    KEPT (keep-if-unknown — a monorepo's nested non-workspace manifests are
    not covered by the root lockfile, and the filter must only remove
    components it has positive dev evidence for).

Shared tail (all ecosystems): kept refs = ``bom-ref``∥``purl`` of every kept
component **plus the ``metadata.component`` root ref**, then the
``dependencies[]`` graph is pruned to kept refs (entries dropped, each
``dependsOn`` filtered).

Failure posture (BomLens parity): the filter NEVER raises and NEVER breaks a
scan — any error returns ``applied=False`` and the SBOM is left exactly as
cdxgen wrote it. Callers should filter a working copy and only commit it once
the on-disk rewrite succeeded, so the in-memory document, the persisted rows,
the signed artifact and Trivy's input can never diverge.

Layering: pure functions only — no DB, no Celery, no config reads (the caller
resolves the ``SCAN_SCOPE_FILTER_*`` toggles). Same discipline as
:mod:`integrations.npm_lockfile`.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from integrations.npm_lockfile import NpmLockfileData

log = structlog.get_logger("integrations.sbom_scope_filter")

# Maven scope tags cdxgen emits for non-deployable nodes. ``required`` and
# *unscoped* components are kept (an unscoped node carries no evidence it is
# test-only — keep-if-unknown, same philosophy as the Node predicate).
_MAVEN_DROP_SCOPES = frozenset({"optional", "excluded"})

# SBOM metadata property stamped onto a filtered document so the signed /
# downloadable artifact self-documents what was removed (mirrors the
# ``bomlens:*`` provenance convention).
FILTER_PROPERTY_NAME = "trusca:scope_filter"

# Audit-trail bound (security-reviewer L2): the purls of dropped components
# are recorded (a bounded sample + the exact totals) so a reviewer can verify
# that nothing shipping was hidden behind an opaque count — the npm predicate
# trusts an attacker-controlled lockfile classification, so counts alone are
# not an auditable trail. 200 covers every realistic drop set; a hostile SBOM
# cannot bloat scan_metadata past it.
MAX_DROPPED_REFS_RECORDED = 200


@dataclass(frozen=True)
class ScopeFilterResult:
    """Outcome of one filter pass.

    Attributes:
        applied: ``True`` when at least one ecosystem predicate was active
            (its guards passed), even if it dropped nothing. ``False`` means
            the document was left untouched — guards no-opped or an error
            occurred.
        dropped: per-ecosystem drop counts, only non-zero keys
            (``{"maven": 12, "npm": 340}``).
        kept_components: component count after filtering (unchanged count
            when ``applied`` is ``False``).
    """

    applied: bool
    dropped: dict[str, int] = field(default_factory=dict)
    kept_components: int = 0
    # Purls of the dropped components — the audit trail (bounded at
    # MAX_DROPPED_REFS_RECORDED; ``dropped`` carries the exact totals).
    dropped_refs: list[str] = field(default_factory=list)

    @property
    def total_dropped(self) -> int:
        return sum(self.dropped.values())


def filter_sbom_to_runtime_scope(
    sbom: dict[str, Any],
    *,
    npm_lock: NpmLockfileData | None,
    maven: bool = True,
    node: bool = True,
) -> ScopeFilterResult:
    """Filter ``sbom`` (in place) down to the deployable runtime set.

    ``maven`` / ``node`` are the per-ecosystem toggles (resolved from config
    by the caller). Never raises — on any error the document is left in its
    pre-call state only if the error happened before the first mutation;
    callers that need transactional semantics must pass a working copy (see
    module docstring).
    """
    try:
        return _filter(sbom, npm_lock=npm_lock, maven=maven, node=node)
    except Exception:  # noqa: BLE001 — the filter must never break a scan
        log.warning("scope_filter_failed", exc_info=True)
        components = sbom.get("components")
        kept = len(components) if isinstance(components, list) else 0
        return ScopeFilterResult(applied=False, kept_components=kept)


def rewrite_sbom_file(path: Path, sbom: dict[str, Any]) -> bool:
    """Atomically rewrite the on-disk SBOM (temp file + ``os.replace``).

    Returns ``False`` (after logging) on any error — the original file is
    left intact, so a failed rewrite degrades to "filter skipped".
    """
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name, suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(sbom, handle, ensure_ascii=False)
        os.replace(tmp_path, path)
        tmp_path = None
        return True
    except Exception:  # noqa: BLE001 — degrade, never break the scan
        log.warning("scope_filter_rewrite_failed", path=str(path), exc_info=True)
        return False
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _filter(
    sbom: dict[str, Any],
    *,
    npm_lock: NpmLockfileData | None,
    maven: bool,
    node: bool,
) -> ScopeFilterResult:
    components = sbom.get("components")
    if not isinstance(components, list):
        return ScopeFilterResult(applied=False)

    # Guards — each ecosystem predicate activates only when the data to
    # filter *safely* is present (BomLens hasScopes parity + hasDev).
    maven_active = maven and _has_maven_scopes(components)
    node_active = node and _has_dev_entries(npm_lock)

    if not maven_active and not node_active:
        return ScopeFilterResult(applied=False, kept_components=len(components))

    kept: list[Any] = []
    dropped: dict[str, int] = {}
    dropped_refs: list[str] = []

    def _record_drop(ecosystem: str, purl: str) -> None:
        dropped[ecosystem] = dropped.get(ecosystem, 0) + 1
        if len(dropped_refs) < MAX_DROPPED_REFS_RECORDED:
            dropped_refs.append(purl)

    for component in components:
        if not isinstance(component, dict):
            kept.append(component)  # defensive: never drop what we can't read
            continue
        purl = component.get("purl")
        purl = purl if isinstance(purl, str) else ""
        if maven_active and purl.startswith("pkg:maven/"):
            if component.get("scope") in _MAVEN_DROP_SCOPES:
                _record_drop("maven", purl)
                continue
        # ``node_active`` implies a parsed lockfile; the explicit None check
        # (not an assert — stripped under ``python -O``) keeps that visible.
        if node_active and npm_lock is not None and purl.startswith("pkg:npm/"):
            if npm_lock.scope_for_purl(purl) == "dev":
                _record_drop("npm", purl)
                continue
        kept.append(component)

    if not dropped:
        # Predicates ran but found nothing non-deployable — no mutation.
        return ScopeFilterResult(applied=True, kept_components=len(components))

    sbom["components"] = kept
    _prune_dependencies(sbom, _kept_refs(sbom, kept))
    _stamp_filter_property(sbom, dropped)
    return ScopeFilterResult(
        applied=True,
        dropped=dropped,
        kept_components=len(kept),
        dropped_refs=dropped_refs,
    )


def _has_maven_scopes(components: list[Any]) -> bool:
    """hasScopes guard — at least one maven component tagged ``required``."""
    for component in components:
        if not isinstance(component, dict):
            continue
        purl = component.get("purl")
        if (
            isinstance(purl, str)
            and purl.startswith("pkg:maven/")
            and component.get("scope") == "required"
        ):
            return True
    return False


def _has_dev_entries(npm_lock: NpmLockfileData | None) -> bool:
    """hasDev guard — the lockfile parsed AND classified something ``dev``."""
    if npm_lock is None:
        return False
    return any(scope == "dev" for scope in npm_lock.scope_by_purl.values())


def _kept_refs(sbom: dict[str, Any], kept: list[Any]) -> set[str]:
    """Refs of every kept component, plus the document root ref (always kept)."""
    refs: set[str] = set()
    for component in kept:
        if not isinstance(component, dict):
            continue
        ref = component.get("bom-ref") or component.get("purl")
        if isinstance(ref, str) and ref:
            refs.add(ref)
    metadata = sbom.get("metadata")
    if isinstance(metadata, dict):
        root = metadata.get("component")
        if isinstance(root, dict):
            root_ref = root.get("bom-ref") or root.get("purl")
            if isinstance(root_ref, str) and root_ref:
                refs.add(root_ref)
    return refs


def _prune_dependencies(sbom: dict[str, Any], kept_refs: set[str]) -> None:
    """Drop graph entries for removed components; filter each ``dependsOn``."""
    dependencies = sbom.get("dependencies")
    if not isinstance(dependencies, list):
        return
    pruned: list[Any] = []
    for entry in dependencies:
        if not isinstance(entry, dict):
            pruned.append(entry)
            continue
        ref = entry.get("ref")
        if isinstance(ref, str) and ref not in kept_refs:
            continue
        depends_on = entry.get("dependsOn")
        if isinstance(depends_on, list):
            entry["dependsOn"] = [
                child
                for child in depends_on
                if isinstance(child, str) and child in kept_refs
            ]
        pruned.append(entry)
    sbom["dependencies"] = pruned


def _stamp_filter_property(sbom: dict[str, Any], dropped: dict[str, int]) -> None:
    """Idempotently stamp ``trusca:scope_filter`` into ``metadata.properties``."""
    metadata = sbom.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        return
    properties = metadata.setdefault("properties", [])
    if not isinstance(properties, list):
        return
    properties[:] = [
        prop
        for prop in properties
        if not (isinstance(prop, dict) and prop.get("name") == FILTER_PROPERTY_NAME)
    ]
    value = ",".join(f"{eco}={count}" for eco, count in sorted(dropped.items()))
    properties.append({"name": FILTER_PROPERTY_NAME, "value": value})


__all__ = [
    "FILTER_PROPERTY_NAME",
    "ScopeFilterResult",
    "filter_sbom_to_runtime_scope",
    "rewrite_sbom_file",
]
