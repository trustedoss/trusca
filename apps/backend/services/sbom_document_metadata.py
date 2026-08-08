# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""What an SBOM says about itself — shared by both documents TRUSCA produces.

TRUSCA emits two CycloneDX documents by two different routes, and they are
easy to mistake for one:

  * the SCAN document — what cdxgen wrote, filtered to the runtime scope, then
    persisted, signed with cosign and offered for download. This is the SBOM a
    consumer receives and verifies.
  * the EXPORT document — rebuilt from the database on request
    (``services/sbom_export``), covering a project's latest succeeded scan.

The 2026 SBOM minimum elements ask a document to state at which lifecycle phase
it was generated, who generated it, with which tool at which version, and
whether an empty field is unknown or withheld. Answering that in one route and
not the other means the SBOM we actually sign and publish is the one that stays
silent — which is what happened when the export route was done first.

So the statements live here and both routes apply them. Everything in this
module is a pure function over a dict; no DB, no network, no I/O.
"""

from __future__ import annotations

from typing import Any

from core.config import sbom_author, slsa_builder_version

#: Document property naming the fields this document leaves empty and why. The
#: guidance asks an author to tell an absent value the author could not
#: establish apart from one the author is withholding. A scan only ever
#: produces the first kind — it writes what it managed to read and has nothing
#: held back — so the statement is made ONCE for the document rather than on
#: every empty field: the claim is identical for all of them, and on a large
#: SBOM the per-field form would add thousands of properties saying it again.
UNDECLARED_FIELDS_PROPERTY = "trusca:undeclared-fields"
UNDECLARED_FIELDS_VALUE = (
    "unknown to the SBOM author: any field left empty in this document is one "
    "this scan could not establish. Nothing is withheld."
)

#: Marks a component whose version the scan could not establish. Read by
#: services/cisa_conformance.py, which matches the suffix so a document written
#: by the sibling upstream tool (``bomlens:evidenceGrade``) is read the same way.
EVIDENCE_GRADE_PROPERTY = "trusca:evidenceGrade"

#: Lifecycle phase per scan kind, using the distinction the minimum elements
#: draw themselves. A scan of source manifests describes software that is not
#: built yet; a scan of a built image describes one that is. An ingested
#: supplier document is deliberately absent — re-exporting it converts someone
#: else's document, and stamping our phase onto the conversion would claim we
#: generated data we only reformatted.
_LIFECYCLE_PHASE_BY_SCAN_KIND = {
    "source": "pre-build",
    "container": "post-build",
}

#: What a tool entry says when it names no version. The guidance asks for this
#: rather than an empty field: "unknown" tells a reader the question was asked.
UNKNOWN_VERSION = "unknown"

_TOOL_VENDOR = "TrustedOSS"
_TOOL_NAME = "TRUSCA"


def tool_version() -> str:
    """The product version recorded as the SBOM tool version.

    Single source: ``core.config.slsa_builder_version`` already fills this role
    for SLSA provenance and the About surface. A second source here would mean
    two answers to one question.
    """
    return slsa_builder_version() or UNKNOWN_VERSION


def lifecycle_phase(scan_kind: str | None) -> str | None:
    """The phase for a scan of this kind, or None when we must not claim one."""
    return _LIFECYCLE_PHASE_BY_SCAN_KIND.get(scan_kind or "")


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _tool_entries(metadata: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """Existing tool entries and whether they came from the 1.5+ object form.

    CycloneDX carries tools either as a bare array (pre-1.5) or as an object
    with ``components`` / ``services``. Both are read; the shape is preserved
    on write, because rewriting a generator's document into the other form is a
    change we have no reason to make.
    """
    tools = metadata.get("tools")
    if isinstance(tools, dict):
        entries = [t for t in _as_list(tools.get("components")) if isinstance(t, dict)]
        return entries, True
    return [t for t in _as_list(tools) if isinstance(t, dict)], False


def stamp_document_metadata(
    doc: dict[str, Any], *, scan_kind: str | None, claim_authorship: bool = True
) -> dict[str, Any]:
    """Stamp the self-describing fields onto ``doc`` in place, and return it.

    ``claim_authorship=False`` records the tool and the unknowns statement but
    neither the phase nor the author — for a document TRUSCA amended rather
    than generated, where naming ourselves as its author would overstate what
    we did.

    Idempotent: stamping twice does not duplicate the tool entry or the
    property, so a re-run of a scan produces the same bytes.
    """
    metadata = _as_dict(doc.get("metadata"))
    doc["metadata"] = metadata

    # --- Tools -------------------------------------------------------------
    entries, object_form = _tool_entries(metadata)
    if not any(
        t.get("name") == _TOOL_NAME and t.get("vendor") == _TOOL_VENDOR
        for t in entries
    ):
        entries.append(
            {"vendor": _TOOL_VENDOR, "name": _TOOL_NAME, "version": tool_version()}
        )
    # A tool whose version the generator did not record says so, rather than
    # leaving the field empty — the element asks which version, and "unknown"
    # answers it where a blank does not.
    for entry in entries:
        if not isinstance(entry.get("version"), str) or not entry["version"]:
            entry["version"] = UNKNOWN_VERSION
    if object_form:
        _as_dict(metadata.get("tools"))["components"] = entries
    else:
        metadata["tools"] = entries

    # --- Generation context and author ------------------------------------
    if claim_authorship:
        phase = lifecycle_phase(scan_kind)
        if phase and not _as_list(metadata.get("lifecycles")):
            metadata["lifecycles"] = [{"phase": phase}]

        author = sbom_author()
        if author and not _as_list(metadata.get("authors")):
            metadata["authors"] = [{"name": author}]

    # --- Undeclared fields -------------------------------------------------
    properties = [p for p in _as_list(metadata.get("properties")) if isinstance(p, dict)]
    if not any(p.get("name") == UNDECLARED_FIELDS_PROPERTY for p in properties):
        properties.append(
            {"name": UNDECLARED_FIELDS_PROPERTY, "value": UNDECLARED_FIELDS_VALUE}
        )
    metadata["properties"] = properties

    return doc
