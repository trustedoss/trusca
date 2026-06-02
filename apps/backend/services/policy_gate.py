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

Dynamic license policy (opt-in per team/org, v2.2 Track C — c2)
--------------------------------------------------------------
When the project's owning team has an EFFECTIVE, ENABLED license policy
(``services.license_policy_service.get_effective_policy``: team > org-default >
none), the forbidden-license count is computed DYNAMICALLY: each component's
license expression is re-classified through the policy
(overrides / exceptions / compound-operator strategy / unknown posture) by the
hardened compound-SPDX evaluator (``services.license_expression``), and the gate
counts distinct components whose expression resolves to ``forbidden`` —
INSTEAD of the static ``licenses.category == 'forbidden'`` stored at scan time.

When there is NO effective policy (none authored, or disabled at every scope),
the forbidden-license count is computed by the original SQL path against the
PERSISTED ``licenses.category``: the no-policy behaviour is **byte-for-byte
identical** to the pre-c2 contract (the golden ``test_policy_gate`` cases prove
this — they create no policy and must stay green). ``GateResult``'s shape is
unchanged; ``forbidden_license_count`` simply reflects the dynamic count when a
policy is active.

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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import structlog
from sqlalchemy import String, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    ComponentVersion,
    LicenseFinding,
    LicensePolicy,
    Project,
    VulnerabilityFinding,
)
from models import (
    License as LicenseModel,
)
from models import (
    Vulnerability as VulnerabilityModel,
)
from services.license_expression import evaluate_expression
from services.license_policy_service import effective_category, get_effective_policy
from services.scan_resolution import latest_succeeded_scan_id

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
    # Reachability surfacing (v2.3 r2). ``reachable_critical_cve_count`` is a
    # SUBSET of ``critical_cve_count``: open critical findings that an analyser
    # has additionally proven REACHABLE (``reachable IS TRUE``). It is a priority
    # signal the gate result / SCA comment surface so a reviewer can see "of the
    # N blocking criticals, M sit on a real call path" — it does NOT, by itself,
    # change the pass/fail verdict. ``reachable_gate_enforced`` reflects whether
    # the opt-in ``GATE_REACHABLE_CRITICAL_ONLY`` mode was active for this
    # evaluation (default False → legacy behaviour, see ``evaluate_gate``).
    # Defaults keep every existing keyword-construction call site valid.
    reachable_critical_cve_count: int = 0
    reachable_gate_enforced: bool = False
    # Whether the opt-in relaxation ACTUALLY took effect for this evaluation.
    # ``reachable_gate_enforced`` only reflects that the env flag was SET; the
    # relaxation additionally requires the scan's open criticals to have been
    # reachability-analysed (see ``evaluate_gate`` safe-by-default fallback). On a
    # non-Go / un-analysed scan ``reachable_gate_enforced`` is True but
    # ``reachable_relaxation_applied`` is False — the gate ran at full strength.
    # Consumers (SCA comment) use this to render an accurate advisory.
    reachable_relaxation_applied: bool = False


# The latest-succeeded-scan resolver was PROMOTED to ``services.scan_resolution``
# so the build gate and every current-state display reader (overview / vuln list
# / license / obligation / source tree) share ONE definition and cannot drift
# (see that module's docstring for the bug this prevents). ``_latest_succeeded_scan_id``
# is kept as a thin re-export so existing in-tree callers / tests keep working.
_latest_succeeded_scan_id = latest_succeeded_scan_id


@dataclass(frozen=True)
class _CriticalReachabilityCounts:
    """Reachability breakdown of a scan's OPEN critical findings, one query.

    All four counts share the same population: open (``status NOT IN
    _CLOSED_FINDING_STATUSES``) ``severity == 'critical'`` findings on one scan.

    * ``total`` — every open critical (the legacy blocking population).
    * ``reachable`` — ``reachable IS TRUE`` (analyser PROVED a real call path).
    * ``analysed`` — ``reachable IS NOT NULL`` (the analyser produced a verdict
      either way — TRUE or FALSE). This is the safe-by-default gate: the
      reachable-only RELAXATION only ever applies when ``analysed > 0``, i.e. the
      scan's ecosystem was actually reachability-analysed. Reachability is
      Go-only today, so a non-Go scan has ``analysed == 0`` and the relaxation is
      a no-op there (the gate stays full-strength).
    * ``unreachable`` — ``reachable IS FALSE`` (analyser PROVED no call path).
      The ONLY findings the relaxation suppresses. NULL (not analysed) is NEVER
      suppressed: an unanalysed finding is treated conservatively as "could be
      reachable" and keeps blocking.

    Invariant: ``reachable + unreachable == analysed`` and
    ``analysed <= total``. One round-trip via conditional aggregation — no N+1.
    """

    total: int
    reachable: int
    analysed: int
    unreachable: int


