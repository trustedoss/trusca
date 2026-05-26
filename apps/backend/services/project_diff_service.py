"""
Release-diff service — feature #28 Phase 2 (compare two release snapshots).

Powers ``GET /v1/projects/{id}/diff?base=<scan_id>&target=<scan_id>``: given two
*succeeded* scans of the SAME project, compute the change in SCA posture between
them. Each succeeded scan is an immutable snapshot (``scan_components`` /
``vulnerability_findings`` / ``license_findings`` keyed by ``scan_id``), so a diff
is a pure set comparison over two scan ids — no mutation, no migration.

Both anchors are validated through ``services.scan_resolution.resolve_snapshot_scan_id``
(belongs to THIS project AND ``status='succeeded'``); an invalid / cross-project /
non-succeeded id raises :class:`SnapshotScanNotFound` → existence-hide 404 at the
router. ``base == target`` is allowed and yields an all-empty diff with equal
summaries.

What is computed (and why it can't drift from the rest of the app)
------------------------------------------------------------------
* **summary** — risk_score / severity component-counts / build-gate verdict /
  component_count for EACH side, reusing the SAME per-scan aggregation helpers
  the Releases table (``services.release_snapshot_service``) and the Overview tab
  use. We call the release-snapshot service's private aggregators directly so the
  diff summary is byte-for-byte the same numbers the Releases row shows.

* **components** — identity is the ``component`` *package* (``components.id`` /
  purl-without-version), NOT the versioned ``component_version``. For each
  package: in target-not-base → added; base-not-target → removed; in both with a
  different ``component_version`` → changed (base_version → target_version). One
  query loads ``(component_id, name, namespace, version, purl_with_version)`` per
  side; the set algebra runs in Python over the two small maps (component counts
  are tens–hundreds, not N rows).

* **vulnerabilities** — keyed by ``(cve_id, component_version)`` over the OPEN
  finding set: a finding whose status is ``not_affected`` / ``false_positive`` /
  ``suppressed`` / ``fixed`` (resolved/suppressed, incl. via VEX) counts as NOT
  open. introduced = open-in-target minus open-in-base; resolved = the converse.
  One query per side returns the open (cve_id, cv) keys + severity + names; the
  diff is a set difference over the keys.

* **licenses** — per-category component counts for base and target (reusing the
  release-snapshot license-distribution aggregator), surfaced as base/target
  pairs so the UI can render the delta.

Defensive cap
-------------
Component / vulnerability change-set lists are typically small, but a pathological
scan pair could in principle produce thousands of entries. Each enumerated list is
capped at :data:`_MAX_LIST` items and ``truncated`` is set when any cap bites. The
SUMMARY counts are always exact (they come from the grouped aggregations, not the
enumerated lists).

Authorization
-------------
Mirrors ``services.release_snapshot_service.list_release_snapshots`` /
``services.project_detail_service.get_project_overview``: load the project, then
``assert_team_access`` (super_admin bypasses; non-member → ``ProjectForbidden``
403; missing project → ``ProjectNotFound`` 404). The router translates both to
RFC 7807. No auth happens in the aggregation helpers — the caller team-scopes the
project first.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.security import CurrentUser
from models import (
    Component,
    ComponentVersion,
    Project,
    Scan,
    ScanComponent,
    Vulnerability,
    VulnerabilityFinding,
)
from services.policy_gate import evaluate_gate
from services.project_service import ProjectForbidden, ProjectNotFound
from services.release_snapshot_service import (
    _component_count_by_scan,
    _compute_risk_score,
    _license_distribution_by_scan,
    _release_label,
    _severity_distribution_by_scan,
)

log = structlog.get_logger("project_diff.service")

# The four risk-bearing severity buckets surfaced on the diff summary (info /
# none are not actionable). Matches schemas.project_diff.DiffSeverityDelta and
# release_snapshot_service._SUMMARY_BUCKETS.
_SUMMARY_BUCKETS = ("critical", "high", "medium", "low")

# Map the internal license_category ENUM values to the diff response keys. The
# response uses "prohibited"/"permissive" (product vocabulary) while the DB ENUM
# uses "forbidden"/"allowed"; "conditional"/"unknown" line up directly.
_LICENSE_CATEGORY_TO_RESPONSE = {
    "forbidden": "prohibited",
    "conditional": "conditional",
    "allowed": "permissive",
    "unknown": "unknown",
}
_RESPONSE_LICENSE_KEYS = ("prohibited", "conditional", "permissive", "unknown")

# Finding statuses that mean a finding is NOT open for diff purposes: resolved or
# suppressed (incl. via VEX — ``suppressed`` maps to OpenVEX ``not_affected``).
# Broader than policy_gate._CLOSED_FINDING_STATUSES (which keeps ``suppressed``
# blocking for release gating) BECAUSE the diff answers "did the user's perceived
# exposure change?": a suppressed CVE is not exposure the user is still tracking
# as open in the vuln list, so flipping it to/from suppressed should read as
# resolved/introduced — exactly the task's "suppressed/resolved via VEX → not
# open" rule.
_CLOSED_FINDING_STATUSES: tuple[str, ...] = (
    "not_affected",
    "false_positive",
    "suppressed",
    "fixed",
)

# Defensive per-list cap. Component / vuln change-sets are normally tens–hundreds;
# this only bites on a pathological scan pair. Summary counts stay exact.
_MAX_LIST = 1000


class _ComponentRow:
    """One ``(package, version)`` observed in a scan — the unit of the component diff."""

    __slots__ = (
        "component_id",
        "name",
        "namespace",
        "purl_no_version",
        "version",
        "purl_with_version",
        "cv_id",
    )

    def __init__(
        self,
        *,
        component_id: uuid.UUID,
        name: str,
        namespace: str | None,
        purl_no_version: str,
        version: str,
        purl_with_version: str,
        cv_id: uuid.UUID,
    ) -> None:
        self.component_id = component_id
        self.name = name
        self.namespace = namespace
        self.purl_no_version = purl_no_version
        self.version = version
        self.purl_with_version = purl_with_version
        self.cv_id = cv_id


async def _components_by_package(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
) -> dict[uuid.UUID, _ComponentRow]:
    """``{component_id: _ComponentRow}`` for one scan.

    Identity is the ``component`` package (``components.id``), so the map keys on
    the PACKAGE not the version — that is what lets "same package, different
    version" register as *changed* rather than added+removed. We assume one
    component_version per package per scan (the scan emits one version of each
    package); if a scan ever carried two versions of the same package the last
    row wins, which is acceptable for the diff's package-level view.

    One indexed query (``scan_components`` PK / ``ix`` cover the join). Reuses the
    same scan_components → component_version → component join the components-list
    service uses.
    """
    stmt = (
        select(
            Component.id.label("component_id"),
            Component.name.label("name"),
            Component.namespace.label("namespace"),
            Component.purl.label("purl_no_version"),
            ComponentVersion.id.label("cv_id"),
            ComponentVersion.version.label("version"),
            ComponentVersion.purl_with_version.label("purl_with_version"),
        )
        .select_from(ScanComponent)
        .join(
            ComponentVersion,
            ComponentVersion.id == ScanComponent.component_version_id,
        )
        .join(Component, Component.id == ComponentVersion.component_id)
        .where(ScanComponent.scan_id == scan_id)
    )
    result = await session.execute(stmt)
    rows: dict[uuid.UUID, _ComponentRow] = {}
    for r in result.all():
        rows[r.component_id] = _ComponentRow(
            component_id=r.component_id,
            name=r.name,
            namespace=r.namespace,
            purl_no_version=r.purl_no_version,
            version=r.version,
            purl_with_version=r.purl_with_version,
            cv_id=r.cv_id,
        )
    return rows


def _diff_components(
    base: dict[uuid.UUID, _ComponentRow],
    target: dict[uuid.UUID, _ComponentRow],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Return ``(added, removed, changed, truncated)`` for the package sets.

    added = package in target not in base; removed = package in base not in
    target; changed = same package present in BOTH at a different
    ``component_version`` (compared by cv id, falling back to the version string).
    Deterministic order (by name then purl) so the response is stable across runs.
    """
    base_ids = set(base.keys())
    target_ids = set(target.keys())

    added_rows = sorted(
        (target[cid] for cid in target_ids - base_ids),
        key=lambda r: (r.name, r.purl_with_version),
    )
    removed_rows = sorted(
        (base[cid] for cid in base_ids - target_ids),
        key=lambda r: (r.name, r.purl_with_version),
    )
    changed_rows = sorted(
        (
            (base[cid], target[cid])
            for cid in base_ids & target_ids
            if base[cid].cv_id != target[cid].cv_id
            or base[cid].version != target[cid].version
        ),
        key=lambda pair: (pair[0].name, pair[0].purl_no_version),
    )

    truncated = (
        len(added_rows) > _MAX_LIST
        or len(removed_rows) > _MAX_LIST
        or len(changed_rows) > _MAX_LIST
    )

    added = [
        {
            "name": r.name,
            "namespace": r.namespace,
            "purl": r.purl_with_version,
            "version": r.version,
        }
        for r in added_rows[:_MAX_LIST]
    ]
    removed = [
        {
            "name": r.name,
            "namespace": r.namespace,
            "purl": r.purl_with_version,
            "version": r.version,
        }
        for r in removed_rows[:_MAX_LIST]
    ]
    changed = [
        {
            "name": b.name,
            "namespace": b.namespace,
            "purl": b.purl_no_version,
            "base_version": b.version,
            "target_version": t.version,
        }
        for b, t in changed_rows[:_MAX_LIST]
    ]
    return added, removed, changed, truncated


