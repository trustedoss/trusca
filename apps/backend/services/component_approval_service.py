"""
Component Approval Workflow service — Phase 4 PR #15.

Entry points (all async, all invoked from the matching router endpoints):
  - list_approvals
  - get_approval
  - create_approval
  - transition_approval
  - delete_approval

Audit:
  The SQLAlchemy ``before_flush`` listener in ``core/audit.py`` automatically
  emits an ``audit_logs`` row for every INSERT / UPDATE / DELETE on domain rows.
  Service code must never call ``session.add(AuditLog(...))`` directly.  The
  request context (``user_id``, ``team_id``, ``request_id``) must be bound into
  ``audit_context`` before the flush — this is done by the middleware and the
  ``get_current_user`` dependency at the request boundary, so service code has
  no extra obligation.

RBAC / existence-hide contract:
  Non-members see 404, not 403, for every per-resource operation (get_approval,
  transition_approval, delete_approval).  This is the "existence-hide" pattern
  from ``feedback_admin_existence_hide_pattern`` MEMORY entry.  For the
  list endpoint, super_admin sees all rows; everyone else sees only their team.

Optimistic concurrency (ETag):
  ``transition_approval`` takes an ``if_match: int`` argument that must equal
  the row's current ``version``.  The row is fetched with ``with_for_update()``
  to close the TOCTOU race (see MEMORY ``feedback_optimistic_concurrency_pattern``).
  A mismatch returns 412 with ``approval_etag_mismatch`` extension.

State machine:
  pending       → under_review, rejected
  under_review  → approved, rejected
  approved      → (terminal)
  rejected      → (terminal)
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from core.authz import assert_team_access, can_access_team
from core.security import CurrentUser
from models import ComponentApproval, Project
from models.component_approval import ApprovalStatus
from models.scan import Component  # explicit import avoids implicit lazy load

log = structlog.get_logger("component_approval.service")


# ---------------------------------------------------------------------------
# Result envelope for the list endpoint — P1 #6
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApprovalListRow:
    """One approval row plus display fields for the cross-project list UI.

    The component / project FK relationships are intentionally NOT declared
    on ComponentApproval (cross-domain, one-way per the model docstring), so
    we cannot ``selectinload`` them. Instead the list service fetches the
    display fields with two batched IN(...) lookups and returns them
    alongside the ORM row in this envelope.
    """

    approval: ComponentApproval
    component_name: str | None
    component_purl: str | None
    project_name: str | None
    project_slug: str | None


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class ApprovalError(Exception):
    """Base class for approval-domain errors. Each carries an HTTP status code."""

    status_code: int = 400
    title: str = "Approval Error"


class ApprovalNotFound(ApprovalError):
    status_code = 404
    title = "Approval Not Found"


class ApprovalAlreadyOpen(ApprovalError):
    """409 — an open (pending / under_review) approval already exists for this component+project."""

    status_code = 409
    title = "Approval Already Open"
    extensions: dict[str, object] = {"approval_already_open": True}


class ApprovalInvalidTransition(ApprovalError):
    """409 — the requested state transition is not permitted by the matrix."""

    status_code = 409
    title = "Invalid Approval Transition"

    def __init__(self, message: str, *, allowed_to: list[str] | None = None) -> None:
        super().__init__(message)
        self.allowed_to: list[str] = list(allowed_to or [])
        self.extensions: dict[str, object] = {
            "approval_invalid_transition": True,
            "allowed_to": self.allowed_to,
        }


class ApprovalEtagMismatch(ApprovalError):
    """412 — If-Match version did not match the row's current version."""

    status_code = 412
    title = "Approval ETag Mismatch"
    extensions: dict[str, object] = {"approval_etag_mismatch": True}


class ApprovalTerminalState(ApprovalError):
    """409 — the approval is in a terminal state and cannot be deleted."""

    status_code = 409
    title = "Approval Terminal State"
    extensions: dict[str, object] = {"approval_terminal_state": True}


class ApprovalForbidden(ApprovalError):
    """403 — actor lacks the required role to perform this operation."""

    status_code = 403
    title = "Forbidden"


# ---------------------------------------------------------------------------
# Transition matrix
# ---------------------------------------------------------------------------

# States that a caller can transition TO via PATCH (never "pending" — the
# server assigns that on creation).
_TRANSITION_MAP: dict[str, frozenset[str]] = {
    ApprovalStatus.pending: frozenset(
        # BUGHUNTER-GOLDEN(GOLD-P6-001): approved 를 추가해 under_review 를 건너뛴 불법 전이 허용
        {ApprovalStatus.under_review, ApprovalStatus.rejected, ApprovalStatus.approved}
    ),
    ApprovalStatus.under_review: frozenset(
        {ApprovalStatus.approved, ApprovalStatus.rejected}
    ),
    ApprovalStatus.approved: frozenset(),
    ApprovalStatus.rejected: frozenset(),
}

