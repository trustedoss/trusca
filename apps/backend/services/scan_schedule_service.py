# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Resolving and writing scheduled-scan cadences (N18).

A project row is authoritative the moment it exists (active or not), and only
its absence falls through to the organization default. This differs from the
gate policy's per-field fall-through (services.gate_policy_service) on purpose:
a schedule is one cohesive decision (when to scan), not several independent
thresholds a team might want to tune one at a time.

Who may write which scope mirrors the gate policy: the organization default is
a super-admin decision, and a project's own schedule is that project's team's
decision (team_admin or above).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import _ROLE_PRIORITY, CurrentUser
from models import Organization, Project, ScanSchedule
from schemas.scan_schedule import ScanScheduleUpsertIn

log = structlog.get_logger("services.scan_schedule")


class ScanScheduleError(Exception):
    """Base for the failures the router turns into Problem Details."""


class ScanScheduleForbidden(ScanScheduleError):
    """Caller may not write at this scope."""


class ScanScheduleScopeNotFound(ScanScheduleError):
    """The project or organization does not exist, or is hidden from the caller."""


def _is_super_admin(actor: CurrentUser) -> bool:
    return actor.is_superuser or actor.role == "super_admin"


def _may_administer_project(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    """Whether ``actor`` may write the schedule of a project owned by ``team_id``.

    Rank rather than equality, matching ``gate_policy_service._may_administer_team``:
    a grade added above team_admin later must not have to be listed here again.
    """
    if _is_super_admin(actor):
        return True
    grade = actor.team_roles.get(team_id)
    if grade is None:
        return False
    return _ROLE_PRIORITY.get(grade, 0) >= _ROLE_PRIORITY["team_admin"]


def _may_read_project(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    return _is_super_admin(actor) or team_id in actor.team_ids


@dataclass(frozen=True)
class ResolvedScanSchedule:
    """What will actually fire for a project, after the fall-through."""

    is_active: bool = False
    cadence: str | None = None
    hour: int | None = None
    day_of_week: int | None = None
    timezone: str | None = None
    #: 'project', 'organization', or 'none'.
    source: str = "none"

    @property
    def fires(self) -> bool:
        return self.is_active and self.cadence is not None


_NONE = ResolvedScanSchedule()


def _from_row(row: ScanSchedule, *, source: str) -> ResolvedScanSchedule:
    return ResolvedScanSchedule(
        is_active=row.is_active,
        cadence=row.cadence,
        hour=row.hour,
        day_of_week=row.day_of_week,
        timezone=row.timezone,
        source=source,
    )


async def _project_team_and_org(
    session: AsyncSession, project_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID] | None:
    """(team_id, organization_id) for ``project_id``, or None if it has vanished."""
    from models import Team

    row = (
        await session.execute(
            select(Team.id, Team.organization_id)
            .join(Project, Project.team_id == Team.id)
            .where(Project.id == project_id)
        )
    ).one_or_none()
    return (row[0], row[1]) if row is not None else None


async def resolve_for_project(
    session: AsyncSession, project_id: uuid.UUID
) -> ResolvedScanSchedule:
    """Return the schedule that applies to ``project_id``.

    A project row (active or not) always wins over the organization default;
    only its absence falls through. This is deliberately whole-row rather than
    per-field: a schedule with no project decision at all inherits the
    organization's cadence outright, but a project that has decided anything
    (even "off") is not blended with it.
    """
    scope = await _project_team_and_org(session, project_id)
    if scope is None:
        return _NONE
    _team_id, organization_id = scope

    rows = (
        await session.execute(
            select(ScanSchedule).where(
                ScanSchedule.organization_id == organization_id,
                (ScanSchedule.project_id == project_id) | (ScanSchedule.project_id.is_(None)),
            )
        )
    ).scalars().all()
    if not rows:
        return _NONE

    project_row = next((r for r in rows if r.project_id == project_id), None)
    if project_row is not None:
        return _from_row(project_row, source="project")

    org_row = next((r for r in rows if r.project_id is None), None)
    if org_row is not None:
        return _from_row(org_row, source="organization")
    return _NONE


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def _resolve_project_scope(
    session: AsyncSession, project_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    scope = await _project_team_and_org(session, project_id)
    if scope is None:
        raise ScanScheduleScopeNotFound(f"project {project_id} not found")
    return scope


async def upsert_project_schedule(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    project_id: uuid.UUID,
    payload: ScanScheduleUpsertIn,
) -> ScanSchedule:
    """Create or replace one project's own schedule.

    Idempotent on the scope: a second PUT updates the row rather than adding a
    second one, which the unique constraint would refuse anyway.
    """
    team_id, organization_id = await _resolve_project_scope(session, project_id)
    if not _may_administer_project(actor, team_id):
        raise ScanScheduleForbidden(
            f"actor may not write the scan schedule for project {project_id}"
        )
    return await _upsert(
        session,
        organization_id=organization_id,
        project_id=project_id,
        payload=payload,
        actor_id=actor.id,
    )


async def upsert_org_schedule(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    organization_id: uuid.UUID,
    payload: ScanScheduleUpsertIn,
) -> ScanSchedule:
    """Create or replace the organization default. Super-admin only.

    The grade that sets a deployment-wide cadence covering every project that
    has not overridden it is the one that answers for the deployment.
    """
    exists = (
        await session.execute(select(Organization.id).where(Organization.id == organization_id))
    ).scalar_one_or_none()
    if exists is None:
        raise ScanScheduleScopeNotFound(f"organization {organization_id} not found")
    if not _is_super_admin(actor):
        raise ScanScheduleForbidden("only a super admin may write the organization scan schedule")
    return await _upsert(
        session,
        organization_id=organization_id,
        project_id=None,
        payload=payload,
        actor_id=actor.id,
    )


async def _upsert(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    project_id: uuid.UUID | None,
    payload: ScanScheduleUpsertIn,
    actor_id: uuid.UUID,
) -> ScanSchedule:
    existing = (
        await session.execute(
            select(ScanSchedule).where(
                ScanSchedule.organization_id == organization_id,
                ScanSchedule.project_id == project_id
                if project_id is not None
                else ScanSchedule.project_id.is_(None),
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = ScanSchedule(
            organization_id=organization_id,
            project_id=project_id,
            created_by_user_id=actor_id,
        )
        session.add(existing)

    existing.is_active = payload.is_active
    existing.cadence = payload.cadence
    existing.hour = payload.hour
    existing.day_of_week = payload.day_of_week
    existing.timezone = payload.timezone
    await session.commit()
    await session.refresh(existing)
    return existing


async def get_project_schedule(
    session: AsyncSession, actor: CurrentUser, *, project_id: uuid.UUID
) -> ScanSchedule | None:
    """The project's own row, or None when it has not written one."""
    team_id, organization_id = await _resolve_project_scope(session, project_id)
    if not _may_read_project(actor, team_id):
        raise ScanScheduleScopeNotFound(f"project {project_id} not found")

    return (
        await session.execute(
            select(ScanSchedule).where(
                ScanSchedule.organization_id == organization_id,
                ScanSchedule.project_id == project_id,
            )
        )
    ).scalar_one_or_none()


async def delete_project_schedule(
    session: AsyncSession, actor: CurrentUser, *, project_id: uuid.UUID
) -> bool:
    """Drop a project's row so it follows its organization default again.

    Returns whether a row was removed, so the router can answer 404 for a
    project that never wrote one rather than reporting a delete that deleted
    nothing.
    """
    team_id, organization_id = await _resolve_project_scope(session, project_id)
    if not _may_administer_project(actor, team_id):
        raise ScanScheduleForbidden(
            f"actor may not write the scan schedule for project {project_id}"
        )

    row = (
        await session.execute(
            select(ScanSchedule).where(
                ScanSchedule.organization_id == organization_id,
                ScanSchedule.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


__all__ = [
    "ResolvedScanSchedule",
    "ScanScheduleForbidden",
    "ScanScheduleScopeNotFound",
    "delete_project_schedule",
    "get_project_schedule",
    "resolve_for_project",
    "upsert_org_schedule",
    "upsert_project_schedule",
]