async def _open_findings_by_key(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
) -> dict[tuple[str, str], dict[str, Any]]:
    """``{(cve_id, component_version): finding info}`` for OPEN findings of one scan.

    "Open" = status NOT IN ``_CLOSED_FINDING_STATUSES`` (resolved / suppressed,
    incl. via VEX, are excluded). The key is ``(cve external_id, cv version)`` so
    the diff registers "the same CVE on the same package version" — the user's
    unit of exposure. Multiple component_versions affected by the same CVE produce
    distinct keys (each is its own exposure). One indexed query per side; the set
    difference happens in Python over the two small key sets.
    """
    stmt = (
        select(
            Vulnerability.external_id.label("cve_id"),
            cast(Vulnerability.severity, String).label("severity"),
            Component.name.label("component_name"),
            ComponentVersion.version.label("component_version"),
        )
        .select_from(VulnerabilityFinding)
        .join(
            Vulnerability,
            Vulnerability.id == VulnerabilityFinding.vulnerability_id,
        )
        .join(
            ComponentVersion,
            ComponentVersion.id == VulnerabilityFinding.component_version_id,
        )
        .join(Component, Component.id == ComponentVersion.component_id)
        .where(VulnerabilityFinding.scan_id == scan_id)
        .where(
            cast(VulnerabilityFinding.status, String).notin_(_CLOSED_FINDING_STATUSES)
        )
    )
    result = await session.execute(stmt)
    findings: dict[tuple[str, str], dict[str, Any]] = {}
    for r in result.all():
        key = (r.cve_id, r.component_version)
        # Dedupe on (cve_id, component_version): if the same CVE appears more than
        # once on the same version (multiple findings), keep the first — they
        # describe the same exposure.
        if key in findings:
            continue
        findings[key] = {
            "cve_id": r.cve_id,
            "severity": r.severity,
            "component_name": r.component_name,
            "component_version": r.component_version,
        }
    return findings