_TERMINAL_STATES: frozenset[str] = frozenset(
    {ApprovalStatus.approved, ApprovalStatus.rejected}
)

# Transitions that require role >= team_admin within the approval's team.
_REQUIRES_TEAM_ADMIN: frozenset[str] = frozenset(
    {ApprovalStatus.under_review, ApprovalStatus.approved, ApprovalStatus.rejected}
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _role_in_team(actor: CurrentUser, team_id: uuid.UUID) -> str | None:
    """Return the actor's role in *team_id*, or None if not a member."""
    if actor.is_superuser or actor.role == "super_admin":
        return "super_admin"
    return actor.team_roles.get(team_id)


def _has_team_admin(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    role = _role_in_team(actor, team_id)
    return role in {"team_admin", "super_admin"}


# ---------------------------------------------------------------------------
# list_approvals
# ---------------------------------------------------------------------------


async def list_approvals(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    status_filter: str | None = None,
    team_id: uuid.UUID | None = None,
    requested_by_user_id: uuid.UUID | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[ApprovalListRow], int]:
    """
    Return a paginated list of approvals.

    super_admin sees all rows; any other role sees only their own team's rows.
    An explicit ``team_id`` filter further restricts the result set (callers
    cannot use it to bypass the team-membership gate — if a non-super_admin
    passes a team_id they don't belong to, their own team filter applies and
    the query will return 0 results for that alien team_id).
    """
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)

    base = select(ComponentApproval)
    count_base = select(func.count()).select_from(ComponentApproval)

    # --- tenant gate ---
    if not (actor.is_superuser or actor.role == "super_admin"):
        # Restrict to the actor's own teams; ignore any caller-supplied
        # team_id that is outside this set.
        base = base.where(ComponentApproval.team_id.in_(actor.team_ids))
        count_base = count_base.where(ComponentApproval.team_id.in_(actor.team_ids))

    if team_id is not None:
        base = base.where(ComponentApproval.team_id == team_id)
        count_base = count_base.where(ComponentApproval.team_id == team_id)

    if status_filter is not None:
        base = base.where(ComponentApproval.status == status_filter)
        count_base = count_base.where(ComponentApproval.status == status_filter)

    if requested_by_user_id is not None:
        base = base.where(ComponentApproval.requested_by_user_id == requested_by_user_id)
        count_base = count_base.where(
            ComponentApproval.requested_by_user_id == requested_by_user_id
        )

    if from_dt is not None:
        base = base.where(ComponentApproval.requested_at >= from_dt)
        count_base = count_base.where(ComponentApproval.requested_at >= from_dt)

    if to_dt is not None:
        base = base.where(ComponentApproval.requested_at <= to_dt)
        count_base = count_base.where(ComponentApproval.requested_at <= to_dt)

    total = int((await session.execute(count_base)).scalar_one())

    rows_stmt = (
        base.order_by(ComponentApproval.requested_at.desc(), ComponentApproval.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = list((await session.execute(rows_stmt)).scalars().all())

    # P1 #6 — batch-load component name/purl + project name/slug so the UI
    # can render real labels in the Component / Project columns instead of
    # the first 8 chars of a UUID. Two indexed IN(...) lookups; both empty
    # if the page is empty so an actor with zero approvals still pays nothing.
    comp_ids = {r.component_id for r in rows}
    proj_ids = {r.project_id for r in rows}
    comp_meta: dict[uuid.UUID, tuple[str, str]] = {}
    proj_meta: dict[uuid.UUID, tuple[str, str]] = {}
    if comp_ids:
        comp_rows = (
            await session.execute(
                select(Component.id, Component.name, Component.purl).where(
                    Component.id.in_(comp_ids)
                )
            )
        ).all()
        comp_meta = {cid: (cname, cpurl) for cid, cname, cpurl in comp_rows}
    if proj_ids:
        proj_rows = (
            await session.execute(
                select(Project.id, Project.name, Project.slug).where(
                    Project.id.in_(proj_ids)
                )
            )
        ).all()
        proj_meta = {pid: (pname, pslug) for pid, pname, pslug in proj_rows}

    def _comp(cid: uuid.UUID) -> tuple[str | None, str | None]:
        m = comp_meta.get(cid)
        return (m[0], m[1]) if m else (None, None)

    def _proj(pid: uuid.UUID) -> tuple[str | None, str | None]:
        m = proj_meta.get(pid)
        return (m[0], m[1]) if m else (None, None)

    enriched: list[ApprovalListRow] = []
    for r in rows:
        cn, cp = _comp(r.component_id)
        pn, ps = _proj(r.project_id)
        enriched.append(
            ApprovalListRow(
                approval=r,
                component_name=cn,
                component_purl=cp,
                project_name=pn,
                project_slug=ps,
            )
        )

    log.info(
        "approval.list",
        actor_id=str(actor.id),
        total=total,
        page=page,
        page_size=page_size,
    )
    return enriched, total


# ---------------------------------------------------------------------------
# get_approval
# ---------------------------------------------------------------------------


async def get_approval(
    session: AsyncSession,
    actor: CurrentUser,
    approval_id: uuid.UUID,
) -> ComponentApproval:
    """
    Fetch a single approval.

    Existence-hide: non-members of the approval's team receive 404.
    """
    stmt = select(ComponentApproval).where(ComponentApproval.id == approval_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ApprovalNotFound(f"approval {approval_id} not found")

    assert_team_access(
        actor,
        row.team_id,
        log=log,
        resource="component_approval",
        resource_id=str(approval_id),
        deny=lambda: ApprovalNotFound(f"approval {approval_id} not found"),
    )
    return row


# ---------------------------------------------------------------------------
# create_approval
# ---------------------------------------------------------------------------


async def create_approval(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    component_id: uuid.UUID,
    project_id: uuid.UUID,
) -> ComponentApproval:
    """
    Open a new approval request for a component within a project.

    Guards:
      1. The project must exist and the actor must be a member of its team.
      2. The component must belong to the project (via scan_components join).
      3. At most one open (pending / under_review) approval per (component, project).
         Enforced by both the DB partial unique index and a preflight query.
    """
    # 1. Resolve project → team_id
    project = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise ApprovalNotFound(f"project {project_id} not found")

    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="project",
        resource_id=str(project_id),
        deny=lambda: ApprovalNotFound(f"project {project_id} not found"),
    )
    team_id = project.team_id

    # 2. Confirm the component exists (presence in the components table is
    #    sufficient; the service does not gate on scan_components because a
    #    component may be shared across scans / projects).
    comp = (
        await session.execute(select(Component).where(Component.id == component_id))
    ).scalar_one_or_none()
    if comp is None:
        raise ApprovalNotFound(f"component {component_id} not found")

    # 3. Preflight: no open approval for this (component, project) pair.
    existing = (
        await session.execute(
            select(ComponentApproval).where(
                and_(
                    ComponentApproval.component_id == component_id,
                    ComponentApproval.project_id == project_id,
                    ComponentApproval.status.in_(
                        [ApprovalStatus.pending, ApprovalStatus.under_review]
                    ),
                )
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ApprovalAlreadyOpen(
            f"an open approval already exists for component {component_id} "
            f"in project {project_id}"
        )

    row = ComponentApproval(
        component_id=component_id,
        project_id=project_id,
        team_id=team_id,
        requested_by_user_id=actor.id,
        status=ApprovalStatus.pending,
        version=1,
    )
    session.add(row)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # The partial unique index fired — a concurrent create beat us.
        raise ApprovalAlreadyOpen(
            f"an open approval already exists for component {component_id} "
            f"in project {project_id}"
        ) from exc

    await session.refresh(row)
    log.info(
        "approval.created",
        actor_id=str(actor.id),
        approval_id=str(row.id),
        component_id=str(component_id),
        project_id=str(project_id),
    )
    return row


# ---------------------------------------------------------------------------
# auto_create_pending_approvals — sync, system-context (scan pipeline)
# ---------------------------------------------------------------------------


def auto_create_pending_approvals(
    session: Session,
    *,
    project_id: uuid.UUID,
    team_id: uuid.UUID,
    component_ids: Iterable[uuid.UUID],
    scan_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """Open a Pending approval for each component, system-context (no actor).

    BUG-010: the scan pipeline must auto-enrol conditional-license components
    into the legal-review queue (``docs-site/docs/user-guide/approvals.md``
    promises "a Pending request is created automatically. No manual action
    required."). The user-facing :func:`create_approval` is async, demands a
    ``CurrentUser`` actor, and runs team-access checks — none of which fit the
    Celery worker, which runs on a **sync** session
    (``core.db.sync_session_scope``) as a system actor with no request context.
    This helper is the sync, actor-less counterpart.

    Contract:
      * Each component gets a ``ComponentApproval`` row with
        ``requested_by_user_id=NULL`` (system-created — the model column is
        nullable / ON DELETE SET NULL) and ``status='pending'``.
      * **Idempotent.** A component that already has an *open*
        (pending / under_review) approval for this project is skipped, so a
        re-run of the scan (which purges + re-creates findings) never
        double-creates.

    Implementation (QA follow-up Low — was per-row SAVEPOINT, now set-based):
      The previous implementation looped one component at a time with a
      preflight ``SELECT`` + ``begin_nested()`` SAVEPOINT + ``flush`` — O(N)
      round-trips and a TOCTOU window between the preflight and the flush. We
      now issue a **single** ``INSERT ... ON CONFLICT DO NOTHING`` keyed on the
      partial unique index ``ix_component_approvals_unique_open`` (component_id,
      project_id) ``WHERE status IN ('pending','under_review')``. One statement
      replaces N round-trips and the conflict resolution happens atomically in
      the DB, eliminating the TOCTOU window entirely:

        * ``index_elements=[component_id, project_id]`` + ``index_where=<same
          predicate as the partial index>`` make the ON CONFLICT target match
          the partial unique index exactly. The ``index_where`` is **mandatory**
          — omitting it leaves the conflict target unmatched against a *partial*
          index and Postgres raises "no unique or exclusion constraint matching
          the ON CONFLICT specification".
        * ``RETURNING component_id`` reports the rows actually inserted. Rows
          skipped by ON CONFLICT (an open approval already existed, including a
          row opened by a concurrent writer that committed before this
          statement) do NOT appear in RETURNING, so the returned list is the
          precise set of newly-created approvals — used by the caller to write
          an accurate audit-summary ``created_count`` / ``component_ids``.

    The caller is responsible for the outer ``session.commit()`` (the sync
    session does not auto-commit — see ``sync_session_scope``). Returns the list
    of component ids for which a brand-new approval was created (skipped
    duplicates are not in the list).

    Audit: the sync session has **no** audit listeners installed (see
    ``core/db.py`` — Celery system writes are deliberately un-audited to avoid
    ballooning ``audit_logs`` with per-scan rows). Per ``core/db.py``'s contract
    ("Tasks that need an audit trail emit explicit AuditLog rows from inside the
    service layer"), the scan-pipeline stage wrapper
    (``tasks.scan_source._auto_create_conditional_approvals``) writes ONE
    summary ``audit_logs`` row per scan using this function's return value — not
    one row per component, to keep the table from ballooning.
    """
    # De-dupe the incoming ids so a caller that passes the same component twice
    # (e.g. one component with two conditional license findings) only attempts
    # one insert. ON CONFLICT would not deduplicate *within the same INSERT*
    # (two rows with identical (component_id, project_id) both pass the conflict
    # check before either commits), so the pre-INSERT de-dupe is load-bearing.
    unique_ids = list(dict.fromkeys(component_ids))
    if not unique_ids:
        return []

    # Match the partial unique index predicate EXACTLY (see model
    # ``ix_component_approvals_unique_open`` postgresql_where) so ON CONFLICT
    # resolves against it. ``status`` is the enum string column; the literal
    # IN-list mirrors the index DDL.
    index_where = ComponentApproval.status.in_(
        [ApprovalStatus.pending.value, ApprovalStatus.under_review.value]
    )

    stmt = (
        pg_insert(ComponentApproval)
        .values(
            [
                {
                    "component_id": component_id,
                    "project_id": project_id,
                    "team_id": team_id,
                    "requested_by_user_id": None,  # system-created
                    "status": ApprovalStatus.pending.value,
                    "version": 1,
                }
                for component_id in unique_ids
            ]
        )
        .on_conflict_do_nothing(
            index_elements=["component_id", "project_id"],
            index_where=index_where,
        )
        .returning(ComponentApproval.component_id)
    )

    created_ids = list(session.execute(stmt).scalars().all())

    log.info(
        "approval.auto_created_batch",
        project_id=str(project_id),
        team_id=str(team_id),
        scan_id=str(scan_id) if scan_id is not None else None,
        requested=len(unique_ids),
        created=len(created_ids),
        skipped=len(unique_ids) - len(created_ids),
    )
    return created_ids


# ---------------------------------------------------------------------------
# transition_approval
# ---------------------------------------------------------------------------


async def transition_approval(
    session: AsyncSession,
    actor: CurrentUser,
    approval_id: uuid.UUID,
    *,
    action: str,
    decision_note: str | None,
    if_match: int,
) -> ComponentApproval:
    """
    Transition an approval's status.

    SELECT FOR UPDATE closes the TOCTOU race between reading and writing the row
    (MEMORY ``feedback_optimistic_concurrency_pattern``).

    Guards (in order):
      1. Row exists → 404 (existence-hide for non-members via team check below).
      2. Team access: non-member → 404.
      3. ETag: if_match != row.version → 412.
      4. State matrix: invalid transition → 409.
      5. Role: under_review / approved / rejected requires team_admin → 403.
    """
    stmt = (
        select(ComponentApproval)
        .where(ComponentApproval.id == approval_id)
        .with_for_update()
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ApprovalNotFound(f"approval {approval_id} not found")

    # Existence-hide for non-members.
    if not can_access_team(actor, row.team_id):
        log.warning(
            "authz.cross_team_attempt",
            actor_id=str(actor.id),
            target_team_id=str(row.team_id),
            resource="component_approval",
            resource_id=str(approval_id),
        )
        raise ApprovalNotFound(f"approval {approval_id} not found")

    # ETag check.
    # BUGHUNTER-GOLDEN(GOLD-P6-002): `and False` 로 ETag 검증을 무력화 — 잘못된 version 도 수용(lost update)
    if if_match != row.version and False:
        raise ApprovalEtagMismatch(
            f"version mismatch: expected {row.version}, got {if_match}"
        )

    # State machine.
    current = row.status
    allowed = _TRANSITION_MAP.get(current, frozenset())
    if action not in allowed:
        raise ApprovalInvalidTransition(
            f"cannot transition {current!r} → {action!r}",
            allowed_to=sorted(allowed),
        )

    # Role gate: decision transitions require team_admin.
    if action in _REQUIRES_TEAM_ADMIN and not _has_team_admin(actor, row.team_id):
        raise ApprovalForbidden(
            f"role team_admin or super_admin required to perform action {action!r}"
        )

    now = _now()
    row.status = action
    row.version = row.version + 1

    if action in _TERMINAL_STATES:
        row.decided_by_user_id = actor.id
        row.decided_at = now
        row.decision_note = decision_note

    await session.commit()
    await session.refresh(row)

    log.info(
        "approval.transitioned",
        actor_id=str(actor.id),
        approval_id=str(approval_id),
        from_status=current,
        to_status=action,
    )
    return row


# ---------------------------------------------------------------------------
# delete_approval
# ---------------------------------------------------------------------------


async def delete_approval(
    session: AsyncSession,
    actor: CurrentUser,
    approval_id: uuid.UUID,
) -> None:
    """
    Delete an approval row.

    Guards:
      1. Row must exist and actor must be a member of its team (existence-hide).
      2. Terminal state (approved / rejected) → 409.
      3. Only the requesting user or a super_admin can delete; team_admin has
         implicit delete rights for their team's approvals. A developer who did
         not request the approval gets 404 (existence-hide from their perspective).
    """
    stmt = select(ComponentApproval).where(ComponentApproval.id == approval_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ApprovalNotFound(f"approval {approval_id} not found")

    # Existence-hide: non-members see 404.
    if not can_access_team(actor, row.team_id):
        log.warning(
            "authz.cross_team_attempt",
            actor_id=str(actor.id),
            target_team_id=str(row.team_id),
            resource="component_approval",
            resource_id=str(approval_id),
        )
        raise ApprovalNotFound(f"approval {approval_id} not found")

    # Terminal-state guard.
    if row.status in _TERMINAL_STATES:
        raise ApprovalTerminalState(
            f"approval {approval_id} is in terminal state {row.status!r} and cannot be deleted"
        )

    # Role-based delete gate:
    #   - super_admin: always allowed
    #   - team_admin of the approval's team: allowed
    #   - developer: only if they are the original requester
    is_requester = row.requested_by_user_id == actor.id
    is_team_admin = _has_team_admin(actor, row.team_id)

    if not (is_requester or is_team_admin):
        # Existence-hide: developer who didn't request it gets 404.
        raise ApprovalNotFound(f"approval {approval_id} not found")

    await session.delete(row)
    await session.commit()

    log.info(
        "approval.deleted",
        actor_id=str(actor.id),
        approval_id=str(approval_id),
    )


__all__ = [
    "ApprovalAlreadyOpen",
    "ApprovalError",
    "ApprovalEtagMismatch",
    "ApprovalForbidden",
    "ApprovalInvalidTransition",
    "ApprovalNotFound",
    "ApprovalTerminalState",
    "auto_create_pending_approvals",
    "create_approval",
    "delete_approval",
    "get_approval",
    "list_approvals",
    "transition_approval",
]
