"""
Normalise an uploaded SBOM to a CycloneDX-shaped dict for component / dependency
persistence — model 3 (supplier-submitted SBOM ingest).

The ingest pipeline (``tasks/scan_sbom_ingest``) needs two things from an
uploaded SBOM:

  1. CVE matching — handled by Trivy, which reads CycloneDX **and** SPDX
     directly, so the *original* file is passed straight to ``run_trivy_sbom``.
     No conversion happens for matching (avoids a lossy SPDX→CDX round-trip).
  2. Component / version / dependency-edge persistence — done by
     ``tasks/scan_source.persist_sbom_components``, which only understands a
     CycloneDX dict (``components[]`` + ``dependencies[]``). THAT is what this
     module produces.

We deliberately do NOT depend on ``spdx-tools`` (it pulls rdflib / beartype /
ply and still cannot emit CycloneDX). For the two SPDX serialisations that
matter in practice — JSON and Tag-Value — the mapping is a short, dependency-free
dict / line walk, exactly the scope the BomLens ``convert-to-cdx.sh`` jq covered.
SPDX RDF / XML are unsupported (callers should reject at upload with 415).

The emitted ``licenses`` use the shape ``persist_sbom_components`` →
``_extract_spdx_ids`` reads: ``[{"license": {"id": "<spdx>"}}]`` for a single
SPDX id, or ``[{"expression": "<spdx-expression>"}]`` for a compound expression.
``bom-ref`` is set to the SPDX ``SPDXID`` so ``DEPENDS_ON`` edges resolve against
the persisted components (the dependency-graph builder keys on bom-ref + purl).
"""

from __future__ import annotations

import re
from typing import Any

from services.sbom_conformance import (
    FORMAT_CYCLONEDX,
    FORMAT_SPDX_JSON,
    FORMAT_SPDX_TV,
    detect_format,
)


class SbomConvertError(Exception):
    """Base for conversion failures."""


class UnsupportedSbomFormat(SbomConvertError):
    """The upload is not CycloneDX-JSON or SPDX (JSON / Tag-Value)."""


# An SPDX license field carries a real license only when it is neither absent
# nor an explicit non-assertion.
_NO_LICENSE = {"", "NOASSERTION", "NONE"}
# Compound SPDX expression markers — if present we emit a CycloneDX
# ``expression`` entry instead of an ``id`` entry.
_EXPR_TOKENS = re.compile(r"\b(?:OR|AND|WITH)\b|[()]")


def _license_entries(*candidates: str | None) -> list[dict[str, Any]]:
    """Build a CycloneDX ``licenses`` array from SPDX license fields.

    Prefers the first non-empty / non-NOASSERTION candidate (concluded over
    declared). A compound expression becomes ``{"expression": ...}``; a bare id
    becomes ``{"license": {"id": ...}}``.
    """
    for cand in candidates:
        if not isinstance(cand, str):
            continue
        value = cand.strip()
        if value.upper() in _NO_LICENSE:
            continue
        if _EXPR_TOKENS.search(value):
            return [{"expression": value}]
        return [{"license": {"id": value}}]
    return []


# ---------------------------------------------------------------------------
# SPDX-JSON → CycloneDX dict.
# ---------------------------------------------------------------------------
def _spdx_json_to_cdx(doc: dict[str, Any]) -> dict[str, Any]:
    packages = [p for p in (doc.get("packages") or []) if isinstance(p, dict)]
    components: list[dict[str, Any]] = []

    for pkg in packages:
        name = pkg.get("name")
        if not isinstance(name, str) or not name:
            continue
        version = pkg.get("versionInfo") or ""
        purl = None
        for ref in pkg.get("externalRefs") or []:
            if isinstance(ref, dict) and ref.get("referenceType") == "purl":
                loc = ref.get("referenceLocator")
                if isinstance(loc, str) and loc:
                    purl = loc
                    break
        component: dict[str, Any] = {
            "type": "library",
            "name": name,
            "version": version,
        }
        spdx_id = pkg.get("SPDXID")
        if isinstance(spdx_id, str) and spdx_id:
            component["bom-ref"] = spdx_id
        if purl:
            component["purl"] = purl
        licenses = _license_entries(
            pkg.get("licenseConcluded"), pkg.get("licenseDeclared")
        )
        if licenses:
            component["licenses"] = licenses
        checksums = pkg.get("checksums")
        if isinstance(checksums, list) and checksums:
            hashes = [
                {"alg": c.get("algorithm"), "content": c.get("checksumValue")}
                for c in checksums
                if isinstance(c, dict) and c.get("checksumValue")
            ]
            if hashes:
                component["hashes"] = hashes
        components.append(component)

    dependencies = _spdx_relationships_to_dependencies(
        doc.get("relationships") or []
    )

    metadata: dict[str, Any] = {}
    creation = doc.get("creationInfo") or {}
    if isinstance(creation.get("created"), str):
        metadata["timestamp"] = creation["created"]
    if isinstance(doc.get("name"), str) and doc["name"]:
        metadata["component"] = {"type": "application", "name": doc["name"]}

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": metadata,
        "components": components,
        "dependencies": dependencies,
    }


