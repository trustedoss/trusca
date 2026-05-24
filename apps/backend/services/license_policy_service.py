"""
License policy service — v2.2 (Track C — c1 "per-team/org dynamic license policy").

Pure async DB I/O for the ``/v1/license-policies`` HTTP surface. Mirrors the
shape of :mod:`services.api_key_service` (domain exceptions carrying
``status_code`` / ``title``, ``bind_audit_team`` before commit, structlog).

What c1 delivers (THIS PR):
  - CRUD on ``license_policies`` rows (upsert team / org, get, list, delete).
  - RBAC: team policies require ``team_admin`` of that team (or super_admin);
    org-default policies require super_admin.
  - ``get_effective_policy`` — the precedence resolver c2 will call:
        team (present + enabled) → org default (present + enabled) → None.
  - ``effective_category`` — a PURE helper that resolves a SINGLE, SIMPLE SPDX
    id (NOT a compound expression) against a policy + a static-catalog default.

What c1 does NOT do:
  - It does NOT touch ``services.policy_gate``. Wiring the gate to consult these
    policies is c2.
  - ``effective_category`` handles ONLY a single SPDX id. Compound expressions
    (``A AND B``, ``A OR B``, ``A WITH exc``) and SPDX adversarial-input
    hardening are c2 (it will consume ``compound_operator_strategy``). Passing a
    compound string here is the caller's bug; this helper does not split it.

Audit:
  The SQLAlchemy ``before_flush`` listener emits an ``audit_logs`` row for each
  INSERT / UPDATE / DELETE on ``license_policies``. Services call
  :func:`core.audit.bind_audit_team` after the access gate and before the
  mutating commit so the resulting ``audit_logs.team_id`` is non-NULL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from core.audit import bind_audit_team
from core.security import CurrentUser
from models import LicensePolicy, Team
from schemas.license_policy import LicensePolicyUpsertIn

log = structlog.get_logger("license_policy.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class LicensePolicyError(Exception):
    """Base class for license-policy domain errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "License Policy Error"


class LicensePolicyNotFound(LicensePolicyError):
    status_code = 404
    title = "License Policy Not Found"


class LicensePolicyForbidden(LicensePolicyError):
    status_code = 403
    title = "Forbidden"


class LicensePolicyConflict(LicensePolicyError):
    """409 — uniqueness conflict (e.g. a second org-default for the same org)."""

    status_code = 409
    title = "License Policy Conflict"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _is_super_admin(actor: CurrentUser) -> bool:
    return actor.is_superuser or actor.role == "super_admin"


