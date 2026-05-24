"""
SBOM export service — Phase 3 (Step 4).

Builds an SBOM (CycloneDX or SPDX, JSON or XML / Tag-Value) for a project's
*latest succeeded scan*. The router (`api/v1/sbom.py`) is a thin HTTP adapter
that wires up auth + IDOR + Content-Disposition; serialization decisions live
here so the same code can be re-used by background export jobs (Excel/PDF
report attachments, scheduled deliveries) without booting FastAPI.

Output formats
--------------
- ``cyclonedx-json`` — CycloneDX 1.5 JSON  (Content-Type ``application/json``)
- ``cyclonedx-xml``  — CycloneDX 1.5 XML   (Content-Type ``application/xml``)
- ``spdx-json``      — SPDX 2.3 JSON       (Content-Type ``application/json``)
- ``spdx-tv``        — SPDX 2.3 Tag-Value  (Content-Type ``text/plain``)

Each export is fully self-contained: we do not stream from disk, do not depend
on the scan_artifacts side-channel, and do not require Dependency-Track.
Components come from ``ScanComponent`` ⨝ ``ComponentVersion`` ⨝ ``Component``
of the project's latest *succeeded* scan, ordered by purl (falling back to
bom-ref / name) for a stable byte-for-byte output (so callers may content-hash
the body).

Byte-stability (BUG-006)
------------------------
The user guide (``docs-site/docs/user-guide/sbom.md``) promises: re-exporting
the same scan yields identical bytes across all four formats. That requires
*every* field that could vary between two calls to be derived from persisted,
scan-bound state — never from wall-clock time or a fresh ``uuid4()``:

- ``serialNumber`` / ``documentNamespace`` derive deterministically from the
  scan id via a UUIDv5 in a fixed namespace (:data:`_SBOM_UUID_NAMESPACE`).
  Two exports of the same scan share the same serial number; a project with no
  succeeded scan derives its serial from the *project* id instead (still
  stable across re-exports).
- ``metadata.timestamp`` / SPDX ``Created`` use the scan's persisted
  completion time (``completed_at`` → ``updated_at`` → ``created_at``), never
  ``datetime.now()``. A project with no scan uses the Unix epoch sentinel so
  the document is still well-formed and stable.
- components / packages are emitted in purl-lexical order (bom-ref / name as
  tiebreak), matching the guide's "purl lexical sort" guarantee.

Empty-project policy
--------------------
A project that has no succeeded scan still gets a valid SBOM document with an
empty ``components`` / ``packages`` list. Failing here would force the UI to
hide the export button until the first scan finishes; preferring an empty but
well-formed document is cheaper for everyone.

XML escaping
------------
The CycloneDX-XML serializer uses ``xml.etree.ElementTree`` so attribute /
text content is safely escaped (``<`` / ``>`` / ``&`` / quotes). We never
``+`` strings into the XML body. SPDX Tag-Value has no escape mechanism; the
SPDX spec sidesteps that by restricting the value set (no newlines in tags),
which we mirror by replacing CR/LF with spaces in any free-form text.
"""

from __future__ import annotations

import json
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Component, ComponentVersion, Project, Scan, ScanComponent

