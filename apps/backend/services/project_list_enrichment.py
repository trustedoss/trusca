"""
Project-list row enrichment — batched scan-status + severity-summary + counts.

The project-list endpoint (``GET /v1/projects``) used to return bare project
rows, so the UI rendered every project as "Idle" with no risk indicator. This
module enriches a *page* of project rows with derived fields:

  - ``latest_scan_status`` — the status of each project's latest scan *attempt*
    (the scan denormalized into ``Project.latest_scan_id``). Drives the row's
    status badge; ``None`` (→ "Idle") when the project has never been scanned.

  - ``severity_summary`` — vulnerability-severity *component* counts from each
    project's latest *succeeded* scan (the same anchor the overview / build gate
    use via ``services.scan_resolution.latest_succeeded_scan_id``). Drives the
    per-row risk indicator; ``None`` when the project has no succeeded scan.

  - ``scan_count`` / ``release_count`` / ``last_scan_at`` (W3 #30) — list-row
    discoverability aggregates. ``scan_count`` is total scan attempts (any
    status), ``release_count`` is succeeded-scan count (the "release" model in
    tracker §0.5: every succeeded scan IS a release snapshot), and
    ``last_scan_at`` is the most-recent scan attempt's timestamp (regardless of
    status). All three are produced by a SINGLE batched GROUP BY query.

Why these two anchor on DIFFERENT scans
----------------------------------------
``latest_scan_id`` tracks the last *attempt* (queued/running/succeeded/failed/
cancelled), so it is the right anchor for the status badge but the WRONG anchor
for current SCA posture: a project whose newest attempt FAILED still carries
valid findings from its last SUCCEEDED scan. So the status badge reads the
latest-attempt scan while the severity summary reads the latest-succeeded scan —
exactly the split ``services.scan_resolution`` documents. A project can therefore
show ``latest_scan_status="failed"`` AND a non-null ``severity_summary`` at once.

Efficiency (the whole point — DoD: up to 100 rows per page, no N+1)
------------------------------------------------------------------
Every field is computed in BATCHED queries over the page's project ids:

  1. one query joining ``scans`` by the page's ``latest_scan_id`` set → a
     ``{project_id: status}`` map (the status-badge source);
  2. one ``DISTINCT ON`` query resolving the latest-succeeded scan id per
     project (mirroring ``dashboard_service._latest_succeeded_scan_ids``), then a
     SINGLE grouped severity aggregation over exactly that scan-id set →
     ``{project_id: {critical, high, medium, low}}``;
  3. one ``GROUP BY project_id`` aggregation over ``scans`` → a
     ``{project_id: {scan_count, release_count, last_scan_at}}`` map (W3 #30).
     This uses the existing ``ix_scans_project_created_at`` (project_id leading
     + created_at) covering index — no separate index needed.

No per-row query is ever issued. The aggregation mirrors
``dashboard_service._severity_counts`` (worst CVE per component, MAX over a rank
CASE) but keeps ``project_id`` in the GROUP BY so each project gets its own
counts.

CLAUDE.md compliance:
  - Core rule #1/#2: pure async-SQLAlchemy reads against PostgreSQL; no schema
    change (existing indexes ``ix_projects_latest_scan_id`` and
    ``ix_scans_project_created_at`` cover the access paths).
  - This module is a pure DB read with NO auth check: the caller
    (``list_projects`` / the router) has already team-scoped the page of rows it
    passes in, so enrichment only ever sees projects the actor may read.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import String, case, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Scan, Vulnerability, VulnerabilityFinding

# Higher rank = "worse". Mirrors ``dashboard_service`` / ``project_detail_service``
# so the list rows, the dashboard, and the project detail can never disagree on
# how a CVE severity maps to a bucket. ``unknown`` folds into ``info`` rank: a CVE
# whose severity we don't know must never read as a clean (none) ribbon.
_SEVERITY_FROM_RANK: dict[int, str] = {
    0: "none",
    1: "info",
    2: "low",
    3: "medium",
    4: "high",
    5: "critical",
}

# The buckets the list-row indicator surfaces. ``info`` / ``none`` are not
# actionable on a compact list badge, so the summary carries only the four
# risk-bearing buckets (matches ``schemas.scan.SeveritySummary``).
_SUMMARY_BUCKETS = ("critical", "high", "medium", "low")


def _severity_rank_case() -> Any:
    """CASE mapping a ``vuln_severity`` ENUM value to its integer rank.

    Postgres ENUM ↔ varchar comparison needs an explicit cast to text, exactly
    as in ``dashboard_service`` / ``project_detail_service``.
    """
    return case(
        {
            literal("critical"): 5,
            literal("high"): 4,
            literal("medium"): 3,
            literal("low"): 2,
            literal("info"): 1,
            literal("unknown"): 1,
        },
        value=cast(Vulnerability.severity, String),
        else_=0,
    )


async def _latest_attempt_status_map(
    session: AsyncSession,
    *,
    projects: list[Any],
) -> dict[uuid.UUID, str]:
    """``{project_id: latest-attempt scan status}`` for the page.

    Anchors on ``Project.latest_scan_id`` (the denormalized last-attempt pointer)
    so this is the status-badge source. One query over the page's non-null
    ``latest_scan_id`` set; projects with no ``latest_scan_id`` (never scanned)
    are simply absent from the map → the caller maps them to ``None`` ("Idle").
    """
    scan_id_to_project: dict[uuid.UUID, uuid.UUID] = {
        p.latest_scan_id: p.id for p in projects if p.latest_scan_id is not None
    }
    if not scan_id_to_project:
        return {}

    stmt = (
        select(Scan.id, cast(Scan.status, String).label("status"))
        .where(Scan.id.in_(list(scan_id_to_project.keys())))
    )
    result = await session.execute(stmt)
    return {
        scan_id_to_project[row.id]: row.status
        for row in result.all()
        if row.id in scan_id_to_project
    }


async def _latest_succeeded_scan_id_map(
    session: AsyncSession,
    *,
    project_ids: list[uuid.UUID],
) -> dict[uuid.UUID, uuid.UUID]:
    """``{project_id: latest *succeeded* scan id}`` for the page.

    One ``DISTINCT ON (project_id)`` pass (mirrors
    ``dashboard_service._latest_succeeded_scan_ids`` but keeps the project id so we
    can key the per-project severity aggregation). ``Project.latest_scan_id`` is
    deliberately NOT used: it tracks the last attempt, not the last success.
    Projects with no succeeded scan are absent → ``severity_summary = null``.
    """
    if not project_ids:
        return {}

    stmt = (
        select(Scan.project_id, Scan.id)
        .distinct(Scan.project_id)
        .where(Scan.project_id.in_(project_ids))
        .where(cast(Scan.status, String) == "succeeded")
        .order_by(Scan.project_id, Scan.created_at.desc(), Scan.id.desc())
    )
    result = await session.execute(stmt)
    return {row.project_id: row.id for row in result.all()}


async def _severity_summary_map(
    session: AsyncSession,
    *,
    succeeded_by_project: dict[uuid.UUID, uuid.UUID],
) -> dict[uuid.UUID, dict[str, int]]:
    """``{project_id: {critical, high, medium, low}}`` over the succeeded scans.

    A single grouped aggregation, NOT one query per project. We collapse each
    (scan, component_version) to its worst CVE finding (MAX rank), then count how
    many components land in each severity bucket per scan, and finally re-key from
    scan id back to project id. Mirrors ``dashboard_service._severity_counts`` but
    grouped by ``scan_id`` too so each project gets its own counts.

    Every project that HAS a succeeded scan gets an entry (all-zero when the scan
    carried no CVE findings) so the caller can distinguish "succeeded, clean"
    (all-zero summary) from "never succeeded" (absent → null summary).
    """
    if not succeeded_by_project:
        return {}

    scan_to_project = {scan_id: pid for pid, scan_id in succeeded_by_project.items()}
    scan_ids = list(scan_to_project.keys())

    # Seed every succeeded project with an all-zero bucket so "succeeded but no
    # CVEs" is reported as zeros (non-null) rather than dropping to null.
    summaries: dict[uuid.UUID, dict[str, int]] = {
        pid: dict.fromkeys(_SUMMARY_BUCKETS, 0) for pid in succeeded_by_project
    }

    sev_rank = _severity_rank_case()
    per_cv = (
        select(
            VulnerabilityFinding.scan_id.label("scan_id"),
            VulnerabilityFinding.component_version_id.label("cv_id"),
            func.max(sev_rank).label("max_rank"),
        )
        .select_from(VulnerabilityFinding)
        .join(Vulnerability, Vulnerability.id == VulnerabilityFinding.vulnerability_id)
        .where(VulnerabilityFinding.scan_id.in_(scan_ids))
        .group_by(
            VulnerabilityFinding.scan_id,
            VulnerabilityFinding.component_version_id,
        )
        .subquery()
    )

    stmt = (
        select(
            per_cv.c.scan_id,
            per_cv.c.max_rank,
            func.count().label("n"),
        )
        .group_by(per_cv.c.scan_id, per_cv.c.max_rank)
    )
    result = await session.execute(stmt)

    for row in result.all():
        project_id = scan_to_project.get(row.scan_id)
        if project_id is None:
            continue
        bucket = _SEVERITY_FROM_RANK.get(int(row.max_rank), "none")
        if bucket not in _SUMMARY_BUCKETS:
            # info / none / unknown-rank components are not surfaced on the
            # list-row risk indicator.
            continue
        summaries[project_id][bucket] += int(row.n)

    return summaries


async def _scan_counts_map(
    session: AsyncSession,
    *,
    project_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict[str, Any]]:
    """``{project_id: {scan_count, release_count, last_scan_at}}`` for the page.

    W3 #30 — list-row discoverability aggregates produced by a SINGLE
    ``GROUP BY project_id`` query over ``scans``:

      - ``scan_count``    = total scan attempts (any status),
      - ``release_count`` = COUNT of attempts whose status='succeeded'
                            (tracker §0.5: every succeeded scan = a release),
      - ``last_scan_at``  = MAX(created_at) of any attempt (last *attempt*,
                            NOT last success — mirrors the overview field of
                            the same name).

    Projects with no scans at all are absent from the result map — the caller
    overlays defaults ``(0, 0, None)`` onto those rows. The existing covering
    index ``ix_scans_project_created_at`` (project_id leading + created_at)
    serves the GROUP BY without an index scan over the whole table.
    """
    if not project_ids:
        return {}

    scan_count_col = func.count().label("scan_count")
    release_count_col = func.count(
        case(
            (cast(Scan.status, String) == "succeeded", 1),
        )
    ).label("release_count")
    last_scan_at_col = func.max(Scan.created_at).label("last_scan_at")

    stmt = (
        select(
            Scan.project_id,
            scan_count_col,
            release_count_col,
            last_scan_at_col,
        )
        .where(Scan.project_id.in_(project_ids))
        .group_by(Scan.project_id)
    )
    result = await session.execute(stmt)
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for row in result.all():
        last_at: datetime | None = row.last_scan_at
        out[row.project_id] = {
            "scan_count": int(row.scan_count),
            "release_count": int(row.release_count),
            "last_scan_at": last_at,
        }
    return out


async def enrich_project_rows(
    session: AsyncSession,
    *,
    projects: list[Any],
) -> tuple[
    dict[uuid.UUID, str],
    dict[uuid.UUID, dict[str, int]],
    dict[uuid.UUID, dict[str, Any]],
]:
    """Return ``(status_by_project, severity_summary_by_project, counts_by_project)``.

    All three maps are computed in BATCHED queries over the page's project ids —
    never per row. The caller overlays them onto each ``ProjectPublic`` row:

      - ``status_by_project.get(p.id)`` → ``latest_scan_status`` (None ⇒ "Idle"),
      - ``severity_summary_by_project.get(p.id)`` → ``severity_summary`` (absent
        ⇒ null; present ⇒ a four-bucket dict, all-zero when the succeeded scan had
        no CVE findings),
      - ``counts_by_project.get(p.id)`` → ``{scan_count, release_count,
        last_scan_at}`` (absent ⇒ caller defaults to ``(0, 0, None)`` — the
        project has no scans at all). W3 #30 discoverability aggregates.

    A pure read with NO auth check: the caller has already team-scoped ``projects``.
    Returns three empty dicts for an empty page (no SQL issued).
    """
    if not projects:
        return {}, {}, {}

    project_ids = [p.id for p in projects]

    status_by_project = await _latest_attempt_status_map(session, projects=projects)
    succeeded_by_project = await _latest_succeeded_scan_id_map(
        session, project_ids=project_ids
    )
    severity_summary_by_project = await _severity_summary_map(
        session, succeeded_by_project=succeeded_by_project
    )
    counts_by_project = await _scan_counts_map(session, project_ids=project_ids)

    return status_by_project, severity_summary_by_project, counts_by_project


__all__ = ["enrich_project_rows"]