async def _critical_reachability_counts(
    session: AsyncSession,
    scan_id: uuid.UUID,
) -> _CriticalReachabilityCounts:
    """Compute the open-critical reachability breakdown for ``scan_id`` in ONE query.

    Uses conditional aggregation (``COUNT(*) FILTER``-style ``case`` sums) so the
    total / reachable / analysed / unreachable counts come back from a single
    indexed scan rather than four separate round-trips (no N+1). The base
    predicate is the legacy open-critical population — open
    (``status NOT IN _CLOSED_FINDING_STATUSES``) ``severity == 'critical'`` —
    so ``total`` here is byte-for-byte the pre-r2 blocking count, keeping the
    legacy gate population and the reachability breakdown provably consistent.

    NULL handling is deliberate and tri-state-safe:
      * ``reachable`` counts ``reachable IS TRUE`` only.
      * ``unreachable`` counts ``reachable IS FALSE`` only.
      * ``analysed`` counts ``reachable IS NOT NULL`` (TRUE or FALSE).
      * NULL (not analysed) contributes to ``total`` but to NONE of the others —
        it is never treated as reachable OR unreachable.
    """
    stmt = (
        select(
            func.count(),
            func.coalesce(
                func.sum(
                    case((VulnerabilityFinding.reachable.is_(True), 1), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case((VulnerabilityFinding.reachable.isnot(None), 1), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case((VulnerabilityFinding.reachable.is_(False), 1), else_=0)
                ),
                0,
            ),
        )
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
    row = (await session.execute(stmt)).one()
    return _CriticalReachabilityCounts(
        total=int(row[0]),
        reachable=int(row[1]),
        analysed=int(row[2]),
        unreachable=int(row[3]),
    )


def _resolve_reachable_critical_only() -> bool:
    """Read the opt-in ``GATE_REACHABLE_CRITICAL_ONLY`` flag at evaluation time.

    Default OFF (returns False) → the critical-CVE gate condition is unchanged:
    ANY open critical CVE blocks, regardless of reachability. This preserves the
    legacy contract byte-for-byte (the golden ``test_policy_gate`` cases author
    no reachability and must stay green).

    When set to a truthy value (``1``/``true``/``yes``/``on``, case-insensitive)
    the critical condition is RELAXED: open critical findings the analyser PROVED
    UNREACHABLE (``reachable IS FALSE``) no longer block the build. This is a
    deliberate relaxation an operator opts into ("don't fail my build for a
    critical CVE that isn't on a real call path"), so it is gated behind an
    explicit env flag and never silently enabled. Read inside the function per
    CLAUDE.md core rule #11.

    SAFE-BY-DEFAULT FALLBACK (security-reviewer fix-first, Medium #1)
    ----------------------------------------------------------------
    This flag is GLOBAL but reachability is currently a Go-only signal. On a
    non-Go scan every finding is ``reachable IS NULL`` (never analysed). A naive
    "block only reachable-TRUE" relaxation would then yield an EMPTY blocking set
    on those scans — silently DISABLING the gate for whole ecosystems. To prevent
    that foot-gun, the actual relaxation in :func:`evaluate_gate` applies ONLY when
    the scan's open criticals were actually reachability-analysed (at least one
    ``reachable IS NOT NULL``); otherwise the gate falls back to the full legacy
    blocking population. And even when it applies, ONLY proven-unreachable
    (``reachable IS FALSE``) criticals are excluded — NULL (not analysed) stays
    BLOCKING, so an unanalysed finding is never silently treated as unreachable.
    See :class:`_CriticalReachabilityCounts` and ``evaluate_gate``.

    Important: this flag never STRENGTHENS the gate — it can only shrink the set
    of blocking criticals, and only by removing PROVEN-unreachable findings on a
    scan that was actually analysed. With it off, behaviour is identical to
    before r2.
    """
    raw = os.getenv("GATE_REACHABLE_CRITICAL_ONLY")
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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

    STATIC (no-policy) path — counts against the PERSISTED
    ``licenses.category == 'forbidden'`` stored at scan time. This is the
    byte-for-byte legacy contract; the golden ``test_policy_gate`` cases pin it.

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


def _static_default_for(spdx_id: str) -> str:
    """Return the static-catalog category for a SINGLE simple SPDX id.

    Lazily imports ``tasks.scan_source._LICENSE_CATEGORY_DEFAULTS`` so the heavy
    Celery scan-pipeline module is NOT pulled into the gate's import graph on the
    common (no-policy) path — this lookup only runs when a policy is active. A
    catalog-miss returns ``"unknown"`` so the policy's unknown-posture applies.
    """
    from tasks.scan_source import _LICENSE_CATEGORY_DEFAULTS

    return _LICENSE_CATEGORY_DEFAULTS.get(spdx_id, "unknown")


def _make_policy_resolver(
    policy: LicensePolicy, component_purl: str | None = None
) -> Callable[[str], str]:
    """Build a single-id resolver that applies *policy* then the static catalog.

    The returned callable is passed to
    :func:`services.license_expression.evaluate_expression` as ``resolve_id``: it
    receives ONE operand token and returns its category. It reuses c1's pure
    :func:`services.license_policy_service.effective_category` (override >
    exception > static catalog > unknown posture) so the single-id semantics are
    shared and cannot drift.

    c3: ``component_purl`` is threaded through so a **component-scoped** exception
    ("waive this exact component") is honoured. Org/team-wide exceptions apply
    regardless; a component-scoped one applies only when its purl matches.
    """

    def _resolve(spdx_id: str) -> str:
        return effective_category(
            spdx_id,
            policy,
            _static_default_for(spdx_id),
            component_purl=component_purl,
        )

    return _resolve


async def _load_scan_license_rows(
    session: AsyncSession,
    scan_id: uuid.UUID,
) -> list[tuple[uuid.UUID, str | None, str | None]]:
    """Batch-load DISTINCT ``(component_version_id, spdx_id, purl)`` for a scan.

    One query — avoids the N+1 of re-fetching each component's license. The
    DISTINCT collapses the (declared/concluded/detected, multi-file) duplicate
    finding rows the static SQL also collapses, so the dynamic count is computed
    over the same logical set. ``spdx_id`` may be NULL (ORT custom LicenseRef-*),
    which the evaluator treats as an empty/unknown expression. ``purl`` is the
    component's ``purl_with_version`` — carried so a component-scoped exception
    (c3) can be matched against the exact component being evaluated.
    """
    stmt = (
        select(
            LicenseFinding.component_version_id,
            LicenseModel.spdx_id,
            ComponentVersion.purl_with_version,
        )
        .select_from(LicenseFinding)
        .join(LicenseModel, LicenseModel.id == LicenseFinding.license_id)
        .join(
            ComponentVersion,
            ComponentVersion.id == LicenseFinding.component_version_id,
        )
        .where(LicenseFinding.scan_id == scan_id)
        .distinct()
    )
    result = await session.execute(stmt)
    return [(row[0], row[1], row[2]) for row in result.all()]


async def _count_forbidden_license_components_dynamic(
    session: AsyncSession,
    scan_id: uuid.UUID,
    policy: LicensePolicy,
) -> int:
    """Count components whose license resolves to ``forbidden`` under *policy*.

    Re-classifies each component's stored license expression through the policy
    (overrides / exceptions / compound-operator strategy / unknown posture) via
    the hardened compound-SPDX evaluator, then counts DISTINCT
    ``component_version_id`` with at least one expression resolving to
    ``forbidden``. Replaces the static SQL count when a policy is active.

    Performance: one batched query loads the (cv, expression) pairs; the
    evaluation is pure CPU over a memoised cache so a license shared by many
    components is classified once. The evaluator is hardened against adversarial
    expressions (length/depth/token bounds; un-parseable → the policy's unknown
    posture), so a hostile SBOM can never hang or crash the gate.
    """
    rows = await _load_scan_license_rows(session, scan_id)
    strategy = dict(policy.compound_operator_strategy or {})
    unknown_posture = policy.unknown_license_category

    # Memoise per distinct (expression, purl) — the same license string recurs
    # across many components, and the purl only changes the verdict for the rare
    # component-scoped exception, so the cache still collapses the common case.
    cache: dict[tuple[str | None, str | None], str] = {}
    forbidden_cv_ids: set[uuid.UUID] = set()

    for cv_id, expression, purl in rows:
        key = (expression, purl)
        if key in cache:
            category = cache[key]
        else:
            resolver = _make_policy_resolver(policy, component_purl=purl)
            category = evaluate_expression(
                expression,
                resolve_id=resolver,
                strategy=strategy,
                unknown_category=unknown_posture,
            ).category
            cache[key] = category
        if category == "forbidden":
            forbidden_cv_ids.add(cv_id)

    return len(forbidden_cv_ids)


async def _team_id_for_project(
    session: AsyncSession,
    project_id: uuid.UUID,
) -> uuid.UUID | None:
    """Return the owning ``team_id`` for *project_id*, or None if it is gone."""
    return (
        await session.execute(select(Project.team_id).where(Project.id == project_id))
    ).scalar_one_or_none()


async def _resolve_forbidden_license_count(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    scan_id: uuid.UUID,
) -> int:
    """Forbidden-license count, dynamic under an effective policy else static.

    Looks up the project's owning team and resolves its EFFECTIVE, ENABLED
    license policy ONCE (team > org-default > none). When a policy applies the
    count is computed dynamically through the compound-SPDX evaluator; otherwise
    it falls back to the original static SQL path — which is byte-for-byte the
    pre-c2 contract (no policy lookups change the result, only an extra cheap
    SELECT of the project's team_id and the policy rows happens, and a missing
    project / no policy yields the identical static count).
    """
    team_id = await _team_id_for_project(session, project_id)
    if team_id is None:
        # Project vanished mid-evaluation (or a synthetic id): no team → no
        # policy → static path. Defensive; evaluate_gate already found a scan.
        return await _count_forbidden_license_components(session, scan_id)

    policy = await get_effective_policy(session, team_id=team_id)
    if policy is None:
        # No enabled policy at any scope → byte-identical legacy static path.
        return await _count_forbidden_license_components(session, scan_id)

    return await _count_forbidden_license_components_dynamic(session, scan_id, policy)


def _build_reason(
    critical_cve_count: int,
    forbidden_license_count: int,
    epss_gate_count: int = 0,
    epss_threshold: float | None = None,
    *,
    reachable_critical_only: bool = False,
) -> str | None:
    """Compose the human-readable ``reason`` field. ``None`` on pass.

    The EPSS clause is appended only when the EPSS gate is active
    (``epss_threshold`` is not None) AND at least one finding tripped it, so a
    disabled or passing EPSS gate leaves the legacy reason text untouched.

    ``reachable_critical_only`` only changes the WORDING of the critical clause
    (it reads "reachable critical CVE(s)" so the reviewer knows the count was
    narrowed to reachable findings). The counting is done by the caller; with the
    flag off the legacy "critical CVE(s) detected" wording is byte-for-byte
    preserved.
    """
    parts: list[str] = []
    if critical_cve_count > 0:
        noun = "reachable critical" if reachable_critical_only else "critical"
        parts.append(
            f"{critical_cve_count} {noun} "
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
    *,
    scan_id: uuid.UUID | None = None,
) -> GateResult:
    """Compute the build-gate verdict for ``project_id``.

    The function is a pure read against the live session — no auth check
    happens here. Callers (HTTP routers, Celery tasks) are responsible for
    asserting team access before invoking it.

    ``scan_id`` (feature #28) optionally pins the verdict to a SPECIFIC
    pre-resolved succeeded scan instead of the project's latest succeeded scan.
    The CALLER is responsible for validating that the scan belongs to this
    project and is succeeded (the HTTP layer does so via
    :func:`services.scan_resolution.resolve_snapshot_scan_id` before calling).
    When ``None`` (the default, used by CI's "latest verdict" contract) the
    latest succeeded scan is resolved here exactly as before.
    """
    if scan_id is None:
        scan_id = await _latest_succeeded_scan_id(session, project_id)
    evaluated_at = datetime.now(tz=UTC)

    # Read the EPSS threshold once per evaluation (None when the gate is
    # disabled). We surface it in the result meta even on the no-scan path so
    # callers can render "EPSS gate: 0.5 (no signal)" consistently.
    epss_threshold = _resolve_epss_threshold()
    # Opt-in reachable-only critical mode (default OFF → legacy behaviour).
    reachable_critical_only = _resolve_reachable_critical_only()

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
            reachable_critical_cve_count=0,
            reachable_gate_enforced=reachable_critical_only,
            # No scan → nothing analysed → the relaxation can never have applied.
            reachable_relaxation_applied=False,
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
            reachable_critical_cve_count=0,
            reachable_gate_enforced=reachable_critical_only,
            reachable_relaxation_applied=False,
            reason=None,
        )
        return result

    # One round-trip: total / reachable / analysed / unreachable open criticals.
    crit = await _critical_reachability_counts(session, scan_id)
    all_critical_cve_count = crit.total
    # Always surfaced (subset signal), independent of the opt-in mode.
    reachable_critical_cve_count = crit.reachable
    forbidden_license_count = await _resolve_forbidden_license_count(
        session, project_id=project_id, scan_id=scan_id
    )
    epss_gate_count = (
        await _count_open_epss_findings(session, scan_id, epss_threshold)
        if epss_threshold is not None
        else 0
    )

    # The critical count that DRIVES the verdict.
    #
    # Default (flag off): every open critical blocks — identical to the pre-r2
    # contract.
    #
    # Opt-in (flag on) with SAFE-BY-DEFAULT FALLBACK: the relaxation applies ONLY
    # when this scan's open criticals were actually reachability-analysed
    # (``crit.analysed > 0`` — true on Go scans, false on never-analysed
    # ecosystems like a pure-npm/PyPI scan whose findings are all NULL). When it
    # applies, we exclude ONLY proven-unreachable (``reachable IS FALSE``)
    # criticals; NULL (not analysed) stays blocking. When it does NOT apply
    # (analysed == 0), we fall back to the full legacy blocking population so the
    # gate is never silently disabled on an un-analysed ecosystem.
    relaxation_applies = reachable_critical_only and crit.analysed > 0
    if relaxation_applies:
        # total - unreachable == (reachable + null). NULL kept conservatively.
        blocking_critical_count = all_critical_cve_count - crit.unreachable
    else:
        blocking_critical_count = all_critical_cve_count

    # The relaxation actually SUPPRESSED at least one otherwise-blocking critical.
    # This is an operational signal (the gate verdict was softened from what the
    # legacy gate would have produced), so it is WARNING, not INFO.
    relaxation_suppressed = (
        relaxation_applies
        and all_critical_cve_count > 0
        and blocking_critical_count < all_critical_cve_count
    )
    if relaxation_suppressed:
        log.warning(
            "policy_gate.reachable_relaxation_suppressed_criticals",
            project_id=str(project_id),
            scan_id=str(scan_id),
            all_critical_cve_count=all_critical_cve_count,
            blocking_critical_count=blocking_critical_count,
            unreachable_critical_count=crit.unreachable,
            reachable_critical_cve_count=reachable_critical_cve_count,
            analysed_critical_count=crit.analysed,
        )

    # Reason wording reflects the narrowed population only when the relaxation is
    # actually in effect for this scan (analysed). On the safe-fallback path the
    # legacy "critical" wording is preserved — the operator turned the flag on but
    # it had no effect, so we must not claim "reachable critical".
    reason = _build_reason(
        blocking_critical_count,
        forbidden_license_count,
        epss_gate_count,
        epss_threshold,
        reachable_critical_only=relaxation_applies,
    )
    # BUGHUNTER-GOLDEN(GOLD-P6-003): 카운트와 무관하게 항상 fail — 게이트 결정이 카운트와 모순
    gate: GateOutcome = "fail"  # noqa: was "fail" if reason is not None else "pass"

    result = GateResult(
        gate=gate,
        reason=reason,
        # ``critical_cve_count`` reflects the count the gate ACTED ON so existing
        # consumers (SCA comment, CI) read a count consistent with the verdict.
        # The full population is recoverable as reachable + (the rest); the
        # always-present ``reachable_critical_cve_count`` is the subset signal.
        critical_cve_count=blocking_critical_count,
        forbidden_license_count=forbidden_license_count,
        project_id=project_id,
        scan_id=scan_id,
        evaluated_at=evaluated_at,
        epss_gate_count=epss_gate_count,
        epss_threshold=epss_threshold,
        reachable_critical_cve_count=reachable_critical_cve_count,
        reachable_gate_enforced=reachable_critical_only,
        reachable_relaxation_applied=relaxation_applies,
    )
    log.info(
        "policy_gate.evaluated",
        project_id=str(project_id),
        gate=result.gate,
        scan_id=str(scan_id),
        critical_cve_count=blocking_critical_count,
        all_critical_cve_count=all_critical_cve_count,
        reachable_critical_cve_count=reachable_critical_cve_count,
        analysed_critical_count=crit.analysed,
        unreachable_critical_count=crit.unreachable,
        reachable_gate_enforced=reachable_critical_only,
        reachable_relaxation_applied=relaxation_applies,
        forbidden_license_count=forbidden_license_count,
        epss_gate_count=epss_gate_count,
        epss_threshold=epss_threshold,
        reason=reason,
    )
    return result


__all__ = ["GateOutcome", "GateResult", "evaluate_gate"]