log = structlog.get_logger("sbom_export.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class SBOMExportError(Exception):
    """Base — each subclass carries an HTTP status used by the router."""

    status_code: int = 400
    title: str = "SBOM Export Error"


class SBOMUnsupportedFormat(SBOMExportError):
    status_code = 422
    title = "Unsupported SBOM Format"


# ---------------------------------------------------------------------------
# Format catalogue
# ---------------------------------------------------------------------------

# Each format declares (content_type, file_extension). The router uses both.
# We keep a literal-style map (rather than a Literal arg with branching at
# the call site) so adding a new format is a single-line edit.
_FORMAT_CATALOG: dict[str, tuple[str, str]] = {
    "cyclonedx-json": ("application/json", "cdx.json"),
    "cyclonedx-xml": ("application/xml", "cdx.xml"),
    "spdx-json": ("application/json", "spdx.json"),
    "spdx-tv": ("text/plain", "spdx"),
}

SUPPORTED_FORMATS: tuple[str, ...] = tuple(_FORMAT_CATALOG.keys())


# Fixed UUIDv5 namespace for deriving deterministic serialNumber /
# documentNamespace values from a scan (or project) id. This is a constant
# *label* — NOT environment / config — so it is safe at module scope under
# CLAUDE.md rule #11 (no env access at import time). It must never change once
# shipped: changing it would alter every previously-emitted serialNumber and
# break hash-based compliance verification of older SBOMs.
_SBOM_UUID_NAMESPACE = uuid.UUID("6f3f5b1e-2c9a-5e4d-9b7a-0c1d2e3f4a5b")

# Sentinel timestamp for a project that has never produced a succeeded scan.
# Using a fixed epoch keeps the "empty SBOM" document byte-stable across
# re-exports (the alternative — current time — would re-introduce BUG-006).
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


async def _load_project(session: AsyncSession, project_id: uuid.UUID) -> Project | None:
    result = await session.execute(select(Project).where(Project.id == project_id))
    return result.scalar_one_or_none()


async def _load_latest_succeeded_scan(
    session: AsyncSession, *, project_id: uuid.UUID
) -> Scan | None:
    """Return the most recent ``status='succeeded'`` scan for the project, or None."""
    stmt = (
        select(Scan)
        .where(Scan.project_id == project_id)
        .where(Scan.status == "succeeded")
        .order_by(Scan.created_at.desc(), Scan.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _load_scan_components(
    session: AsyncSession, *, scan_id: uuid.UUID
) -> list[dict[str, Any]]:
    """
    Return per-component dictionaries for the given scan.

    Each row is shaped to be format-agnostic so each serializer can pick the
    fields it needs without re-querying.
    """
    stmt = (
        select(
            ScanComponent.id.label("scan_component_id"),
            ComponentVersion.id.label("component_version_id"),
            ComponentVersion.version.label("version"),
            ComponentVersion.purl_with_version.label("purl"),
            Component.id.label("component_id"),
            Component.name.label("name"),
            Component.namespace.label("namespace"),
            Component.package_type.label("package_type"),
            Component.description.label("description"),
        )
        .select_from(ScanComponent)
        .join(ComponentVersion, ComponentVersion.id == ScanComponent.component_version_id)
        .join(Component, Component.id == ComponentVersion.component_id)
        .where(ScanComponent.scan_id == scan_id)
        # Stable byte-for-byte output (BUG-006): the user guide promises a
        # purl-lexical sort, so order by purl first. purl_with_version is
        # unique (DB constraint) so it is already a strict total order; the
        # name → version → cv_id tiebreak below only matters for the
        # (rare) rows with a NULL purl, where it restores a total order.
        .order_by(
            ComponentVersion.purl_with_version.asc(),
            Component.name.asc(),
            ComponentVersion.version.asc(),
            ComponentVersion.id.asc(),
        )
    )
    result = await session.execute(stmt)
    return [dict(row._mapping) for row in result.all()]


# ---------------------------------------------------------------------------
# CycloneDX JSON
# ---------------------------------------------------------------------------


def _utc_iso(now: datetime) -> str:
    """ISO 8601 timestamp with millisecond precision and a Z suffix.

    CycloneDX/SPDX both accept "...Z"; using Z (not +00:00) sidesteps a class
    of validators that only recognise the literal Z form.
    """
    # `isoformat(timespec="milliseconds")` keeps the body compact and stable.
    return now.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _deterministic_timestamp(scan: Scan | None) -> datetime:
    """The persisted scan-completion time used for the SBOM's timestamp.

    BUG-006: the timestamp must derive from scan-bound, persisted state so two
    exports of the same scan are byte-identical. We prefer the most specific
    completion marker available and fall back through ``updated_at`` →
    ``created_at``. A project with no succeeded scan uses the Unix-epoch
    sentinel so the empty document is still well-formed and stable.
    """
    if scan is None:
        return _EPOCH
    return scan.completed_at or scan.updated_at or scan.created_at or _EPOCH


def _deterministic_serial_uuid(project: Project, scan: Scan | None) -> uuid.UUID:
    """A stable UUIDv5 derived from the scan id (or project id when no scan).

    Re-exporting the same scan reproduces the same UUID, so the CycloneDX
    ``serialNumber`` and SPDX ``documentNamespace`` are byte-stable. We anchor
    on the *scan* id (not the project) so two different scans of the same
    project get distinct serial numbers, which is what compliance tooling
    expects when diffing SBOMs over time.
    """
    anchor = str(scan.id) if scan is not None else f"project:{project.id}"
    return uuid.uuid5(_SBOM_UUID_NAMESPACE, anchor)


def _cyclonedx_components(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        comp: dict[str, Any] = {
            # CycloneDX uses bom-ref to disambiguate components within one BOM.
            # The cv_id (UUID) is unique within the export; using it directly
            # makes diffs across two exports of the same scan identical.
            "bom-ref": str(r["component_version_id"]),
            "type": "library",
            "name": r["name"],
            "version": r["version"],
        }
        if r.get("namespace"):
            comp["group"] = r["namespace"]
        if r.get("description"):
            comp["description"] = r["description"]
        if r.get("purl"):
            comp["purl"] = r["purl"]
        out.append(comp)
    return out


def _build_cyclonedx_doc(
    *,
    project: Project,
    scan: Scan | None,
    rows: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Build the CycloneDX 1.5 dict (used both for the JSON and XML serializers)."""
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        # urn:uuid: is the CycloneDX-prescribed serialNumber form. BUG-006:
        # the UUID derives deterministically from the scan id so re-exporting
        # the same scan is byte-identical — never a fresh uuid4().
        "serialNumber": f"urn:uuid:{_deterministic_serial_uuid(project, scan)}",
        "version": 1,
        "metadata": {
            "timestamp": _utc_iso(now),
            "tools": [
                {
                    "vendor": "TrustedOSS",
                    "name": "TrustedOSS Portal",
                    "version": "0.0.1",
                }
            ],
            "component": {
                # The scanned project itself, as a CycloneDX "application".
                "bom-ref": f"project:{project.id}",
                "type": "application",
                "name": project.name,
                "version": str(scan.id) if scan is not None else "no-scan",
            },
        },
        "components": _cyclonedx_components(rows),
    }


def _serialize_cyclonedx_json(doc: dict[str, Any]) -> str:
    # Stdlib `json` keeps the byte ordering deterministic — we sort no keys
    # because CycloneDX has a documented field order convention; the dict we
    # build above is already in that order. ``indent=2`` keeps the output
    # human-readable; SBOM bodies stay small (< 1 MB for typical projects).
    return json.dumps(doc, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CycloneDX XML
# ---------------------------------------------------------------------------


_CDX_NS = "http://cyclonedx.org/schema/bom/1.5"


def _serialize_cyclonedx_xml(doc: dict[str, Any]) -> str:
    """
    Render the CycloneDX 1.5 dict as XML using ElementTree.

    We deliberately do not depend on ``cyclonedx-python-lib`` so the export
    surface is import-cheap and stable across the lib's major-version bumps.
    The shape we emit here is a strict subset of the schema (the same subset
    most tools care about): metadata + components.
    """
    # Use the namespace as the default XML namespace via ET's prefix mapping.
    ET.register_namespace("", _CDX_NS)

    bom = ET.Element(
        f"{{{_CDX_NS}}}bom",
        attrib={
            "version": str(doc["version"]),
            "serialNumber": doc["serialNumber"],
        },
    )

    metadata = ET.SubElement(bom, f"{{{_CDX_NS}}}metadata")
    ts = ET.SubElement(metadata, f"{{{_CDX_NS}}}timestamp")
    ts.text = doc["metadata"]["timestamp"]
    tools = ET.SubElement(metadata, f"{{{_CDX_NS}}}tools")
    for t in doc["metadata"]["tools"]:
        tool = ET.SubElement(tools, f"{{{_CDX_NS}}}tool")
        ET.SubElement(tool, f"{{{_CDX_NS}}}vendor").text = t["vendor"]
        ET.SubElement(tool, f"{{{_CDX_NS}}}name").text = t["name"]
        ET.SubElement(tool, f"{{{_CDX_NS}}}version").text = t["version"]
    project_component = doc["metadata"]["component"]
    pc = ET.SubElement(
        metadata,
        f"{{{_CDX_NS}}}component",
        attrib={"type": project_component["type"], "bom-ref": project_component["bom-ref"]},
    )
    ET.SubElement(pc, f"{{{_CDX_NS}}}name").text = project_component["name"]
    ET.SubElement(pc, f"{{{_CDX_NS}}}version").text = project_component["version"]

    components_el = ET.SubElement(bom, f"{{{_CDX_NS}}}components")
    for comp in doc["components"]:
        c = ET.SubElement(
            components_el,
            f"{{{_CDX_NS}}}component",
            attrib={"type": comp["type"], "bom-ref": comp["bom-ref"]},
        )
        if "group" in comp:
            ET.SubElement(c, f"{{{_CDX_NS}}}group").text = comp["group"]
        ET.SubElement(c, f"{{{_CDX_NS}}}name").text = comp["name"]
        ET.SubElement(c, f"{{{_CDX_NS}}}version").text = comp["version"]
        if "description" in comp:
            ET.SubElement(c, f"{{{_CDX_NS}}}description").text = comp["description"]
        if "purl" in comp:
            ET.SubElement(c, f"{{{_CDX_NS}}}purl").text = comp["purl"]

    ET.indent(bom, space="  ")
    body = ET.tostring(bom, encoding="unicode", xml_declaration=False)
    # ET.tostring does not emit the XML prolog when xml_declaration=False is
    # honoured by the underlying writer — supply our own to keep the body
    # format stable across CPython point releases.
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'


# ---------------------------------------------------------------------------
# SPDX 2.3 JSON
# ---------------------------------------------------------------------------


def _spdx_id_for_component(cv_id: uuid.UUID) -> str:
    """SPDXRef-* identifier. The spec requires [A-Za-z0-9.\\-]+."""
    return f"SPDXRef-Pkg-{cv_id.hex}"


def _spdx_doc_namespace(project: Project, scan: Scan | None) -> str:
    """
    SPDX requires a unique documentNamespace per *document*. BUG-006: it must
    also be byte-stable across re-exports of the same scan, so we derive it
    deterministically from a UUIDv5 (scan id, or project id when the project
    has no succeeded scan) instead of a fresh uuid4. Two exports of the same
    scan therefore share a namespace; two different scans get distinct ones.
    """
    base = "https://trustedoss.io/spdx"
    return f"{base}/{project.id}/{_deterministic_serial_uuid(project, scan)}"


def _spdx_clean(value: str) -> str:
    """Strip CR/LF — SPDX tag values are line-oriented."""
    return value.replace("\r", " ").replace("\n", " ").strip()


def _spdx_packages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        spdx_id = _spdx_id_for_component(r["component_version_id"])
        pkg: dict[str, Any] = {
            "SPDXID": spdx_id,
            "name": r["name"],
            "versionInfo": r["version"],
            # SPDX requires downloadLocation; we don't carry one, so use the
            # SPDX-reserved sentinel for "we know it exists but don't have a
            # location for it". (NOASSERTION = caller should assert.)
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
        }
        if r.get("description"):
            pkg["description"] = _spdx_clean(r["description"])
        if r.get("purl"):
            pkg["externalRefs"] = [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": r["purl"],
                }
            ]
        out.append(pkg)
    return out


def _build_spdx_doc(
    *,
    project: Project,
    scan: Scan | None,
    rows: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{project.name} SBOM",
        "documentNamespace": _spdx_doc_namespace(project, scan),
        "creationInfo": {
            "created": _utc_iso(now),
            "creators": ["Tool: TrustedOSS Portal-0.0.1", "Organization: TrustedOSS"],
        },
        "packages": _spdx_packages(rows),
    }


def _serialize_spdx_json(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# SPDX 2.3 Tag-Value
# ---------------------------------------------------------------------------


def _serialize_spdx_tv(doc: dict[str, Any]) -> str:
    """
    Render the SPDX 2.3 Tag-Value form.

    The SPDX tag-value grammar is line-oriented: each tag is on its own line,
    multi-line free text is wrapped in ``<text>...</text>`` blocks. We do not
    use the multi-line block here because we always cleaned newlines out of
    free-form fields in `_spdx_clean`.
    """
    lines: list[str] = []
    # Document-level header ----------------------------------------------------
    lines.append(f"SPDXVersion: {doc['spdxVersion']}")
    lines.append(f"DataLicense: {doc['dataLicense']}")
    lines.append(f"SPDXID: {doc['SPDXID']}")
    lines.append(f"DocumentName: {_spdx_clean(doc['name'])}")
    lines.append(f"DocumentNamespace: {doc['documentNamespace']}")
    lines.append(f"Created: {doc['creationInfo']['created']}")
    for creator in doc["creationInfo"]["creators"]:
        lines.append(f"Creator: {creator}")

    # One blank line between sections is the SPDX convention.
    for pkg in doc.get("packages", []):
        lines.append("")
        lines.append(f"PackageName: {_spdx_clean(pkg['name'])}")
        lines.append(f"SPDXID: {pkg['SPDXID']}")
        lines.append(f"PackageVersion: {_spdx_clean(pkg['versionInfo'])}")
        lines.append(f"PackageDownloadLocation: {pkg['downloadLocation']}")
        lines.append(f"FilesAnalyzed: {'true' if pkg['filesAnalyzed'] else 'false'}")
        lines.append(f"PackageLicenseConcluded: {pkg['licenseConcluded']}")
        lines.append(f"PackageLicenseDeclared: {pkg['licenseDeclared']}")
        lines.append(f"PackageCopyrightText: {pkg['copyrightText']}")
        if "description" in pkg:
            lines.append(f"PackageDescription: {pkg['description']}")
        for ref in pkg.get("externalRefs", []):
            lines.append(
                "ExternalRef: "
                f"{ref['referenceCategory']} {ref['referenceType']} {ref['referenceLocator']}"
            )

    # Trailing newline keeps `cat` / `wc -l` output sane.
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _filename(project: Project, fmt: str) -> str:
    """Operator-friendly filename: ``sbom-<project-slug>.<ext>``."""
    _, ext = _FORMAT_CATALOG[fmt]
    # Slug is already validated [a-z0-9-]+ at create time, so no further
    # escaping is required.
    return f"sbom-{project.slug}.{ext}"


async def export_sbom(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    fmt: str,
    now: datetime | None = None,
) -> tuple[str, str, str]:
    """
    Build the SBOM body for ``project_id`` in the requested format.

    Returns ``(content, content_type, filename)``.

    Raises :class:`SBOMUnsupportedFormat` (422) for an unknown format. The
    router is responsible for translating the missing-project / forbidden
    cases to RFC 7807 — those checks fire BEFORE this function runs.

    Empty-project policy (see module docstring): if the project has no
    succeeded scan we still return a valid SBOM document with an empty
    components/packages list, not 404.

    Byte-stability (BUG-006): by default the document timestamp derives from
    the scan's persisted completion time, so two exports of the same scan are
    byte-identical. ``now`` is an explicit override retained only for callers
    that must stamp a specific time (and accept the non-determinism that
    implies); production / HTTP callers pass nothing.
    """
    if fmt not in _FORMAT_CATALOG:
        raise SBOMUnsupportedFormat(
            f"unknown SBOM format {fmt!r}; supported: {sorted(SUPPORTED_FORMATS)}",
        )

    project = await _load_project(session, project_id)
    if project is None:
        # Surface as the same 422 we'd use for an unknown format. The router
        # checks IDOR + existence BEFORE calling us, so this branch is only
        # reachable from internal callers (e.g. background exports) — having
        # it here keeps the contract self-consistent.
        raise SBOMUnsupportedFormat(f"project {project_id} not found")

    scan = await _load_latest_succeeded_scan(session, project_id=project_id)
    rows: list[dict[str, Any]] = []
    if scan is not None:
        rows = await _load_scan_components(session, scan_id=scan.id)

    # BUG-006: default to the scan's persisted completion time so re-exports
    # are byte-stable. `now` stays an explicit override for callers that need
    # to pin a specific stamp.
    timestamp = now if now is not None else _deterministic_timestamp(scan)

    content_type, _ = _FORMAT_CATALOG[fmt]
    filename = _filename(project, fmt)

    if fmt == "cyclonedx-json":
        body = _serialize_cyclonedx_json(
            _build_cyclonedx_doc(project=project, scan=scan, rows=rows, now=timestamp)
        )
    elif fmt == "cyclonedx-xml":
        body = _serialize_cyclonedx_xml(
            _build_cyclonedx_doc(project=project, scan=scan, rows=rows, now=timestamp)
        )
    elif fmt == "spdx-json":
        body = _serialize_spdx_json(
            _build_spdx_doc(project=project, scan=scan, rows=rows, now=timestamp)
        )
    elif fmt == "spdx-tv":
        body = _serialize_spdx_tv(
            _build_spdx_doc(project=project, scan=scan, rows=rows, now=timestamp)
        )
    else:  # pragma: no cover - guarded by the catalog check above
        raise SBOMUnsupportedFormat(f"unknown SBOM format {fmt!r}")

    log.info(
        "sbom_exported",
        project_id=str(project_id),
        scan_id=str(scan.id) if scan is not None else None,
        format=fmt,
        components=len(rows),
        bytes=len(body.encode("utf-8")),
    )
    return body, content_type, filename


__all__ = [
    "SBOMExportError",
    "SBOMUnsupportedFormat",
    "SUPPORTED_FORMATS",
    "export_sbom",
]
