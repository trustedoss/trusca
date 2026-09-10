# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Vulnerabilities API — Phase 3 PR #11 (Vulnerabilities tab + drawer).

Three endpoints:

  GET   /v1/projects/{project_id}/vulnerabilities          List CVE findings
  GET   /v1/vulnerability_findings/{finding_id}            Drawer detail
  PATCH /v1/vulnerability_findings/{finding_id}/status     Workflow transition

All routes require role >= developer; the `→ suppressed` transition is gated
inside the service layer to require role >= team_admin within the project's
team. Cross-team access (IDOR) is enforced inside the service: 403 for the
list endpoint (team-membership signal is not a secret here, mirrors PR #10
projects), 404 for detail / status (existence-hide cross-team rows).

All 4xx/5xx responses are RFC 7807 `application/problem+json`.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1._csv_export_response import csv_stream_response
from api.v1._snapshot_anchor import snapshot_anchor
from core.config import csv_export_rate_limit, ticket_status_refresh_rate_limit
from core.db import get_db
from core.errors import problem_response
from core.ratelimit import _authenticated_user_key, limiter
from core.security import CurrentUser, require_role
from schemas.vulnerability_detail import (
    TicketStatusRefreshOut,
    UpgradeCluster,
    UpgradeClusterFinding,
    UpgradeClusterListResponse,
    VulnerabilityAssignmentUpdate,
    VulnerabilityBulkStatusResponse,
    VulnerabilityBulkStatusResult,
    VulnerabilityBulkStatusUpdate,
    VulnerabilityDetailResponse,
    VulnerabilityListItem,
    VulnerabilityListResponse,
    VulnerabilityStatusUpdate,
)
from services.project_service import ProjectError
from services.scan_resolution import SnapshotScanNotFound
from services.table_export_service import stream_vulnerabilities_csv
from services.ticket_status_service import (
    FindingNotFound,
    NoTicketConfigured,
    TicketStatusServiceError,
    refresh_ticket_status,
)
from services.upgrade_cluster_service import (
    DEFAULT_CLUSTER_LIMIT,
    MAX_CLUSTER_LIMIT,
    list_upgrade_clusters,
)
from services.vulnerability_service import (
    VulnerabilityApprovalRequired,
    VulnerabilityBulkInputError,
    VulnerabilityConflict,
    VulnerabilityError,
    VulnerabilityInvalidTransition,
    bulk_transition_status,
    get_vulnerability_detail,
    list_project_vulnerabilities,
    update_finding_assignment,
    update_vulnerability_status,
)

router = APIRouter(prefix="/v1", tags=["vulnerabilities"])
log = structlog.get_logger("vulnerabilities.api")


# ---------------------------------------------------------------------------
# Error translation helpers
# ---------------------------------------------------------------------------


def _problem_for_vulnerability_error(request: Request, exc: ProjectError) -> Response:
    """
    Convert a vulnerability/project domain exception into a Problem Details
    response. Keeps the per-exception switch small: VulnerabilityInvalidTransition
    and VulnerabilityConflict carry extension data; everything else uses the
    base envelope from `problem_response`.
    """
    if isinstance(exc, VulnerabilityApprovalRequired):
        # A machine-readable marker rather than leaving clients to match on the
        # title. Two different 409s reach this endpoint (a stale if_match and
        # this one), and they need opposite responses from the UI: one is
        # "reload and try again", the other is "ask somebody".
        return problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=str(exc) or exc.title,
            instance=request.url.path,
            approval_required=True,
        )
    if isinstance(exc, VulnerabilityInvalidTransition):
        # RFC 7807 §3.2 explicitly allows extension members. We surface the
        # legal target set so the UI can disable buttons for invalid moves.
        return problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=str(exc) or exc.title,
            instance=request.url.path,
            allowed_to=list(exc.allowed_to),
        )
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


