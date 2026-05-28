"""
VEX (Vulnerability Exploitability eXchange) export service — v2.1 Track A (A1).

Builds a VEX document (OpenVEX or CycloneDX-VEX) from the *current internal
finding status* of a project's latest scan. The router (``api/v1/vex.py``) is a
thin HTTP adapter that wires up auth + IDOR + Content-Disposition; serialization
decisions live here so the same code can be re-used by background jobs (report
attachments, scheduled deliveries) and by the upcoming A2 round-trip import test
without booting FastAPI.

Why a dedicated VEX export
--------------------------
Today every vulnerability finding carries an *internal* status
(``VULN_FINDING_STATUS_VALUES``: new / analyzing / exploitable / not_affected /
false_positive / suppressed / fixed) but there is no way to emit that triage as
a standards-shaped VEX document. A1 adds the read-only export; A2 will add the
import side and use this exporter as the basis for a round-trip consistency test
(import(export(x)) == x at the status level).

Output formats
--------------
- ``openvex``    — OpenVEX v0.2.0 JSON      (Content-Type ``application/json``)
- ``cyclonedx``  — CycloneDX 1.5 VEX BOM    (Content-Type ``application/json``)

Both are JSON; the ``Content-Type`` is therefore ``application/json`` for both,
and the ``Content-Disposition`` filename extension disambiguates them.

Status mapping (internal → VEX)
-------------------------------
Internal status is the single source of truth; each format has its own closed
status vocabulary, so we map deterministically. The free-text justification
(``analysis_justification``) is carried verbatim into a free-text field — never
mapped onto the OpenVEX ``justification`` enum, whose members have precise legal
meaning we cannot infer from arbitrary analyst prose.

OpenVEX (``status`` of each statement):

    internal           OpenVEX status
    ----------------   ----------------------
    new                under_investigation
    analyzing          under_investigation
    exploitable        affected
    not_affected       not_affected
    false_positive     not_affected
    suppressed         not_affected
    fixed              fixed

CycloneDX-VEX (``vulnerabilities[].analysis.state``):

    internal           CycloneDX analysis.state
    ----------------   ----------------------
    new                in_triage
    analyzing          in_triage
    exploitable        exploitable
    not_affected       not_affected
    false_positive     false_positive
    suppressed         not_affected
    fixed              resolved

The free-text ``analysis_justification`` goes to OpenVEX ``impact_statement``
and to CycloneDX ``analysis.detail``.

Byte-stability
--------------
Like the SBOM exporter (BUG-006), re-exporting the same scan must yield
byte-for-byte identical output so callers may content-hash the body. That means
*every* field that could vary between two calls is derived from persisted,
scan-bound state — never wall-clock time or a fresh ``uuid4()``:

- The OpenVEX ``@id`` / document ``version`` derive from a deterministic UUIDv5
  in a fixed namespace anchored on the scan id (or project id when the project
  has no succeeded scan), so two exports of the same scan share an id.
- ``timestamp`` (OpenVEX) / ``metadata.timestamp`` (CycloneDX) use the scan's
  persisted completion time (``completed_at`` → ``updated_at`` → ``created_at``),
  never ``datetime.now()``. A project with no scan uses the Unix-epoch sentinel
  so the document is still well-formed and stable. An explicit ``now`` override
  exists only for callers that must pin a stamp (and accept the resulting
  non-determinism); HTTP callers pass nothing.
- statements / vulnerabilities are emitted in a deterministic order
  (``(vulnerability external_id, purl)``) and products / affects refs are
  sorted, so the byte layout is stable across re-exports.

Empty-project policy
--------------------
A project with no succeeded scan (or no findings) still gets a valid VEX
document with an empty ``statements`` / ``vulnerabilities`` list, mirroring the
SBOM "empty but well-formed" policy.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Component,
    ComponentVersion,
    Project,
    Scan,
    Vulnerability,
    VulnerabilityFinding,
)
from services.scan_resolution import latest_succeeded_scan_id

log = structlog.get_logger("vex_export.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class VEXExportError(Exception):
    """Base — each subclass carries an HTTP status used by the router."""

    status_code: int = 400
    title: str = "VEX Export Error"


class VEXUnsupportedFormat(VEXExportError):
    status_code = 422
    title = "Unsupported VEX Format"


# ---------------------------------------------------------------------------
# Format catalogue
# ---------------------------------------------------------------------------

# Each format declares (content_type, file_extension). The router uses both.
# Both VEX formats are JSON, so the extension is what disambiguates the
# download. Keep a literal-style map so adding a new format is a one-line edit.
_FORMAT_CATALOG: dict[str, tuple[str, str]] = {
    "openvex": ("application/json", "openvex.json"),
    "cyclonedx": ("application/json", "vex.cdx.json"),
}

SUPPORTED_FORMATS: tuple[str, ...] = tuple(_FORMAT_CATALOG.keys())


# Fixed UUIDv5 namespace for deriving deterministic document ids from a scan
# (or project) id. This is a constant *label* — NOT environment / config — so
# it is safe at module scope under CLAUDE.md rule #11 (no env access at import
# time). It must never change once shipped: changing it would alter every
# previously-emitted VEX @id and break hash-based verification of older docs.
_VEX_UUID_NAMESPACE = uuid.UUID("8a4c1d2e-9f6b-5a3c-8d7e-1b2c3d4e5f60")

# Sentinel timestamp for a project that has never produced a succeeded scan.
# Using a fixed epoch keeps the "empty VEX" document byte-stable across
# re-exports (the alternative — current time — would re-introduce BUG-006).
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# OpenVEX context + author are document-level constants (not config).
_OPENVEX_CONTEXT = "https://openvex.dev/ns/v0.2.0"
_OPENVEX_AUTHOR = "TrustedOSS Portal"


# ---------------------------------------------------------------------------
# Status mapping (internal → VEX). Single source of truth for both the code
# and the docs. Each map is total over VULN_FINDING_STATUS_VALUES; the loader
# only ever produces those seven values (DB ENUM), so a KeyError here would be
# a schema drift and is treated as a programming error (caught by tests).
# ---------------------------------------------------------------------------

_OPENVEX_STATUS_MAP: dict[str, str] = {
    "new": "under_investigation",
    "analyzing": "under_investigation",
    "exploitable": "affected",
    "not_affected": "not_affected",
    "false_positive": "not_affected",
    "suppressed": "not_affected",
    "fixed": "fixed",
}

_CYCLONEDX_STATE_MAP: dict[str, str] = {
    "new": "in_triage",
    "analyzing": "in_triage",
    "exploitable": "exploitable",
    "not_affected": "not_affected",
    "false_positive": "false_positive",
    "suppressed": "not_affected",
    "fixed": "resolved",
}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


async def _load_project(session: AsyncSession, project_id: uuid.UUID) -> Project | None:
    result = await session.execute(select(Project).where(Project.id == project_id))
    return result.scalar_one_or_none()


async def _load_findings(
    session: AsyncSession, *, scan_id: uuid.UUID
) -> list[dict[str, Any]]:
    """
    Return per-finding dictionaries for the given scan.

    Each row is shaped to be format-agnostic so each serializer can pick the
    fields it needs without re-querying. Deterministic order — by CVE external
    id then purl then finding id — gives the byte-stable layout the module
    docstring promises (BUG-006-style).
    """
    stmt = (
        select(
            VulnerabilityFinding.id.label("finding_id"),
            VulnerabilityFinding.status.label("status"),
            VulnerabilityFinding.analysis_justification.label("justification"),
            Vulnerability.external_id.label("cve_id"),
            Vulnerability.source.label("source"),
            ComponentVersion.purl_with_version.label("purl"),
            Component.name.label("component_name"),
            ComponentVersion.version.label("component_version"),
        )
        .select_from(VulnerabilityFinding)
        .join(Vulnerability, Vulnerability.id == VulnerabilityFinding.vulnerability_id)
        .join(
            ComponentVersion,
            ComponentVersion.id == VulnerabilityFinding.component_version_id,
        )
        .join(Component, Component.id == ComponentVersion.component_id)
        .where(VulnerabilityFinding.scan_id == scan_id)
        # Stable byte-for-byte output: order by CVE id, then purl, then the
        # finding id. external_id + purl is effectively unique per scan (a CVE
        # can affect multiple components → multiple findings), and finding_id
        # is a strict tiebreak so the order is a total order even for the
        # (unexpected) duplicate.
        .order_by(
            Vulnerability.external_id.asc(),
            ComponentVersion.purl_with_version.asc(),
            VulnerabilityFinding.id.asc(),
        )
    )
    result = await session.execute(stmt)
    return [dict(row._mapping) for row in result.all()]


async def _load_latest_succeeded_scan(
    session: AsyncSession, *, project: Project
) -> Scan | None:
    """Return the project's latest *succeeded* scan row, or None.

    We resolve the latest SUCCEEDED scan via
    :func:`services.scan_resolution.latest_succeeded_scan_id` — NOT
    ``project.latest_scan_id`` (the last *attempted* scan). That way the VEX
    export reflects exactly the findings the vulnerability list / build gate show:
    a project whose newest attempt failed still exports its last good scan's
    triage instead of an empty document. When the project has never succeeded a
    scan we treat it as empty (no findings, well-formed doc).
    """
    scan_id = await latest_succeeded_scan_id(session, project.id)
    if scan_id is None:
        return None
    result = await session.execute(select(Scan).where(Scan.id == scan_id))
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Determinism helpers (shared with the SBOM exporter's design intent)
# ---------------------------------------------------------------------------


def _utc_iso(now: datetime) -> str:
    """ISO 8601 timestamp with millisecond precision and a Z suffix.

    Both VEX schemas accept the literal-Z form; using Z (not +00:00) sidesteps
    validators that only recognise Z.
    """
    return now.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _deterministic_timestamp(scan: Scan | None) -> datetime:
    """Persisted scan-completion time used for the VEX document timestamp.

    Must derive from scan-bound, persisted state so two exports of the same
    scan are byte-identical. Prefer the most specific completion marker and
    fall back ``updated_at`` → ``created_at``. No scan → Unix-epoch sentinel.
    """
    if scan is None:
        return _EPOCH
    return scan.completed_at or scan.updated_at or scan.created_at or _EPOCH


def _deterministic_doc_uuid(project: Project, scan: Scan | None) -> uuid.UUID:
    """A stable UUIDv5 derived from the scan id (or project id when no scan).

    Re-exporting the same scan reproduces the same UUID, so the OpenVEX ``@id``
    is byte-stable. Anchored on the *scan* id (not the project) so two scans of
    the same project get distinct ids, which is what diff tooling expects.
    """
    anchor = str(scan.id) if scan is not None else f"project:{project.id}"
    return uuid.uuid5(_VEX_UUID_NAMESPACE, anchor)


def _deterministic_version(scan: Scan | None) -> int:
    """The document ``version`` field.

    A1 is read-only and always emits a single immutable snapshot per scan, so
    version is a constant ``1``. (Both OpenVEX and CycloneDX want a version;
    keeping it constant preserves byte-stability.)
    """
    return 1


# ---------------------------------------------------------------------------
# OpenVEX v0.2.0
# ---------------------------------------------------------------------------


def _openvex_statements(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        status = _OPENVEX_STATUS_MAP[r["status"]]
        statement: dict[str, Any] = {
            "vulnerability": {"name": r["cve_id"]},
            # products are identified by purl. We emit a single-element list
            # per finding; OpenVEX allows aggregating multiple products per
            # statement, but one-finding-one-statement keeps the mapping
            # lossless and round-trippable (A2).
            "products": [{"@id": r["purl"]}] if r.get("purl") else [],
            "status": status,
        }
        # Free-text justification → impact_statement (NEVER the OpenVEX
        # `justification` enum, which has precise legal meaning we cannot infer
        # from arbitrary analyst prose).
        if r.get("justification"):
            statement["impact_statement"] = r["justification"]
        out.append(statement)
    return out


def _build_openvex_doc(
    *,
    project: Project,
    scan: Scan | None,
    rows: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    doc_uuid = _deterministic_doc_uuid(project, scan)
    return {
        "@context": _OPENVEX_CONTEXT,
        "@id": f"https://trustedoss.io/vex/{project.id}/{doc_uuid}",
        "author": _OPENVEX_AUTHOR,
        "timestamp": _utc_iso(now),
        "version": _deterministic_version(scan),
        "statements": _openvex_statements(rows),
    }


def _serialize_openvex(doc: dict[str, Any]) -> str:
    # Keys are emitted in the dict-insertion order above (a documented, stable
    # field order); statements are already sorted by the loader. indent=2 keeps
    # the body readable; VEX bodies stay small.
    return json.dumps(doc, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CycloneDX 1.5 VEX
# ---------------------------------------------------------------------------


def _cyclonedx_vulnerabilities(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        state = _CYCLONEDX_STATE_MAP[r["status"]]
        analysis: dict[str, Any] = {"state": state}
        if r.get("justification"):
            analysis["detail"] = r["justification"]
        vuln: dict[str, Any] = {
            "id": r["cve_id"],
            # `source` mirrors the DB Vulnerability.source (NVD / OSV / GHSA…).
            "source": {"name": r["source"]},
            "analysis": analysis,
            # `affects[].ref` points at the affected component. We use the purl
            # directly so the document is self-contained (no bom-ref join to a
            # components list, which a pure VEX doc may omit).
            "affects": [{"ref": r["purl"]}] if r.get("purl") else [],
        }
        out.append(vuln)
    return out


def _build_cyclonedx_doc(
    *,
    project: Project,
    scan: Scan | None,
    rows: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        # Deterministic urn:uuid serialNumber (BUG-006): derives from the scan
        # id so re-exports are byte-identical — never a fresh uuid4().
        "serialNumber": f"urn:uuid:{_deterministic_doc_uuid(project, scan)}",
        "version": _deterministic_version(scan),
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
                "bom-ref": f"project:{project.id}",
                "type": "application",
                "name": project.name,
                "version": str(scan.id) if scan is not None else "no-scan",
            },
        },
        "vulnerabilities": _cyclonedx_vulnerabilities(rows),
    }


def _serialize_cyclonedx(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _filename(project: Project, fmt: str) -> str:
    """Operator-friendly filename: ``vex-<project-slug>.<ext>``."""
    _, ext = _FORMAT_CATALOG[fmt]
    # Slug is validated [a-z0-9-]+ at create time, so no further escaping.
    return f"vex-{project.slug}.{ext}"


async def export_vex(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    fmt: str,
    now: datetime | None = None,
) -> tuple[str, str, str]:
    """
    Build the VEX body for ``project_id`` in the requested format.

    Returns ``(content, content_type, filename)``.

    Raises :class:`VEXUnsupportedFormat` (422) for an unknown format. The
    router is responsible for translating the missing-project / forbidden cases
    to RFC 7807 — those checks fire BEFORE this function runs.

    Empty-project policy: a project with no succeeded scan (or no findings)
    still returns a valid VEX document with an empty statements/vulnerabilities
    list, not 404.

    Byte-stability (BUG-006): by default the document timestamp derives from
    the scan's persisted completion time, so two exports of the same scan are
    byte-identical. ``now`` is an explicit override retained only for callers
    that must stamp a specific time (and accept the non-determinism that
    implies); production / HTTP callers pass nothing.
    """
    if fmt not in _FORMAT_CATALOG:
        raise VEXUnsupportedFormat(
            f"unknown VEX format {fmt!r}; supported: {sorted(SUPPORTED_FORMATS)}",
        )

    project = await _load_project(session, project_id)
    if project is None:
        # Surface as the same 422 we'd use for an unknown format. The router
        # checks IDOR + existence BEFORE calling us, so this branch is only
        # reachable from internal callers — keeping it here makes the contract
        # self-consistent.
        raise VEXUnsupportedFormat(f"project {project_id} not found")

    scan = await _load_latest_succeeded_scan(session, project=project)
    rows: list[dict[str, Any]] = []
    if scan is not None:
        rows = await _load_findings(session, scan_id=scan.id)

    timestamp = now if now is not None else _deterministic_timestamp(scan)

    content_type, _ = _FORMAT_CATALOG[fmt]
    filename = _filename(project, fmt)

    if fmt == "openvex":
        body = _serialize_openvex(
            _build_openvex_doc(project=project, scan=scan, rows=rows, now=timestamp)
        )
    elif fmt == "cyclonedx":
        body = _serialize_cyclonedx(
            _build_cyclonedx_doc(project=project, scan=scan, rows=rows, now=timestamp)
        )
    else:  # pragma: no cover - guarded by the catalog check above
        raise VEXUnsupportedFormat(f"unknown VEX format {fmt!r}")

    log.info(
        "vex_exported",
        project_id=str(project_id),
        scan_id=str(scan.id) if scan is not None else None,
        format=fmt,
        statements=len(rows),
        bytes=len(body.encode("utf-8")),
    )
    return body, content_type, filename


__all__ = [
    "SUPPORTED_FORMATS",
    "VEXExportError",
    "VEXUnsupportedFormat",
    "export_vex",
]