def _can_admin_team(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    """True iff *actor* may write the policy of *team_id* (team_admin or super)."""
    if _is_super_admin(actor):
        return True
    return actor.team_roles.get(team_id) == "team_admin"


def _is_team_member(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    """True iff *actor* may READ the effective policy for *team_id*."""
    if _is_super_admin(actor):
        return True
    return team_id in actor.team_ids


def _apply_upsert(row: LicensePolicy, payload: LicensePolicyUpsertIn) -> None:
    """Copy validated upsert fields onto an ORM row (create or update path)."""
    # ``mode="json"`` collapses the Literal-typed dicts to plain ``dict[str, str]``
    # / JSON-safe values so the JSONB columns (``dict[str, Any]``) accept them and
    # mypy sees compatible types.
    dumped = payload.model_dump(mode="json")
    row.name = payload.name
    row.category_overrides = dumped["category_overrides"]
    row.license_exceptions = dumped["license_exceptions"]
    row.unknown_license_category = payload.unknown_license_category
    row.compound_operator_strategy = dumped["compound_operator_strategy"]
    row.enabled = payload.enabled


async def _resolve_team_org(session: AsyncSession, team_id: uuid.UUID) -> uuid.UUID:
    """Return the organization_id for *team_id*, or raise 404 if the team is gone."""
    org_id = (
        await session.execute(select(Team.organization_id).where(Team.id == team_id))
    ).scalar_one_or_none()
    if org_id is None:
        # Existence-hide: don't leak whether the team exists.
        raise LicensePolicyNotFound(f"team {team_id} not found")
    return org_id


# ---------------------------------------------------------------------------
# upsert_team_policy
# ---------------------------------------------------------------------------


async def upsert_team_policy(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    team_id: uuid.UUID,
    payload: LicensePolicyUpsertIn,
) -> LicensePolicy:
    """
    Create or update the license policy for *team_id*.

    RBAC: ``team_admin`` of *team_id* (or super_admin). A second PUT UPDATES the
    existing row (idempotent on the (org, team) scope — no duplicate created).
    """
    org_id = await _resolve_team_org(session, team_id)

    if not _can_admin_team(actor, team_id):
        raise LicensePolicyForbidden(
            f"actor lacks permission to write the license policy for team {team_id}"
        )

    bind_audit_team(team_id)

    existing = (
        await session.execute(
            select(LicensePolicy).where(
                LicensePolicy.organization_id == org_id,
                LicensePolicy.team_id == team_id,
            )
        )
    ).scalar_one_or_none()

    created = existing is None
    if existing is None:
        row = LicensePolicy(
            organization_id=org_id,
            team_id=team_id,
            created_by_user_id=actor.id,
        )
        _apply_upsert(row, payload)
        session.add(row)
    else:
        row = existing
        _apply_upsert(row, payload)
        row.updated_at = _now()

    try:
        await session.commit()
    except IntegrityError as exc:  # pragma: no cover - guarded by the SELECT above
        await session.rollback()
        raise LicensePolicyConflict(
            f"license policy for team {team_id} conflicts with an existing row"
        ) from exc

    await session.refresh(row)
    log.info(
        "license_policy.team_upsert",
        actor_id=str(actor.id),
        policy_id=str(row.id),
        team_id=str(team_id),
        created=created,
        enabled=row.enabled,
    )
    return row


# ---------------------------------------------------------------------------
# upsert_org_policy
# ---------------------------------------------------------------------------


async def upsert_org_policy(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    organization_id: uuid.UUID,
    payload: LicensePolicyUpsertIn,
) -> LicensePolicy:
    """
    Create or update the ORG-DEFAULT license policy (``team_id IS NULL``).

    RBAC: super_admin ONLY. At most one org-default per org — the partial unique
    index ``uq_license_policies_org_default`` backstops this; the service finds
    the existing row first so the common path is a clean UPDATE.
    """
    if not _is_super_admin(actor):
        raise LicensePolicyForbidden("only super_admin may write the org-default license policy")

    existing = (
        await session.execute(
            select(LicensePolicy).where(
                LicensePolicy.organization_id == organization_id,
                LicensePolicy.team_id.is_(None),
            )
        )
    ).scalar_one_or_none()

    created = existing is None
    if existing is None:
        row = LicensePolicy(
            organization_id=organization_id,
            team_id=None,
            created_by_user_id=actor.id,
        )
        _apply_upsert(row, payload)
        session.add(row)
    else:
        row = existing
        _apply_upsert(row, payload)
        row.updated_at = _now()

    try:
        await session.commit()
    except IntegrityError as exc:
        # A concurrent creator raced us to the single org-default slot. Surface a
        # clean 409, never a 500.
        await session.rollback()
        raise LicensePolicyConflict(
            f"an org-default license policy already exists for organization " f"{organization_id}"
        ) from exc

    await session.refresh(row)
    log.info(
        "license_policy.org_upsert",
        actor_id=str(actor.id),
        policy_id=str(row.id),
        organization_id=str(organization_id),
        created=created,
        enabled=row.enabled,
    )
    return row


# ---------------------------------------------------------------------------
# get_policy / get_effective_policy
# ---------------------------------------------------------------------------


async def get_team_policy_row(session: AsyncSession, *, team_id: uuid.UUID) -> LicensePolicy | None:
    """Return the team's own policy row (regardless of ``enabled``), or None."""
    return (
        await session.execute(select(LicensePolicy).where(LicensePolicy.team_id == team_id))
    ).scalar_one_or_none()


async def get_org_default_policy_row(
    session: AsyncSession, *, organization_id: uuid.UUID
) -> LicensePolicy | None:
    """Return the org-default policy row (regardless of ``enabled``), or None."""
    return (
        await session.execute(
            select(LicensePolicy).where(
                LicensePolicy.organization_id == organization_id,
                LicensePolicy.team_id.is_(None),
            )
        )
    ).scalar_one_or_none()


async def get_policy(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    team_id: uuid.UUID,
) -> LicensePolicy:
    """
    Return the EFFECTIVE policy for *team_id* (the c2 resolution order):
        team policy (present + enabled) → org default (present + enabled) → 404.

    RBAC: any member of *team_id* (or super_admin). Existence-hide: a non-member
    gets 403 (membership is the gate); a member with no policy at any scope gets
    404 so the UI can render "no policy → static fallback".
    """
    org_id = await _resolve_team_org(session, team_id)

    if not _is_team_member(actor, team_id):
        raise LicensePolicyForbidden(f"actor is not a member of team {team_id}")

    effective = await get_effective_policy(session, team_id=team_id, organization_id=org_id)
    if effective is None:
        raise LicensePolicyNotFound(
            f"no enabled license policy applies to team {team_id} "
            f"(falls back to the static catalog)"
        )
    return effective


async def get_org_policy(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    organization_id: uuid.UUID,
) -> LicensePolicy:
    """
    Return the org-default policy row.

    RBAC is enforced at the router via ``require_super_admin_or_404`` (admin
    existence-hide). This service raises 404 if no org default exists.
    """
    row = await get_org_default_policy_row(session, organization_id=organization_id)
    if row is None:
        raise LicensePolicyNotFound(
            f"no org-default license policy for organization {organization_id}"
        )
    return row


async def get_effective_policy(
    session: AsyncSession,
    *,
    team_id: uuid.UUID,
    organization_id: uuid.UUID | None = None,
) -> LicensePolicy | None:
    """
    Resolve the policy that applies to *team_id*, in precedence order:

        team policy (present AND enabled)
          else org-default policy (present AND enabled)
            else None  → caller falls back to the static catalog.

    A DISABLED team policy is skipped (falls through to the org default); a
    disabled org default yields None. This is the resolver c2 calls before
    classifying a license. ``organization_id`` is resolved from the team when
    not supplied.
    """
    team_row = await get_team_policy_row(session, team_id=team_id)
    if team_row is not None and team_row.enabled:
        return team_row

    org_id = organization_id
    if org_id is None:
        org_id = (
            await session.execute(select(Team.organization_id).where(Team.id == team_id))
        ).scalar_one_or_none()
        if org_id is None:
            return None

    org_row = await get_org_default_policy_row(session, organization_id=org_id)
    if org_row is not None and org_row.enabled:
        return org_row
    return None


# ---------------------------------------------------------------------------
# list_policies
# ---------------------------------------------------------------------------


async def list_policies(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    organization_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[LicensePolicy], int]:
    """
    Return a paginated list of policies visible to *actor*.

    Visibility:
      - super_admin: all policies (optionally filtered by org / team).
      - everyone else: only policies for teams they belong to. Org-default rows
        (team_id IS NULL) are visible to a member of any team in that org.
    """
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)

    base = select(LicensePolicy)
    count_base = select(func.count()).select_from(LicensePolicy)

    if not _is_super_admin(actor):
        team_ids = list(actor.team_ids)
        if not team_ids:
            # No memberships → nothing visible.
            return [], 0
        # Org ids the actor can see org-defaults for: orgs of the actor's teams.
        org_rows = (
            await session.execute(
                select(Team.id, Team.organization_id).where(Team.id.in_(team_ids))
            )
        ).all()
        visible_org_ids = {r[1] for r in org_rows}
        visibility: ColumnElement[bool] = LicensePolicy.team_id.in_(team_ids)
        if visible_org_ids:
            visibility = or_(
                LicensePolicy.team_id.in_(team_ids),
                and_(
                    LicensePolicy.team_id.is_(None),
                    LicensePolicy.organization_id.in_(visible_org_ids),
                ),
            )
        base = base.where(visibility)
        count_base = count_base.where(visibility)

    if organization_id is not None:
        base = base.where(LicensePolicy.organization_id == organization_id)
        count_base = count_base.where(LicensePolicy.organization_id == organization_id)
    if team_id is not None:
        base = base.where(LicensePolicy.team_id == team_id)
        count_base = count_base.where(LicensePolicy.team_id == team_id)

    total = int((await session.execute(count_base)).scalar_one())
    rows_stmt = (
        base.order_by(LicensePolicy.created_at.desc(), LicensePolicy.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = list((await session.execute(rows_stmt)).scalars().all())

    log.info(
        "license_policy.list",
        actor_id=str(actor.id),
        total=total,
        page=page,
        page_size=page_size,
    )
    return rows, total


# ---------------------------------------------------------------------------
# delete_team_policy
# ---------------------------------------------------------------------------


async def delete_team_policy(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    team_id: uuid.UUID,
) -> None:
    """
    Delete (reset) the team's own license policy.

    RBAC: ``team_admin`` of *team_id* (or super_admin). Idempotent — deleting a
    non-existent policy raises 404 so the caller can distinguish "already gone".
    After deletion the team falls back to the org default (or the static
    catalog).
    """
    org_id = await _resolve_team_org(session, team_id)

    if not _can_admin_team(actor, team_id):
        raise LicensePolicyForbidden(
            f"actor lacks permission to reset the license policy for team {team_id}"
        )

    row = (
        await session.execute(
            select(LicensePolicy).where(
                LicensePolicy.organization_id == org_id,
                LicensePolicy.team_id == team_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise LicensePolicyNotFound(f"no license policy for team {team_id}")

    bind_audit_team(team_id)
    await session.delete(row)
    await session.commit()

    log.info(
        "license_policy.team_deleted",
        actor_id=str(actor.id),
        team_id=str(team_id),
        policy_id=str(row.id),
    )


# ---------------------------------------------------------------------------
# effective_category — PURE single-SPDX-id resolver (c1 scope)
# ---------------------------------------------------------------------------


def effective_category(
    spdx_id: str,
    policy: LicensePolicy | None,
    static_default: str,
) -> str:
    """
    Resolve the effective category for a SINGLE, SIMPLE SPDX id.

    Precedence:
      1. A matching, non-expired entry in ``policy.license_exceptions`` (matched
         by ``spdx_id``, ignoring component-purl-scoped exceptions here — c2
         applies purl scoping with the component in hand) → ``"allowed"``.
      2. ``policy.category_overrides[spdx_id]`` if present → that category.
      3. If *spdx_id* is recognised by the static catalog (``static_default`` is
         a concrete category, i.e. NOT ``"unknown"``) → ``static_default``.
      4. Otherwise (uncatalogued / static_default == "unknown") →
         ``policy.unknown_license_category``.

    When *policy* is None OR ``policy.enabled`` is False, the static behaviour is
    returned unchanged (``static_default``) — the dynamic policy is off.

    SCOPE NOTE (c1 only): this handles ONE simple SPDX identifier. Compound
    expressions (``A AND B`` / ``A OR B`` / ``A WITH exc``) and SPDX
    adversarial-input hardening are c2 — c2 splits the expression and combines
    sub-verdicts per ``policy.compound_operator_strategy``. Do NOT pass a
    compound string here; this function does not split it.
    """
    if policy is None or not policy.enabled:
        return static_default

    # 1. Exception (org/team-wide; purl-scoped exceptions deferred to c2).
    now = _now()
    for exc in policy.license_exceptions or []:
        if not isinstance(exc, dict):
            continue
        if exc.get("spdx_id") != spdx_id:
            continue
        # A purl-scoped exception is NOT applied here (no component in hand).
        if exc.get("component_purl"):
            continue
        expires_at = exc.get("expires_at")
        if expires_at is not None and _is_expired(expires_at, now):
            continue
        return "allowed"

    # 2. Explicit override.
    overrides = policy.category_overrides or {}
    if spdx_id in overrides:
        return str(overrides[spdx_id])

    # 3. Static catalog verdict, if concrete.
    if static_default and static_default != "unknown":
        return static_default

    # 4. Uncatalogued posture.
    return policy.unknown_license_category


def _is_expired(expires_at: Any, now: datetime) -> bool:
    """True if the (ISO-8601 string or datetime) *expires_at* is in the past."""
    if isinstance(expires_at, datetime):
        dt = expires_at
    elif isinstance(expires_at, str):
        try:
            dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            # Unparseable expiry → treat as no expiry (defensive; validated at
            # write time by the Pydantic schema, so this is belt-and-braces).
            return False
    else:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt < now


__all__ = [
    "LicensePolicyConflict",
    "LicensePolicyError",
    "LicensePolicyForbidden",
    "LicensePolicyNotFound",
    "delete_team_policy",
    "effective_category",
    "get_effective_policy",
    "get_org_default_policy_row",
    "get_org_policy",
    "get_policy",
    "get_team_policy_row",
    "list_policies",
    "upsert_org_policy",
    "upsert_team_policy",
]