def _problem_for_snapshot_not_found(request: Request) -> Response:
    """RFC 7807 404 for an unresolvable ``?scan_id=`` snapshot anchor (feature #28).

    Existence-hide: the detail is uniform whether the scan is nonexistent,
    belongs to another project (IDOR probe), or is not succeeded — so the caller
    learns nothing about whether the id exists elsewhere.
    """
    return problem_response(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Scan Snapshot Not Found",
        detail="No succeeded scan with that id exists for this project.",
        instance=request.url.path,
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/vulnerabilities
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/vulnerabilities",
    response_model=VulnerabilityListResponse,
    summary="Paginated CVE findings for the project's latest scan",
)
async def list_project_vulnerabilities_endpoint(
    request: Request,
    project_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=255),
    severity: list[str] | None = Query(default=None),
    finding_status: list[str] | None = Query(default=None, alias="status"),
    license_category: list[str] | None = Query(
        default=None,
        description=(
            "W2 #33 — License risk-axis filter. Repeatable; accepted values: "
            "``forbidden``, ``conditional``, ``allowed``, ``unknown`` (the cv "
            "had no license finding in this scan). Unknown values are dropped, "
            "so a query that filters ONLY by unknown values returns an empty "
            "page (not a 422). Omit to include all categories."
        ),
    ),
    min_epss: float | None = Query(
        default=None,
        ge=0,
        le=1,
        description=(
            "Keep only findings whose CVE has an EPSS exploit-probability >= this "
            "threshold, in [0, 1]. CVEs with no published EPSS score are excluded. "
            "Omit to disable EPSS filtering."
        ),
    ),
    reachable: str | None = Query(
        default=None,
        pattern=r"^(true|false|unknown)$",
        description=(
            "Tri-state reachability filter (v2.3). ``true`` → only findings whose "
            "vulnerable symbol is reachable on the call graph; ``false`` → only "
            "findings an analyser proved NOT reachable; ``unknown`` → only "
            "not-analysed findings (reachable IS NULL). Omit to disable the "
            "reachability filter."
        ),
    ),
    sla: str | None = Query(
        default=None,
        pattern=r"^(overdue|imminent|ok)$",
        description=(
            "X1 SLA filter (single value). ``overdue`` → past the per-severity "
            "SLA due date; ``imminent`` → due within 7 days; ``ok`` → inside "
            "the window. Findings with no SLA (info / unknown severity) never "
            "match any token. Omit to disable SLA filtering."
        ),
    ),
    assignee: str | None = Query(
        default=None,
        pattern=r"^(me|unassigned|inactive)$",
        description=(
            "Ownership filter. `me` returns the caller's own findings, "
            "`unassigned` the ones nobody has taken, and `inactive` the ones "
            "assigned to somebody whose account is closed, which look owned "
            "and cannot move. Values name a state rather than a person: the "
            "parameter already says whose. Deliberately not a user id, which "
            "would add a way to ask which findings a named person owns "
            "without any screen needing it."
        ),
    ),
    sort: str = Query(
        default="severity",
        pattern=r"^(severity|cvss|status|discovered_at|epss|reachable|component|priority|sla_due)$",
        description=(
            "Sort key. ``reachable`` ranks reachable findings first (then "
            "not-analysed, then proven-unreachable), tie-broken by severity desc. "
            "``component`` sorts by affected package name. ``priority`` is the "
            "triage ranking: CISA-KEV-listed CVEs first, then severity desc, "
            "then EPSS desc (nulls last). ``sla_due`` orders by the SLA due "
            "date (asc = most urgent first); findings with no SLA sort last in "
            "both directions."
        ),
    ),
    order: str = Query(default="desc", pattern=r"^(asc|desc)$"),
    scan_id: uuid.UUID | None = Depends(snapshot_anchor),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    try:
        items, total, severity_distribution = await list_project_vulnerabilities(
            session,
            project_id=project_id,
            actor=actor,
            limit=limit,
            offset=offset,
            search=search,
            severity=severity,
            status=finding_status,
            license_category=license_category,
            min_epss=min_epss,
            reachable=reachable,
            sla=sla,
            assignee=assignee,
            sort=sort,
            order=order,
            snapshot_scan_id=scan_id,
        )
    except SnapshotScanNotFound:
        return _problem_for_snapshot_not_found(request)
    except (VulnerabilityError, ProjectError) as exc:
        return _problem_for_vulnerability_error(request, exc)

    body = VulnerabilityListResponse(
        items=[VulnerabilityListItem.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
        severity_distribution=severity_distribution,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/vulnerabilities/export.csv  (B5)
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/vulnerabilities/export.csv",
    response_class=StreamingResponse,
    summary="The filtered CVE findings as CSV",
)
# The limiter is opt-in in this app (no default limits), so an undecorated
# route has none. One export walks the list service up to two hundred times
# and holds a pooled connection for the whole stream, which makes it the
# cheapest denial-of-service primitive the lowest role has if left open.
@limiter.limit(csv_export_rate_limit, key_func=_authenticated_user_key)
async def export_project_vulnerabilities_csv_endpoint(
    request: Request,
    project_id: uuid.UUID,
    search: str | None = Query(default=None, max_length=255),
    severity: list[str] | None = Query(default=None),
    finding_status: list[str] | None = Query(default=None, alias="status"),
    license_category: list[str] | None = Query(default=None),
    min_epss: float | None = Query(default=None, ge=0, le=1),
    reachable: str | None = Query(default=None, pattern=r"^(true|false|unknown)$"),
    sla: str | None = Query(default=None, pattern=r"^(overdue|imminent|ok)$"),
    assignee: str | None = Query(default=None, pattern=r"^(me|unassigned|inactive)$"),
    sort: str = Query(
        default="severity",
        pattern=r"^(severity|cvss|status|discovered_at|epss|reachable|component|priority|sla_due)$",
    ),
    order: str = Query(default="desc", pattern=r"^(asc|desc)$"),
    scan_id: uuid.UUID | None = Depends(snapshot_anchor),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    """
    The same rows the list endpoint would return, without the paging.

    Every filter the list accepts is accepted here and applied by the same
    code: the export pages the list service rather than rebuilding its query,
    so a filter that narrows the screen narrows the file identically, and the
    cross-team check the list performs is the one this performs.

    ``limit`` and ``offset`` are deliberately absent. Exporting "page 3 of
    what I am looking at" is not a thing anyone wants, and accepting them
    would invite a caller to walk the table with a script.

    ``sort``/``order`` are still accepted and still validated (an invalid
    value still 422s), but no longer decide the exported rows' order (#463):
    the export walks a fixed key (the finding id) instead of the screen's 8
    sort modes, so a filtered export that reaches real depth does not pay
    for ``OFFSET``'s cost growing with it. Kept on the signature rather than
    dropped so an existing caller's `?sort=...&order=...` keeps 200ing
    instead of 422ing on an unknown parameter.
    """
    stream = stream_vulnerabilities_csv(
        session,
        project_id=project_id,
        actor=actor,
        filters={
            "search": search,
            "severity": severity,
            "status": finding_status,
            "license_category": license_category,
            "min_epss": min_epss,
            "reachable": reachable,
            "sla": sla,
            "assignee": assignee,
            "sort": sort,
            "order": order,
            "snapshot_scan_id": scan_id,
        },
    )
    try:
        return await csv_stream_response(
            request,
            stream=stream,
            filename=f"vulnerabilities_{project_id}.csv",
        )
    except SnapshotScanNotFound:
        return _problem_for_snapshot_not_found(request)
    except (VulnerabilityError, ProjectError) as exc:
        return _problem_for_vulnerability_error(request, exc)


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/vulnerabilities/upgrade-clusters
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/vulnerabilities/upgrade-clusters",
    response_model=UpgradeClusterListResponse,
    summary="Open findings grouped by the component upgrade that fixes them",
)
async def list_upgrade_clusters_endpoint(
    request: Request,
    project_id: uuid.UUID,
    scan_id: uuid.UUID | None = Depends(snapshot_anchor),
    limit: int = Query(default=DEFAULT_CLUSTER_LIMIT, ge=1, le=MAX_CLUSTER_LIMIT),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    """The "Group by upgrade" view: the resolved scan's OPEN findings grouped by
    the minimum safe upgrade that clears them, most-actionable first.

    Same auth / snapshot semantics as the list endpoint — non-member → 403,
    missing project → 404, unresolvable ``?scan_id=`` → 404, no succeeded scan →
    200 with an empty ``clusters`` and ``total_findings == 0``.
    """
    try:
        result = await list_upgrade_clusters(
            session,
            project_id=project_id,
            actor=actor,
            snapshot_scan_id=scan_id,
            limit=limit,
        )
    except SnapshotScanNotFound:
        return _problem_for_snapshot_not_found(request)
    except (VulnerabilityError, ProjectError) as exc:
        return _problem_for_vulnerability_error(request, exc)

    body = UpgradeClusterListResponse(
        scan_id=result.scan_id,
        total_findings=result.total_findings,
        total_clusters=result.total_clusters,
        truncated=result.truncated,
        clusters=[
            UpgradeCluster(
                component_version_id=c["component_version_id"],
                component_name=c["component_name"],
                component_purl=c["component_purl"],
                current_version=c["current_version"],
                recommended_version=c["recommended_version"],
                reason=c["reason"],
                direct=c["direct"],
                max_severity=c["max_severity"],
                max_epss=c["max_epss"],
                finding_count=c["finding_count"],
                findings=[UpgradeClusterFinding.model_validate(f) for f in c["findings"]],
            )
            for c in result.clusters
        ],
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/vulnerability_findings/{finding_id}
# ---------------------------------------------------------------------------


def _detail_response(payload: dict[str, Any]) -> Response:
    """Shared serializer for the two endpoints that return a detail payload.

    Issue #382: this used to name all 41 fields of ``VulnerabilityDetailResponse``
    as keyword arguments by hand. A key the service payload already carried
    but nobody added to that call was dropped silently (it happened twice:
    kev / kev_due_date, then the ER28a ownership fields). ``model_validate``
    removes the hand-listing entirely, so there is no per-field call site left
    to fall behind the payload. The nested structures (affected_components,
    status_history, upgrade_recommendation) do not need the per-item
    ``model_validate`` calls the old code had either: Pydantic validates a
    dict or a list of dicts against a nested model field on its own.

    The response model declares ``extra="forbid"``, so a payload key this
    schema does not know about raises (500) instead of being dropped, and a
    field the payload fails to supply raises too, because most fields here
    have no default.
    """
    body = VulnerabilityDetailResponse.model_validate(payload)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


@router.get(
    "/vulnerability_findings/{finding_id}",
    response_model=VulnerabilityDetailResponse,
    summary="Vulnerability finding drawer payload (404 if invisible to caller)",
)
async def get_vulnerability_finding_endpoint(
    request: Request,
    finding_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    try:
        payload = await get_vulnerability_detail(
            session,
            finding_id=finding_id,
            actor=actor,
        )
    except (VulnerabilityError, ProjectError) as exc:
        return _problem_for_vulnerability_error(request, exc)
    return _detail_response(payload)


# ---------------------------------------------------------------------------
# PATCH /v1/vulnerability_findings/{finding_id}/status
# ---------------------------------------------------------------------------


@router.patch(
    "/vulnerability_findings/{finding_id}/status",
    response_model=VulnerabilityDetailResponse,
    summary="Transition a vulnerability finding's VEX status (audit-logged)",
    responses={
        200: {
            "description": (
                "Status transitioned, OR the finding was already at the "
                "requested status (idempotent no-op — M-26). Body is the "
                "post-commit detail payload."
            ),
        },
        403: {
            "description": (
                "Caller's role is insufficient (e.g. developer attempting `→ suppressed`)."
            ),
        },
        404: {
            "description": (
                "Finding does not exist, or exists in a team the caller cannot access. "
                "Returned in lieu of 403 to avoid leaking existence."
            ),
        },
        409: {"description": "if_match snapshot did not match the current updated_at."},
        422: {
            "description": (
                "Transition is not allowed by the workflow matrix. The "
                "Problem Details body carries an `allowed_to` extension "
                "listing the legal next states from the current status."
            ),
        },
    },
)
async def update_vulnerability_status_endpoint(
    request: Request,
    finding_id: uuid.UUID,
    payload: VulnerabilityStatusUpdate,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        result = await update_vulnerability_status(
            session,
            finding_id=finding_id,
            actor=actor,
            target_status=payload.status,
            justification=payload.justification,
            if_match=payload.if_match,
        )
    except VulnerabilityConflict as exc:
        # 409 — distinct from 422 because it indicates concurrent modification,
        # not an invalid request shape.
        return _problem_for_vulnerability_error(request, exc)
    except (VulnerabilityError, ProjectError) as exc:
        return _problem_for_vulnerability_error(request, exc)
    return _detail_response(result)


# ---------------------------------------------------------------------------
# PATCH /v1/vulnerability_findings/{finding_id}/assignment  (ER28a)
# ---------------------------------------------------------------------------


@router.patch(
    "/vulnerability_findings/{finding_id}/assignment",
    response_model=VulnerabilityDetailResponse,
    summary="Set a finding's owner, deadline and ticket (audit-logged)",
    responses={
        200: {
            "description": (
                "Assignment updated. The body is the post-commit detail "
                "payload, which carries `effective_due_date`, `due_source` and "
                "`manual_due_ignored` so the caller can tell the person "
                "immediately when a date they just set does not govern."
            ),
        },
        404: {
            "description": (
                "Finding does not exist, or exists in a team the caller cannot "
                "access. Returned in lieu of 403 to avoid leaking existence."
            ),
        },
        409: {"description": "if_match snapshot did not match the current updated_at."},
        422: {
            "description": (
                "No fields supplied, or the assignee is not an active person "
                "on the project's team."
            ),
        },
    },
)
async def update_vulnerability_assignment_endpoint(
    request: Request,
    finding_id: uuid.UUID,
    payload: VulnerabilityAssignmentUpdate,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    # Only what the caller actually sent: `model_fields_set` is what separates
    # "clear this field" (sent as null) from "leave it alone" (absent). Reading
    # the model's values instead would unassign every finding whose PATCH only
    # meant to set a ticket.
    changes = {
        field: getattr(payload, field)
        for field in payload.model_fields_set
        if field != "if_match"
    }
    try:
        result = await update_finding_assignment(
            session,
            finding_id=finding_id,
            actor=actor,
            changes=changes,
            if_match=payload.if_match,
        )
    except VulnerabilityConflict as exc:
        return _problem_for_vulnerability_error(request, exc)
    except (VulnerabilityError, ProjectError) as exc:
        return _problem_for_vulnerability_error(request, exc)
    return _detail_response(result)


# ---------------------------------------------------------------------------
# POST /v1/vulnerability_findings/{finding_id}/ticket-status/refresh  (#385)
# ---------------------------------------------------------------------------


@router.post(
    "/vulnerability_findings/{finding_id}/ticket-status/refresh",
    response_model=TicketStatusRefreshOut,
    summary="Read the finding's external ticket back and record its state",
    responses={
        200: {
            "description": (
                "The attempt completed; check `ticket_check_error` for whether "
                "it actually reached an answer. A failed OUTBOUND call to the "
                "tracker (no credential configured, the URL failed the SSRF "
                "guard, the tracker rejected the token, the issue does not "
                "exist) is reported here, not as a non-200 status."
            ),
        },
        404: {
            "description": (
                "Finding does not exist, or exists in a team the caller cannot "
                "access. Returned in lieu of 403 to avoid leaking existence."
            ),
        },
        422: {"description": "The finding has no ticket_url set, so there is nothing to check."},
    },
)
# One call spends the org's shared tracker credential against a third-party
# host, so it is rate-limited the same as the other submit-triggered
# external lookups even though it is a deliberate button click.
@limiter.limit(ticket_status_refresh_rate_limit, key_func=_authenticated_user_key)
async def refresh_ticket_status_endpoint(
    request: Request,
    finding_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        result = await refresh_ticket_status(session, finding_id=finding_id, actor=actor)
    except (FindingNotFound, NoTicketConfigured, TicketStatusServiceError) as exc:
        return problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=str(exc) or exc.title,
            instance=request.url.path,
        )
    out = TicketStatusRefreshOut(
        finding_id=result.finding_id,
        ticket_status=result.ticket_status,
        ticket_resolved=result.ticket_resolved,
        ticket_checked_at=result.ticket_checked_at,
        ticket_check_error=result.ticket_check_error,
    )
    return Response(
        content=out.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# POST /v1/projects/{project_id}/vulnerabilities:bulk-transition  (W2 #33b)
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/vulnerabilities:bulk-transition",
    response_model=VulnerabilityBulkStatusResponse,
    summary="Transition many findings in one project to the same VEX status",
    responses={
        200: {
            "description": (
                "Bulk envelope completed. ``results[*].status_code`` reports per-row "
                "outcomes (200/403/404/422). ``succeeded`` + ``failed`` == ``total``."
            ),
        },
        404: {
            "description": (
                "Project does not exist, OR the caller is not a member of the "
                "project's team. Returned in lieu of 403 to avoid leaking team "
                "membership (mirrors the single-row PATCH existence-hide policy)."
            ),
        },
        422: {
            "description": (
                "Envelope-level shape violation: empty ``finding_ids``, more than "
                "``BULK_TRANSITION_MAX`` entries, unknown ``target_status``. "
                "Per-row matrix violations are NOT envelope 422 — they are "
                "reported as ``results[*].status_code == 422``."
            ),
        },
    },
)
async def bulk_transition_vulnerabilities_endpoint(
    request: Request,
    project_id: uuid.UUID,
    payload: VulnerabilityBulkStatusUpdate,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    """W2 #33b — apply one VEX transition across many findings in one round-trip.

    Per-row failures (404 / 403 / 422) are surfaced in the response envelope
    so the UI can render "succeeded N · failed M" with per-row details.
    Only envelope-level shape violations (empty list, > cap, unknown enum)
    return RFC 7807 — those would still abort a per-row partial commit, so
    they belong on the envelope rather than masquerading as per-row outcomes.
    """
    try:
        project, results = await bulk_transition_status(
            session,
            project_id=project_id,
            actor=actor,
            finding_ids=payload.finding_ids,
            target_status=payload.target_status,
            justification=payload.justification,
        )
    except VulnerabilityBulkInputError as exc:
        return _problem_for_vulnerability_error(request, exc)
    except (VulnerabilityError, ProjectError) as exc:
        return _problem_for_vulnerability_error(request, exc)

    # `project` is returned for future audit-correlation use; bind here so
    # the per-row audit rows (already emitted by the before_flush listener
    # at commit time) share a stable request_id grouping.
    _ = project

    succeeded = sum(1 for r in results if r.success)
    body = VulnerabilityBulkStatusResponse(
        target_status=payload.target_status,
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results=[
            VulnerabilityBulkStatusResult(
                finding_id=r.finding_id,
                success=r.success,
                status_code=r.status_code,
                error=r.error,
                detail=r.detail,
                # service returns plain str[] of VEX statuses (which are the
                # transition matrix's outgoing edges); narrow to the wire
                # Literal so Pydantic validates against the canonical set.
                allowed_to=cast("list[Any] | None", r.allowed_to),
            )
            for r in results
        ],
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


__all__ = ["router"]
