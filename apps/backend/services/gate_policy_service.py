# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Resolving a build-gate policy for a project.

Four sources, most specific first: the project's team, the organization the
team belongs to, the environment variable, and the built-in default. Each
field falls through independently, so a team that pins one threshold does not
inherit the rest from itself; it inherits them from the organization exactly
as if it had written no row at all.

The reason every field is nullable rather than defaulted lives here. A policy
row with defaults filled in would mean "this team has decided everything",
which is almost never true: a team writes a row to change one thing. Storing
NULL for the rest keeps the organization's later change flowing through to
them, which is what an organization-wide policy is for.

Nothing in this module fails a lookup. A deployment with no rows, an
unreachable organization, a malformed value that the database somehow admitted
all resolve to the environment answer, because a gate that cannot read its
policy must fall back to the behaviour it had before the policy existed rather
than open or close on its own.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import GatePolicy, Project, Team

log = structlog.get_logger("services.gate_policy")


@dataclass(frozen=True)
class ResolvedGatePolicy:
    """What the gate should apply, after the fall-through.

    ``None`` on a field means "no policy decided this", and the caller uses its
    environment answer. Keeping the distinction here rather than resolving it
    to a value lets the caller's existing defaults stay the single place those
    defaults are written.
    """

    epss_threshold: float | None = None
    reachable_critical_only: bool | None = None
    malicious_blocks: bool | None = None

    @property
    def is_empty(self) -> bool:
        return (
            self.epss_threshold is None
            and self.reachable_critical_only is None
            and self.malicious_blocks is None
        )


_EMPTY = ResolvedGatePolicy()


async def resolve_for_project(
    session: AsyncSession, project_id: uuid.UUID
) -> ResolvedGatePolicy:
    """Return the policy that applies to ``project_id``.

    One query for the team and organization, one for the two candidate rows.
    The gate runs on every CI poll, so this stays at two round trips rather
    than walking the chain a row at a time.
    """
    scope = (
        await session.execute(
            select(Team.id, Team.organization_id)
            .join(Project, Project.team_id == Team.id)
            .where(Project.id == project_id)
        )
    ).one_or_none()
    if scope is None:
        # The project vanished between the caller's check and here, or has no
        # team. Either way there is no policy to apply and the caller's
        # environment answer stands.
        return _EMPTY

    team_id, organization_id = scope
    rows = (
        await session.execute(
            select(GatePolicy).where(
                GatePolicy.organization_id == organization_id,
                # Not ``in_([team_id, None])``: SQL never matches a NULL that
                # way, so the organization default would be invisible and the
                # fall-through would silently stop at the team row.
                or_(GatePolicy.team_id == team_id, GatePolicy.team_id.is_(None)),
            )
        )
    ).scalars().all()
    if not rows:
        return _EMPTY

    team_row = next((row for row in rows if row.team_id == team_id), None)
    org_row = next((row for row in rows if row.team_id is None), None)

    def pick(field: str) -> object:
        for row in (team_row, org_row):
            if row is None:
                continue
            value = getattr(row, field)
            if value is not None:
                return value
        return None

    return ResolvedGatePolicy(
        epss_threshold=pick("epss_threshold"),  # type: ignore[arg-type]
        reachable_critical_only=pick("reachable_critical_only"),  # type: ignore[arg-type]
        malicious_blocks=pick("malicious_blocks"),  # type: ignore[arg-type]
    )
