"""
Policy gate service — Phase 5 PR #17.

The build-blocking decision the CI pipeline asks the portal to make. Given a
project, the service:

  1. Looks up the most recent ``status='succeeded'`` scan.
  2. Counts critical CVEs that are still open (``status NOT IN
     ('not_affected', 'fixed', 'false_positive')``).
  3. Counts component_versions that carry at least one license with
     ``category='forbidden'``.
  4. Optionally counts open findings whose CVE carries an EPSS score at or
     above ``GATE_EPSS_THRESHOLD`` (see below).
  5. Returns ``gate='fail'`` if any of these counts is positive,
     ``gate='pass'`` otherwise.

EPSS gate (opt-in, env-driven)
------------------------------
``GATE_EPSS_THRESHOLD`` is read at evaluation time (CLAUDE.md core rule #11,
no module-level caching). When **unset or unparseable**, the EPSS condition is
disabled and the gate behaves EXACTLY as before — ``epss_gate_count`` is 0 and
``epss_threshold`` is None, so the critical/forbidden contract is byte-for-byte
preserved. When set to a float in [0, 1], the service counts open findings
(same "open" status set as the critical-CVE rule) whose CVE has
``epss_score >= threshold`` (NULL EPSS excluded), and a positive count fails
the gate. Dynamic per-policy thresholds are v2.2; today this is env-only.

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

import os
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
    # EPSS gate (opt-in). When ``epss_threshold`` is None the EPSS condition
    # was disabled (env unset/unparseable) and ``epss_gate_count`` is 0 — the
    # legacy critical/forbidden contract is unchanged. Defaults keep every
    # existing keyword-construction call site (and pickled/old callers) valid.
    epss_gate_count: int = 0
    epss_threshold: float | None = None


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


def _resolve_epss_threshold() -> float | None:
    """Read ``GATE_EPSS_THRESHOLD`` at evaluation time, or None if disabled.

    Returns None when the env var is unset, empty, unparseable, or outside the
    closed [0, 1] EPSS range. Returning None disables the EPSS condition and
    preserves the legacy gate behaviour exactly — a misconfigured threshold
    must never silently *relax* the gate, but it also must never crash the
    build-gate evaluation, so we fail safe to "EPSS disabled" and log a
    warning. Read inside the function per CLAUDE.md core rule #11.
    """
    raw = os.getenv("GATE_EPSS_THRESHOLD")
    if raw is None or raw.strip() == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        log.warning("policy_gate.epss_threshold_unparseable", raw=raw)
        return None
    if not (0.0 <= value <= 1.0):
        log.warning("policy_gate.epss_threshold_out_of_range", value=value)
        return None
    return value


async def _count_open_epss_findings(
    session: AsyncSession,
    scan_id: uuid.UUID,
    threshold: float,
) -> int:
    """Count open findings on ``scan_id`` whose CVE has ``epss_score >= threshold``.

    "Open" reuses the same status set as the critical-CVE rule so the two
    conditions are consistent: a finding the team has already dispositioned
    (not_affected / fixed / false_positive) does not contribute to either gate.
    NULL ``epss_score`` is excluded by the ``>=`` comparison (NULL >= x is NULL),
    which is the intended semantic: a CVE with no published EPSS cannot trip an
    EPSS-probability gate.
    """
    stmt = (
        select(func.count())
        .select_from(VulnerabilityFinding)
        .join(
            VulnerabilityModel,
            VulnerabilityModel.id == VulnerabilityFinding.vulnerability_id,
        )
        .where(VulnerabilityFinding.scan_id == scan_id)
        .where(VulnerabilityModel.epss_score >= threshold)
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


def _build_reason(
    critical_cve_count: int,
    forbidden_license_count: int,
    epss_gate_count: int = 0,
    epss_threshold: float | None = None,
) -> str | None:
    """Compose the human-readable ``reason`` field. ``None`` on pass.

    The EPSS clause is appended only when the EPSS gate is active
    (``epss_threshold`` is not None) AND at least one finding tripped it, so a
    disabled or passing EPSS gate leaves the legacy reason text untouched.
    """
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
    if epss_threshold is not None and epss_gate_count > 0:
        parts.append(
            f"{epss_gate_count} open "
            f"{'CVE' if epss_gate_count == 1 else 'CVEs'} with EPSS >= {epss_threshold:g}",
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

    # Read the EPSS threshold once per evaluation (None when the gate is
    # disabled). We surface it in the result meta even on the no-scan path so
    # callers can render "EPSS gate: 0.5 (no signal)" consistently.
    epss_threshold = _resolve_epss_threshold()

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
            epss_gate_count=0,
            epss_threshold=epss_threshold,
        )
        log.info(
            "policy_gate.evaluated",
            project_id=str(project_id),
            gate=result.gate,
            scan_id=None,
            critical_cve_count=0,
            forbidden_license_count=0,
            epss_gate_count=0,
            epss_threshold=epss_threshold,
            reason=None,
        )
        return result

    critical_cve_count = await _count_open_critical_cves(session, scan_id)
    forbidden_license_count = await _count_forbidden_license_components(session, scan_id)
    epss_gate_count = (
        await _count_open_epss_findings(session, scan_id, epss_threshold)
        if epss_threshold is not None
        else 0
    )

    reason = _build_reason(
        critical_cve_count,
        forbidden_license_count,
        epss_gate_count,
        epss_threshold,
    )
    gate: GateOutcome = "fail" if reason is not None else "pass"

    result = GateResult(
        gate=gate,
        reason=reason,
        critical_cve_count=critical_cve_count,
        forbidden_license_count=forbidden_license_count,
        project_id=project_id,
        scan_id=scan_id,
        evaluated_at=evaluated_at,
        epss_gate_count=epss_gate_count,
        epss_threshold=epss_threshold,
    )
    log.info(
        "policy_gate.evaluated",
        project_id=str(project_id),
        gate=result.gate,
        scan_id=str(scan_id),
        critical_cve_count=critical_cve_count,
        forbidden_license_count=forbidden_license_count,
        epss_gate_count=epss_gate_count,
        epss_threshold=epss_threshold,
        reason=reason,
    )
    return result


__all__ = ["GateOutcome", "GateResult", "evaluate_gate"]