def _diff_vulnerabilities(
    base: dict[tuple[str, str], dict[str, Any]],
    target: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Return ``(introduced, resolved, truncated)``.

    introduced = open-in-target keys minus open-in-base keys; resolved = the
    converse. Deterministic order by (cve_id, component_name, component_version).
    """
    base_keys = set(base.keys())
    target_keys = set(target.keys())

    introduced_rows = sorted(
        (target[k] for k in target_keys - base_keys),
        key=lambda d: (d["cve_id"], d["component_name"], d["component_version"]),
    )
    resolved_rows = sorted(
        (base[k] for k in base_keys - target_keys),
        key=lambda d: (d["cve_id"], d["component_name"], d["component_version"]),
    )

    truncated = len(introduced_rows) > _MAX_LIST or len(resolved_rows) > _MAX_LIST
    return introduced_rows[:_MAX_LIST], resolved_rows[:_MAX_LIST], truncated


def _severity_summary_pair(
    base_dist: dict[str, int],
    target_dist: dict[str, int],
) -> dict[str, dict[str, int]]:
    """``{bucket: {"base": n, "target": n}}`` for the four risk-bearing buckets."""
    return {
        bucket: {
            "base": base_dist.get(bucket, 0),
            "target": target_dist.get(bucket, 0),
        }
        for bucket in _SUMMARY_BUCKETS
    }


def _license_category_pair(
    base_dist: dict[str, int],
    target_dist: dict[str, int],
) -> dict[str, dict[str, int]]:
    """``{response_category: {"base": n, "target": n}}`` over the four categories.

    The aggregator emits internal ENUM keys (forbidden/allowed/…); we re-key to
    the response vocabulary (prohibited/permissive/…) and ensure all four keys
    are present (zero when a category is absent in that snapshot).
    """
    base_resp: dict[str, int] = dict.fromkeys(_RESPONSE_LICENSE_KEYS, 0)
    target_resp: dict[str, int] = dict.fromkeys(_RESPONSE_LICENSE_KEYS, 0)
    for internal, response_key in _LICENSE_CATEGORY_TO_RESPONSE.items():
        base_resp[response_key] += base_dist.get(internal, 0)
        target_resp[response_key] += target_dist.get(internal, 0)
    return {
        key: {"base": base_resp[key], "target": target_resp[key]}
        for key in _RESPONSE_LICENSE_KEYS
    }


async def _gate_status(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    scan_id: uuid.UUID,
) -> str | None:
    """Build-gate verdict pinned to one snapshot, or None if not evaluable.

    Mirrors ``release_snapshot_service``: ``evaluate_gate`` with an explicit
    succeeded ``scan_id`` always returns a real 'pass'/'fail' verdict; we map the
    (impossible-here) no-verdict shape to None to keep the contract honest.
    """
    gate_result = await evaluate_gate(session, project_id, scan_id=scan_id)
    return gate_result.gate if gate_result.scan_id is not None else None


async def diff_release_snapshots(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
    base_scan_id: uuid.UUID,
    target_scan_id: uuid.UUID,
) -> dict[str, Any]:
    """Compute the diff between two succeeded-scan snapshots of one project.

    ``base_scan_id`` / ``target_scan_id`` MUST already be validated as succeeded
    snapshots of ``project_id`` (the router does so via
    :func:`services.scan_resolution.resolve_snapshot_scan_id` before calling, so
    an invalid pin is a 404 BEFORE we run any aggregation). This function then
    only enforces project-team access and computes the diff.

    Returns a plain dict shaped to :class:`schemas.project_diff.ProjectDiff`.

    Authorization mirrors :func:`get_project_overview`: ``ProjectNotFound`` (404)
    for a missing project, ``ProjectForbidden`` (403) for a non-member (super_admin
    bypasses).
    """
    project_result = await session.execute(
        select(Project).where(Project.id == project_id)
    )
    project = project_result.scalar_one_or_none()
    if project is None:
        raise ProjectNotFound(f"project {project_id} not found")

    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="project_diff",
        resource_id=str(project_id),
        deny=lambda: ProjectForbidden(
            f"actor is not a member of team {project.team_id}",
        ),
    )

    # Load the two scan rows so we can surface the release label + created_at for
    # each anchor (both are known-succeeded by the router's resolver).
    scan_rows = (
        await session.execute(
            select(Scan).where(Scan.id.in_({base_scan_id, target_scan_id}))
        )
    ).scalars().all()
    scan_by_id = {s.id: s for s in scan_rows}
    base_scan = scan_by_id[base_scan_id]
    target_scan = scan_by_id[target_scan_id]

    same_scan = base_scan_id == target_scan_id

    # ---- summary (reuse the Releases / Overview per-scan aggregators) ----
    scan_ids = [base_scan_id] if same_scan else [base_scan_id, target_scan_id]
    severity_by_scan = await _severity_distribution_by_scan(session, scan_ids=scan_ids)
    license_by_scan = await _license_distribution_by_scan(session, scan_ids=scan_ids)
    component_count_by_scan = await _component_count_by_scan(session, scan_ids=scan_ids)

    base_sev = severity_by_scan.get(base_scan_id, {})
    target_sev = severity_by_scan.get(target_scan_id, {})
    base_lic = license_by_scan.get(base_scan_id, {})
    target_lic = license_by_scan.get(target_scan_id, {})

    base_risk = _compute_risk_score(base_sev, base_lic)
    target_risk = _compute_risk_score(target_sev, target_lic)

    base_gate = await _gate_status(session, project_id=project_id, scan_id=base_scan_id)
    target_gate = (
        base_gate
        if same_scan
        else await _gate_status(session, project_id=project_id, scan_id=target_scan_id)
    )

    summary = {
        "risk_score": {"base": base_risk, "target": target_risk},
        "severity": _severity_summary_pair(base_sev, target_sev),
        "gate": {"base": base_gate, "target": target_gate},
        "component_count": {
            "base": component_count_by_scan.get(base_scan_id, 0),
            "target": component_count_by_scan.get(target_scan_id, 0),
        },
    }

    # ---- change sets (empty by construction when base == target) ----
    components: dict[str, list[dict[str, Any]]]
    vulnerabilities: dict[str, list[dict[str, Any]]]
    if same_scan:
        components = {"added": [], "removed": [], "changed": []}
        vulnerabilities = {"introduced": [], "resolved": []}
        truncated = False
    else:
        base_components = await _components_by_package(session, scan_id=base_scan_id)
        target_components = await _components_by_package(session, scan_id=target_scan_id)
        added, removed, changed, comp_trunc = _diff_components(
            base_components, target_components
        )

        base_findings = await _open_findings_by_key(session, scan_id=base_scan_id)
        target_findings = await _open_findings_by_key(session, scan_id=target_scan_id)
        introduced, resolved, vuln_trunc = _diff_vulnerabilities(
            base_findings, target_findings
        )

        components = {"added": added, "removed": removed, "changed": changed}
        vulnerabilities = {"introduced": introduced, "resolved": resolved}
        truncated = comp_trunc or vuln_trunc

    licenses = {"category_delta": _license_category_pair(base_lic, target_lic)}

    return {
        "base": {
            "scan_id": base_scan_id,
            "release": _release_label(base_scan),
            "created_at": base_scan.created_at,
        },
        "target": {
            "scan_id": target_scan_id,
            "release": _release_label(target_scan),
            "created_at": target_scan.created_at,
        },
        "summary": summary,
        "components": components,
        "vulnerabilities": vulnerabilities,
        "licenses": licenses,
        "truncated": truncated,
    }


__all__ = ["diff_release_snapshots"]