def _spdx_relationships_to_dependencies(
    relationships: list[Any],
) -> list[dict[str, Any]]:
    """Collapse SPDX ``DEPENDS_ON`` relationships into CycloneDX
    ``[{"ref": parent, "dependsOn": [child, ...]}]`` keyed on SPDXID."""
    adjacency: dict[str, list[str]] = {}
    for rel in relationships:
        if not isinstance(rel, dict):
            continue
        if rel.get("relationshipType") != "DEPENDS_ON":
            continue
        parent = rel.get("spdxElementId")
        child = rel.get("relatedSpdxElement")
        if not isinstance(parent, str) or not isinstance(child, str):
            continue
        adjacency.setdefault(parent, [])
        if child not in adjacency[parent]:
            adjacency[parent].append(child)
    return [{"ref": ref, "dependsOn": children} for ref, children in adjacency.items()]


# ---------------------------------------------------------------------------
# SPDX Tag-Value → CycloneDX dict.
# ---------------------------------------------------------------------------
def _spdx_tv_to_cdx(text: str) -> dict[str, Any]:
    components: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}

    current: dict[str, Any] | None = None

    def _flush(pkg: dict[str, Any] | None) -> None:
        if pkg is None:
            return
        name = pkg.get("name")
        if not name:
            return
        component: dict[str, Any] = {
            "type": "library",
            "name": name,
            "version": pkg.get("version") or "",
        }
        if pkg.get("spdx_id"):
            component["bom-ref"] = pkg["spdx_id"]
        if pkg.get("purl"):
            component["purl"] = pkg["purl"]
        licenses = _license_entries(pkg.get("license_concluded"), pkg.get("license_declared"))
        if licenses:
            component["licenses"] = licenses
        if pkg.get("checksum"):
            component["hashes"] = [{"content": pkg["checksum"]}]
        components.append(component)

    for line in text.splitlines():
        tag, _, value = line.partition(":")
        tag = tag.strip()
        value = value.strip()
        if tag == "Created" and "timestamp" not in metadata:
            metadata["timestamp"] = value
        elif tag == "DocumentName" and "component" not in metadata:
            metadata["component"] = {"type": "application", "name": value}
        elif tag == "PackageName":
            _flush(current)
            current = {"name": value}
        elif current is not None and tag == "SPDXID":
            current["spdx_id"] = value
        elif current is not None and tag == "PackageVersion":
            current["version"] = value
        elif current is not None and tag == "PackageLicenseConcluded":
            current["license_concluded"] = value
        elif current is not None and tag == "PackageLicenseDeclared":
            current["license_declared"] = value
        elif current is not None and tag == "PackageChecksum":
            # value e.g. "SHA1: d6a770ba38..."
            current.setdefault("checksum", value.partition(":")[2].strip() or value)
        elif current is not None and tag == "ExternalRef":
            # "PACKAGE-MANAGER purl pkg:npm/foo@1.0.0"
            m = re.match(r"(?:PACKAGE-MANAGER\s+)?purl\s+(\S+)", value)
            if m:
                current.setdefault("purl", m.group(1))
        elif tag == "Relationship":
            parts = value.split()
            if len(parts) == 3 and parts[1] == "DEPENDS_ON":
                relationships.append(
                    {
                        "relationshipType": "DEPENDS_ON",
                        "spdxElementId": parts[0],
                        "relatedSpdxElement": parts[2],
                    }
                )
    _flush(current)

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": metadata,
        "components": components,
        "dependencies": _spdx_relationships_to_dependencies(relationships),
    }


# ---------------------------------------------------------------------------
# Public entrypoint.
# ---------------------------------------------------------------------------
def to_cyclonedx(raw: bytes) -> dict[str, Any]:
    """Return a CycloneDX-shaped dict for ``persist_sbom_components``.

    CycloneDX-JSON passes through unchanged; SPDX (JSON / Tag-Value) is mapped.
    Raises :class:`UnsupportedSbomFormat` for anything else (RDF / XML / junk),
    so the ingest task can mark the scan failed cleanly.
    """
    fmt, doc = detect_format(raw)
    if fmt == FORMAT_CYCLONEDX and doc is not None:
        return doc
    if fmt == FORMAT_SPDX_JSON and doc is not None:
        return _spdx_json_to_cdx(doc)
    if fmt == FORMAT_SPDX_TV:
        return _spdx_tv_to_cdx(raw.decode("utf-8", errors="replace"))
    raise UnsupportedSbomFormat(
        "uploaded file is not CycloneDX-JSON or SPDX (JSON / Tag-Value)"
    )
