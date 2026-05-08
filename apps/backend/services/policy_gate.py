"""
Policy gate service — Phase 5 PR #17.

The build-blocking decision the CI pipeline asks the portal to make. Given a
project, the service:

  1. Looks up the most recent ``status='succeeded'`` scan.
  2. Counts critical CVEs that are still open (``status NOT IN
     ('not_affected', 'fixed', 'false_positive')``).
  3. Counts component_versions that carry at least one license with
     ``category='forbidden'``.
  4. Returns ``gate='fail'`` if either count is positive, ``gate='pass'``
     otherwise.

A project that has never had a successful scan returns ``gate='pass'`` with
``scan_id=None``: we deliberately do not block builds for "no signal" because
the first PR a team opens against a brand-new project would otherwise fail
before a scan has even been requested. Operators who want stricter behaviour
can add a separate "must have a scan" gate in their CI.

Authorization
-------------
This module is **purely** a DB read — auth/IDOR checks happen in the router
layer that calls into it. Keeping the service auth-free makes it trivially
testable and allows future internal callers (Celery tasks computing gate
deltas for notifications) to reuse it without faking a CurrentUser.

Logging
-------
Every evaluation emits a single ``policy_gate.evaluated`` log line carrying
``project_id``, ``gate``, ``critical_cve_count``, ``forbidden_license_count``,
and ``scan_id``. No PII or credential material flows through this surface,
so no masking is required — but we still avoid logging the project name or
git URL, which the team may consider sensitive.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import structlog
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    License as LicenseModel,
)
from models import (
    LicenseFinding,
    Scan,
    VulnerabilityFinding,
)
from models import (
    Vulnerability as VulnerabilityModel,
)

log = structlog.get_logger("policy_gate.service")

GateOutcome = Literal["pass", "fail"]

# Vulnerability finding statuses that mean the finding is no longer
# actionable from a release-gating perspective. ``suppressed`` is NOT in
# this set: a suppressed critical CVE is still open work for the team.
_CLOSED_FINDING_STATUSES: tuple[str, ...] = ("not_affected", "fixed", "false_positive")


@dataclass(frozen=True)
class GateResult:
    """Verdict computed by :func:`evaluate_gate`.

    ``frozen=True`` so the result cannot be mutated after construction —
    this matters for callers that want to share the dataclass between the
    HTTP response and the SCA-comment builder without defensive copies.
    """

    gate: GateOutcome
    reason: str | None
    critical_cve_count: int
    forbidden_license_count: int
    project_id: uuid.UUID
    scan_id: uuid.UUID | None
    evaluated_at: datetime


async def _latest_succeeded_scan_id(
    session: AsyncSession,
    project_id: uuid.UUID,
) -> uuid.UUID | None:
    """Return the ID of the most recent ``status='succeeded'`` scan, or None.

    We deliberately do NOT use ``Project.latest_scan_id`` here: that pointer
    is updated on every trigger (queued/running/failed), so a project whose
    last attempt failed would otherwise be evaluated against a non-succeeded
    scan and produce a noisy ``gate='fail'``. Querying ``scans`` directly,
    ordered by ``created_at DESC`` and clamped to ``status='succeeded'``,
    gives the contract every caller wants. The compound index
    ``ix_scans_project_created_at`` covers this access path.
    """
    stmt = (
        select(Scan.id)
        .where(Scan.project_id == project_id)
        .where(cast(Scan.status, String) == "succeeded")
        .order_by(Scan.created_at.desc(), Scan.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _count_open_critical_cves(
    session: AsyncSession,
    scan_id: uuid.UUID,
) -> int:
    """Count critical-severity findings on ``scan_id`` that are still open.

    "Open" = ``vulnerability_findings.status NOT IN
    ('not_affected', 'fixed', 'false_positive')``. ``suppressed`` IS counted
    because suppressing a critical CVE without a justification (the
    vulnerability triage UI requires one) is itself a release-blocking
    signal — we'd rather over-count than miss.
    """
    stmt = (
        select(func.count())
        .select_from(VulnerabilityFinding)
        .join(
            VulnerabilityModel,
            VulnerabilityModel.id == VulnerabilityFinding.vulnerability_id,
        )
        .where(VulnerabilityFinding.scan_id == scan_id)
        .where(cast(VulnerabilityModel.severity, String) == "critical")
        .where(
            cast(VulnerabilityFinding.status, String).notin_(_CLOSED_FINDING_STATUSES),
        )
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def _count_forbidden_license_components(
    session: AsyncSession,
    scan_id: uuid.UUID,
) -> int:
    """Count component_versions on ``scan_id`` that carry a forbidden license.

    A single component_version may have several license_findings rows
    (declared + concluded + detected, multiple files, ...). We collapse to
    DISTINCT ``component_version_id`` so the count answers "how many
    components in this build are blocked", not "how many license rows".
    """
    stmt = (
        select(func.count(func.distinct(LicenseFinding.component_version_id)))
        .select_from(LicenseFinding)
        .join(LicenseModel, LicenseModel.id == LicenseFinding.license_id)
        .where(LicenseFinding.scan_id == scan_id)
        .where(cast(LicenseModel.category, String) == "forbidden")
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


def _build_reason(critical_cve_count: int, forbidden_license_count: int) -> str | None:
    """Compose the human-readable ``reason`` field. ``None`` on pass."""
    parts: list[str] = []
    if critical_cve_count > 0:
        parts.append(
            f"{critical_cve_count} critical "
            f"{'CVE' if critical_cve_count == 1 else 'CVEs'} detected",
        )
    if forbidden_license_count > 0:
        parts.append(
            f"{forbidden_license_count} forbidden-licensed "
            f"{'component' if forbidden_license_count == 1 else 'components'} detected",
        )
    if not parts:
        return None
    return "; ".join(parts)


async def evaluate_gate(
    session: AsyncSession,
    project_id: uuid.UUID,
) -> GateResult:
    """Compute the build-gate verdict for ``project_id``.

    The function is a pure read against the live session — no auth check
    happens here. Callers (HTTP routers, Celery tasks) are responsible for
    asserting team access before invoking it.
    """
    scan_id = await _latest_succeeded_scan_id(session, project_id)
    evaluated_at = datetime.now(tz=UTC)

    if scan_id is None:
        # No signal: we explicitly pass. See module docstring.
        result = GateResult(
            gate="pass",
            reason=None,
            critical_cve_count=0,
            forbidden_license_count=0,
            project_id=project_id,
            scan_id=None,
            evaluated_at=evaluated_at,
        )
        log.info(
            "policy_gate.evaluated",
            project_id=str(project_id),
            gate=result.gate,
            scan_id=None,
            critical_cve_count=0,
            forbidden_license_count=0,
            reason=None,
        )
        return result

    critical_cve_count = await _count_open_critical_cves(session, scan_id)
    forbidden_license_count = await _count_forbidden_license_components(session, scan_id)

    reason = _build_reason(critical_cve_count, forbidden_license_count)
    gate: GateOutcome = "fail" if reason is not None else "pass"

    result = GateResult(
        gate=gate,
        reason=reason,
        critical_cve_count=critical_cve_count,
        forbidden_license_count=forbidden_license_count,
        project_id=project_id,
        scan_id=scan_id,
        evaluated_at=evaluated_at,
    )
    log.info(
        "policy_gate.evaluated",
        project_id=str(project_id),
        gate=result.gate,
        scan_id=str(scan_id),
        critical_cve_count=critical_cve_count,
        forbidden_license_count=forbidden_license_count,
        reason=reason,
    )
    return result


__all__ = ["GateOutcome", "GateResult", "evaluate_gate"]
