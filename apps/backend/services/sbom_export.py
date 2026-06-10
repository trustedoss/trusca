"""
SBOM export service — Phase 3 (Step 4).

Builds an SBOM (CycloneDX or SPDX, JSON or XML / Tag-Value) for a project's
*latest succeeded scan*. The router (`api/v1/sbom.py`) is a thin HTTP adapter
that wires up auth + IDOR + Content-Disposition; serialization decisions live
here so the same code can be re-used by background export jobs (Excel/PDF
report attachments, scheduled deliveries) without booting FastAPI.

Output formats
--------------
- ``cyclonedx-json`` — CycloneDX 1.6 JSON  (Content-Type ``application/vnd.cyclonedx+json``)
- ``cyclonedx-xml``  — CycloneDX 1.6 XML   (Content-Type ``application/vnd.cyclonedx+xml``)
- ``spdx-json``      — SPDX 2.3 JSON       (Content-Type ``application/spdx+json``)
- ``spdx-tv``        — SPDX 2.3 Tag-Value  (Content-Type ``text/spdx``)

The format-specific media types (M-24) let downstream tooling branch on
``Content-Type`` instead of sniffing the body; they are the IANA-registered
(CycloneDX, SPDX-JSON) / community-standard (``text/spdx``) types the user
guide documents.

VEX in CycloneDX (H-4)
----------------------
CycloneDX exports embed the scan's vulnerability findings as a top-level
``vulnerabilities[]`` array so the SBOM alone carries the project's VEX
triage. Each entry maps the internal finding status onto CycloneDX's closed
``analysis.state`` vocabulary via
:data:`services.vex_export.CYCLONEDX_STATE_MAP` (single source of truth,
shared with the standalone VEX export). The free-text analyst note
(``analysis_justification``) goes to ``analysis.detail`` — never the CycloneDX
``analysis.justification`` enum, whose members have precise meaning we cannot
infer from arbitrary prose. ``affects[].ref`` points at the affected
component's ``bom-ref`` within THIS document, so consumers can join findings
to components without parsing purls. SPDX has no native VEX representation;
SPDX exports stay component-only (pair them with the standalone VEX export).

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

from models import (
    Component,
    ComponentVersion,
    License,
    LicenseFinding,
    Project,
    Scan,
    ScanComponent,
    Vulnerability,
    VulnerabilityFinding,
)
from services.scan_resolution import resolve_snapshot_scan_id
from services.vex_export import CYCLONEDX_STATE_MAP

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
    "cyclonedx-json": ("application/vnd.cyclonedx+json", "cdx.json"),
    "cyclonedx-xml": ("application/vnd.cyclonedx+xml", "cdx.xml"),
    "spdx-json": ("application/spdx+json", "spdx.json"),
    "spdx-tv": ("text/spdx", "spdx"),
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


async def _load_scan_by_id(session: AsyncSession, *, scan_id: uuid.UUID) -> Scan | None:
    """Load one scan row by id (already validated by the snapshot resolver)."""
    result = await session.execute(select(Scan).where(Scan.id == scan_id))
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


# A license observation, normalized for serialization. ``spdx_id`` is the SPDX
# short identifier (e.g. "MIT"); it is ``None`` for ORT custom licenses
# (``LicenseRef-*``) that have no SPDX id, in which case ``name`` carries the
# human-readable label.
LicenseEntry = dict[str, str | None]
# Per-component licenses grouped by finding ``kind`` (declared / concluded /
# detected). Each list is sorted + de-duplicated for byte-stable output.
ComponentLicenses = dict[str, list[LicenseEntry]]

# When CycloneDX (which has a single per-component ``licenses`` array, not a
# declared/concluded split) picks ONE kind to surface, prefer the most
# authoritative: the scanner's final verdict, then package metadata, then raw
# detector output.
_LICENSE_KIND_PRIORITY = ("concluded", "declared", "detected")


async def _load_scan_licenses(
    session: AsyncSession, *, scan_id: uuid.UUID
) -> dict[uuid.UUID, ComponentLicenses]:
    """
    Return ``{component_version_id: {kind: [LicenseEntry, ...]}}`` for the scan.

    The SBOM export historically emitted ``NOASSERTION`` for every license even
    though scans persist real ``LicenseFinding`` rows. This loader joins those
    findings so the serializers can populate CycloneDX ``licenses`` and SPDX
    ``licenseDeclared`` / ``licenseConcluded``.

    Byte-stability (BUG-006): the same (spdx_id, name) pair can be reported from
    several files (LICENSE, README, package.json) — we de-duplicate per kind and
    sort by ``(spdx_id or "", name)`` so two exports of one scan are identical.
    """
    stmt = (
        select(
            LicenseFinding.component_version_id.label("component_version_id"),
            LicenseFinding.kind.label("kind"),
            License.spdx_id.label("spdx_id"),
            License.name.label("name"),
        )
        .select_from(LicenseFinding)
        .join(License, License.id == LicenseFinding.license_id)
        .where(LicenseFinding.scan_id == scan_id)
    )
    result = await session.execute(stmt)

    # cv_id -> kind -> set of (spdx_id, name) to de-dup before sorting.
    grouped: dict[uuid.UUID, dict[str, set[tuple[str | None, str]]]] = {}
    for row in result.all():
        m = row._mapping
        cv_id = m["component_version_id"]
        grouped.setdefault(cv_id, {}).setdefault(m["kind"], set()).add(
            (m["spdx_id"], m["name"])
        )

    out: dict[uuid.UUID, ComponentLicenses] = {}
    for cv_id, by_kind in grouped.items():
        out[cv_id] = {
            kind: [
                {"spdx_id": spdx_id, "name": name}
                for spdx_id, name in sorted(pairs, key=lambda p: (p[0] or "", p[1]))
            ]
            for kind, pairs in by_kind.items()
        }
    return out


async def _load_scan_vulnerabilities(
    session: AsyncSession, *, scan_id: uuid.UUID
) -> list[dict[str, Any]]:
    """
    Return per-finding dictionaries for the scan's CycloneDX ``vulnerabilities[]``.

    Mirrors the standalone VEX exporter's loader (``vex_export._load_findings``)
    but additionally carries ``component_version_id`` so ``affects[].ref`` can
    point at the component's ``bom-ref`` within the same document. Deterministic
    order — CVE external id, then purl, then finding id — keeps the export
    byte-stable (BUG-006).
    """
    stmt = (
        select(
            VulnerabilityFinding.id.label("finding_id"),
            VulnerabilityFinding.status.label("status"),
            VulnerabilityFinding.analysis_justification.label("justification"),
            VulnerabilityFinding.component_version_id.label("component_version_id"),
            Vulnerability.external_id.label("cve_id"),
            Vulnerability.source.label("source"),
            ComponentVersion.purl_with_version.label("purl"),
        )
        .select_from(VulnerabilityFinding)
        .join(Vulnerability, Vulnerability.id == VulnerabilityFinding.vulnerability_id)
        .join(
            ComponentVersion,
            ComponentVersion.id == VulnerabilityFinding.component_version_id,
        )
        .where(VulnerabilityFinding.scan_id == scan_id)
        .order_by(
            Vulnerability.external_id.asc(),
            ComponentVersion.purl_with_version.asc(),
            VulnerabilityFinding.id.asc(),
        )
    )
    result = await session.execute(stmt)
    return [dict(row._mapping) for row in result.all()]


def _preferred_licenses(by_kind: ComponentLicenses) -> list[LicenseEntry]:
    """Pick the highest-priority non-empty license set for CycloneDX."""
    for kind in _LICENSE_KIND_PRIORITY:
        entries = by_kind.get(kind)
        if entries:
            return entries
    return []


def _spdx_license_expr(entries: list[LicenseEntry]) -> str:
    """Build an SPDX license expression from license entries.

    Only entries with a real SPDX id participate: an ORT ``LicenseRef-*`` would
    require a document-level ``hasExtractedLicensingInfos`` declaration to be a
    valid expression, which we do not emit yet (tracked as a follow-up). When no
    entry has an SPDX id we fall back to the spec sentinel ``NOASSERTION``.
    Multiple ids are conjoined with `` AND `` in sorted order for stability.
    """
    ids = sorted({e["spdx_id"] for e in entries if e["spdx_id"]})
    if not ids:
        return "NOASSERTION"
    return " AND ".join(ids)


def _top_component_version(scan: Scan | None) -> str:
    """The top-level component version for the SBOM metadata.

    SK Telecom (and SBOM consumers generally) expect the delivered software's
    real version here, not an internal id. We surface the optional
    ``scan_metadata['release']`` label (Feature #18 Part A) when present; absent
    a release we keep the scan id (still byte-stable, never empty). Mirrors the
    extraction in ``dashboard_service._release_from_metadata`` /
    ``release_snapshot_service`` — duplicated here to avoid an import cycle.
    """
    if scan is None:
        return "no-scan"
    metadata = scan.scan_metadata or {}
    raw = metadata.get("release")
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped:
            return stripped
    return str(scan.id)


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


def _cyclonedx_license_entries(entries: list[LicenseEntry]) -> list[dict[str, Any]]:
    """Render license entries as CycloneDX ``licenses`` array members.

    SPDX-identified licenses use the ``license.id`` form (validators map it to
    the SPDX list); ORT custom licenses without an id fall back to
    ``license.name``.
    """
    out: list[dict[str, Any]] = []
    for e in entries:
        if e["spdx_id"]:
            out.append({"license": {"id": e["spdx_id"]}})
        else:
            out.append({"license": {"name": e["name"]}})
    return out


def _cyclonedx_components(
    rows: list[dict[str, Any]],
    licenses_by_cv: dict[uuid.UUID, ComponentLicenses],
) -> list[dict[str, Any]]:
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
        # licenses precede purl to match the CycloneDX schema field order.
        license_entries = _cyclonedx_license_entries(
            _preferred_licenses(licenses_by_cv.get(r["component_version_id"], {}))
        )
        if license_entries:
            comp["licenses"] = license_entries
        if r.get("purl"):
            comp["purl"] = r["purl"]
        out.append(comp)
    return out


def _cyclonedx_vulnerabilities(vuln_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Render finding rows as CycloneDX ``vulnerabilities[]`` entries (H-4).

    The internal → CycloneDX ``analysis.state`` mapping is the shared
    :data:`CYCLONEDX_STATE_MAP` (single source with the standalone VEX export).
    Free-text analyst notes go to ``analysis.detail`` — never the closed
    ``analysis.justification`` enum. ``affects[].ref`` is the affected
    component's ``bom-ref`` (the component_version UUID used in
    ``components[]``), so the finding joins to its component inside this
    document without parsing purls.
    """
    out: list[dict[str, Any]] = []
    for r in vuln_rows:
        analysis: dict[str, Any] = {"state": CYCLONEDX_STATE_MAP[r["status"]]}
        if r.get("justification"):
            analysis["detail"] = r["justification"]
        out.append(
            {
                "id": r["cve_id"],
                # Mirrors the DB Vulnerability.source (NVD / OSV / GHSA…).
                "source": {"name": r["source"]},
                "analysis": analysis,
                "affects": [{"ref": str(r["component_version_id"])}],
            }
        )
    return out


def _build_cyclonedx_doc(
    *,
    project: Project,
    scan: Scan | None,
    rows: list[dict[str, Any]],
    licenses_by_cv: dict[uuid.UUID, ComponentLicenses],
    vuln_rows: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Build the CycloneDX 1.6 dict (used both for the JSON and XML serializers)."""
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
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
                "version": _top_component_version(scan),
            },
        },
        "components": _cyclonedx_components(rows, licenses_by_cv),
        # H-4: the SBOM alone carries the VEX triage. Always present (empty
        # list when the scan has no findings) so consumers can rely on the key.
        "vulnerabilities": _cyclonedx_vulnerabilities(vuln_rows),
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


_CDX_NS = "http://cyclonedx.org/schema/bom/1.6"


def _serialize_cyclonedx_xml(doc: dict[str, Any]) -> str:
    """
    Render the CycloneDX 1.6 dict as XML using ElementTree.

    We deliberately do not depend on ``cyclonedx-python-lib`` so the export
    surface is import-cheap and stable across the lib's major-version bumps.
    The shape we emit here is a strict subset of the schema (the same subset
    most tools care about): metadata + components + vulnerabilities.
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
        if "licenses" in comp:
            lics_el = ET.SubElement(c, f"{{{_CDX_NS}}}licenses")
            for entry in comp["licenses"]:
                lic_el = ET.SubElement(lics_el, f"{{{_CDX_NS}}}license")
                lic = entry["license"]
                if "id" in lic:
                    ET.SubElement(lic_el, f"{{{_CDX_NS}}}id").text = lic["id"]
                else:
                    ET.SubElement(lic_el, f"{{{_CDX_NS}}}name").text = lic["name"]
        if "purl" in comp:
            ET.SubElement(c, f"{{{_CDX_NS}}}purl").text = comp["purl"]

    # H-4: VEX triage — mirrors the JSON ``vulnerabilities[]`` array. In the
    # XML schema each affected component ref nests as affects > target > ref.
    vulns_el = ET.SubElement(bom, f"{{{_CDX_NS}}}vulnerabilities")
    for vuln in doc["vulnerabilities"]:
        v = ET.SubElement(vulns_el, f"{{{_CDX_NS}}}vulnerability")
        ET.SubElement(v, f"{{{_CDX_NS}}}id").text = vuln["id"]
        source_el = ET.SubElement(v, f"{{{_CDX_NS}}}source")
        ET.SubElement(source_el, f"{{{_CDX_NS}}}name").text = vuln["source"]["name"]
        analysis_el = ET.SubElement(v, f"{{{_CDX_NS}}}analysis")
        ET.SubElement(analysis_el, f"{{{_CDX_NS}}}state").text = vuln["analysis"]["state"]
        if "detail" in vuln["analysis"]:
            ET.SubElement(analysis_el, f"{{{_CDX_NS}}}detail").text = vuln["analysis"]["detail"]
        affects_el = ET.SubElement(v, f"{{{_CDX_NS}}}affects")
        for target in vuln["affects"]:
            target_el = ET.SubElement(affects_el, f"{{{_CDX_NS}}}target")
            ET.SubElement(target_el, f"{{{_CDX_NS}}}ref").text = target["ref"]

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


def _spdx_packages(
    rows: list[dict[str, Any]],
    licenses_by_cv: dict[uuid.UUID, ComponentLicenses],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        spdx_id = _spdx_id_for_component(r["component_version_id"])
        by_kind = licenses_by_cv.get(r["component_version_id"], {})
        declared = by_kind.get("declared", [])
        concluded = by_kind.get("concluded", [])
        # licenseConcluded is the scanner's final verdict; fall back to the
        # declared (package-metadata) set when no concluded finding exists.
        license_declared = _spdx_license_expr(declared)
        license_concluded = _spdx_license_expr(concluded if concluded else declared)
        pkg: dict[str, Any] = {
            "SPDXID": spdx_id,
            "name": r["name"],
            "versionInfo": r["version"],
            # SPDX requires downloadLocation; we don't carry one, so use the
            # SPDX-reserved sentinel for "we know it exists but don't have a
            # location for it". (NOASSERTION = caller should assert.)
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            # License expressions are derived from persisted LicenseFinding rows
            # (NOASSERTION only when the component has no SPDX-identified
            # license). copyrightText stays NOASSERTION: we have no copyright
            # source yet (tracked with the NOTICE pipeline).
            "licenseConcluded": license_concluded,
            "licenseDeclared": license_declared,
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
    licenses_by_cv: dict[uuid.UUID, ComponentLicenses],
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
        "packages": _spdx_packages(rows, licenses_by_cv),
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
    scan_id: uuid.UUID | None = None,
) -> tuple[str, str, str]:
    """
    Build the SBOM body for ``project_id`` in the requested format.

    Returns ``(content, content_type, filename)``.

    Raises :class:`SBOMUnsupportedFormat` (422) for an unknown format. The
    router is responsible for translating the missing-project / forbidden
    cases to RFC 7807 — those checks fire BEFORE this function runs.

    ``scan_id`` (feature #28) optionally pins the export to a SPECIFIC succeeded
    snapshot instead of the latest succeeded scan. It is validated via
    :func:`services.scan_resolution.resolve_snapshot_scan_id` (must belong to
    THIS project AND be ``status='succeeded'``); an invalid / cross-project /
    non-succeeded id raises :class:`SnapshotScanNotFound`, which the router maps
    to a 404 (existence-hide). Omitting it preserves the latest-succeeded
    default.

    Empty-project policy (see module docstring): if the project has no
    succeeded scan (and no ``scan_id`` was pinned) we still return a valid SBOM
    document with an empty components/packages list, not 404.

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

    # Resolve the snapshot scan: the pinned ``scan_id`` (validated to belong to
    # this project AND be succeeded) when given, else the latest succeeded scan.
    # SnapshotScanNotFound (invalid pin) propagates to the router → 404.
    resolved_scan_id = await resolve_snapshot_scan_id(session, project_id, scan_id)
    scan = (
        await _load_scan_by_id(session, scan_id=resolved_scan_id)
        if resolved_scan_id is not None
        else None
    )
    rows: list[dict[str, Any]] = []
    licenses_by_cv: dict[uuid.UUID, ComponentLicenses] = {}
    vuln_rows: list[dict[str, Any]] = []
    if scan is not None:
        rows = await _load_scan_components(session, scan_id=scan.id)
        licenses_by_cv = await _load_scan_licenses(session, scan_id=scan.id)
        # VEX (H-4) is CycloneDX-only: SPDX has no native VEX representation,
        # so skip the findings query for SPDX exports.
        if fmt.startswith("cyclonedx"):
            vuln_rows = await _load_scan_vulnerabilities(session, scan_id=scan.id)

    # BUG-006: default to the scan's persisted completion time so re-exports
    # are byte-stable. `now` stays an explicit override for callers that need
    # to pin a specific stamp.
    timestamp = now if now is not None else _deterministic_timestamp(scan)

    content_type, _ = _FORMAT_CATALOG[fmt]
    filename = _filename(project, fmt)

    if fmt == "cyclonedx-json":
        body = _serialize_cyclonedx_json(
            _build_cyclonedx_doc(
                project=project,
                scan=scan,
                rows=rows,
                licenses_by_cv=licenses_by_cv,
                vuln_rows=vuln_rows,
                now=timestamp,
            )
        )
    elif fmt == "cyclonedx-xml":
        body = _serialize_cyclonedx_xml(
            _build_cyclonedx_doc(
                project=project,
                scan=scan,
                rows=rows,
                licenses_by_cv=licenses_by_cv,
                vuln_rows=vuln_rows,
                now=timestamp,
            )
        )
    elif fmt == "spdx-json":
        body = _serialize_spdx_json(
            _build_spdx_doc(
                project=project,
                scan=scan,
                rows=rows,
                licenses_by_cv=licenses_by_cv,
                now=timestamp,
            )
        )
    elif fmt == "spdx-tv":
        body = _serialize_spdx_tv(
            _build_spdx_doc(
                project=project,
                scan=scan,
                rows=rows,
                licenses_by_cv=licenses_by_cv,
                now=timestamp,
            )
        )
    else:  # pragma: no cover - guarded by the catalog check above
        raise SBOMUnsupportedFormat(f"unknown SBOM format {fmt!r}")

    # Operational signal: SK Telecom (and most SBOM consumers) reject
    # ``pkg:generic/`` PURLs, which surface here as a "generic"/"unknown"
    # package_type. Counting them lets operators spot low-quality scans without
    # changing the export body.
    generic_count = sum(
        1 for r in rows if r.get("package_type") in {"generic", "unknown"}
    )

    log.info(
        "sbom_exported",
        project_id=str(project_id),
        scan_id=str(scan.id) if scan is not None else None,
        format=fmt,
        components=len(rows),
        licensed_components=len(licenses_by_cv),
        vulnerabilities=len(vuln_rows),
        generic_purls=generic_count,
        bytes=len(body.encode("utf-8")),
    )
    return body, content_type, filename


__all__ = [
    "SBOMExportError",
    "SBOMUnsupportedFormat",
    "SUPPORTED_FORMATS",
    "export_sbom",
]
