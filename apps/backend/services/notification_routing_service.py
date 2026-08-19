# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Deciding who else hears about a notification (N9).

One function does the work that matters: :func:`resolve_extra_delivery` takes
a notification about to be sent and answers with the channels and addresses
that rules add to it. Everything else here is the writing surface.

Three properties shape it, and each is asserted rather than assumed:

Rules add and never subtract. A person's own channel toggles still decide what
reaches that person; a rule decides who else hears. If a rule could remove a
recipient, two mechanisms would answer one question and the silencing one
would win an argument nobody had.

No rules means nothing changes. The resolver returns empty for a deployment
that has written none, and the caller unions empty into what it already had,
so every notification goes exactly where it went before.

Every matching rule contributes. They do not fall through the way the gate
thresholds do and do not override each other, because each one is somebody
saying "also tell us", and any precedence order would silently drop one of
those requests.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import structlog
from sqlalchemy import Select, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from core.security import _ROLE_PRIORITY, CurrentUser
from models import NotificationRoutingRule, Organization, Project, Team
from schemas.notification_routing import NotificationRoutingRuleIn

log = structlog.get_logger("services.notification_routing")

#: Severity order, worst first. A rule naming a minimum matches that severity
#: and everything above it, so the comparison is a position in this list.
#: ``info`` and ``unknown`` sit at the bottom and are reachable only by a rule
#: that names them, which is the reading an operator writing "at least info"
#: expects.
_SEVERITY_ORDER: tuple[str, ...] = (
    "critical",
    "high",
    "medium",
    "low",
    "info",
    "unknown",
)


@dataclass(frozen=True)
class ExtraDelivery:
    """What rules add to a notification that was going out anyway."""

    channels: list[str] = field(default_factory=list)
    recipients: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.channels and not self.recipients


_NOTHING = ExtraDelivery()


def _severity_rank(severity: str | None) -> int | None:
    if not severity:
        return None
    try:
        return _SEVERITY_ORDER.index(severity.strip().lower())
    except ValueError:
        return None


def _matches(
    rule: NotificationRoutingRule,
    *,
    kind: str,
    severity: str | None,
    project_id: uuid.UUID | None,
) -> bool:
    """Whether one rule's condition covers this notification.

    An absent condition matches everything, which is what makes a rule with no
    conditions the "tell us about all of it" an organization writes first.

    A condition the notification cannot answer does not match. A rule naming a
    minimum severity does not fire for a notification carrying no severity at
    all: the operator asked about severe things, and "unknown" is not an
    answer to that question, it is the absence of one.
    """
    kinds = [k for k in (rule.kinds or []) if isinstance(k, str)]
    if kinds and kind not in kinds:
        return False

    if rule.min_severity is not None:
        floor = _severity_rank(rule.min_severity)
        actual = _severity_rank(severity)
        if floor is None or actual is None:
            return False
        # Lower index is worse, so "at least high" means index <= index(high).
        if actual > floor:
            return False

    if rule.project_id is not None and rule.project_id != project_id:
        return False

    return True


def _collect(
    rules: list[NotificationRoutingRule],
    *,
    kind: str,
    severity: str | None,
    project_id: uuid.UUID | None,
) -> ExtraDelivery:
    channels: list[str] = []
    recipients: list[str] = []
    seen_channels: set[str] = set()
    seen_recipients: set[str] = set()

    for rule in rules:
        if not _matches(rule, kind=kind, severity=severity, project_id=project_id):
            continue
        for channel in rule.channels or []:
            if isinstance(channel, str) and channel not in seen_channels:
                seen_channels.add(channel)
                channels.append(channel)
        for address in rule.email_recipients or []:
            if not isinstance(address, str):
                continue
            # Addresses are compared lowercased because two rules naming the
            # same team in different case are one recipient, and a person who
            # receives the same alert twice stops reading either copy.
            folded = address.strip().lower()
            if folded and folded not in seen_recipients:
                seen_recipients.add(folded)
                recipients.append(folded)

    if not channels and not recipients:
        return _NOTHING
    return ExtraDelivery(channels=channels, recipients=recipients)


def _scope_query(project_id: uuid.UUID) -> Select[tuple[uuid.UUID, uuid.UUID]]:
    """The project's team and the organization that team belongs to.

    One statement rather than two: this runs inside the notification path, and
    a second round trip per notification is a cost every deployment pays
    whether or not it has written a single rule.
    """
    return (
        select(Team.id, Team.organization_id)
        .join(Project, Project.team_id == Team.id)
        .where(Project.id == project_id)
    )


def _active_rules_query(
    organization_id: uuid.UUID, team_id: uuid.UUID
) -> Select[tuple[NotificationRoutingRule]]:
    """Rules that could fire for a project in this team.

    One expression for both entry points. It was written twice, once for the
    worker and once for the API, and a mutation that dropped the NULL branch
    from the worker's copy left every test green because the tests reached the
    other one.

    Not ``in_([team_id, None])``: SQL never matches a NULL that way, so every
    organization-wide rule would be invisible and the feature would look as
    though it only supported team rules.
    """
    return select(NotificationRoutingRule).where(
        NotificationRoutingRule.organization_id == organization_id,
        NotificationRoutingRule.is_active.is_(True),
        or_(
            NotificationRoutingRule.team_id == team_id,
            NotificationRoutingRule.team_id.is_(None),
        ),
    )


def resolve_extra_delivery_sync(
    session: Session,
    *,
    kind: str,
    severity: str | None,
    project_id: uuid.UUID | None,
) -> ExtraDelivery:
    """The Celery-side entry point. Synchronous, because the worker is.

    Scoped by the project's team and organization. A notification with no
    project cannot be scoped to either, so no rule fires: an organization rule
    is still a statement about that organization's work, and matching it to a
    notification whose subject is unknown would send somebody else's alert to
    the address in it.
    """
    if project_id is None:
        return _NOTHING

    scope = session.execute(_scope_query(project_id)).one_or_none()
    if scope is None:
        return _NOTHING
    team_id, organization_id = scope

    rules = list(
        session.execute(_active_rules_query(organization_id, team_id)).scalars().all()
    )
    return _collect(rules, kind=kind, severity=severity, project_id=project_id)


async def resolve_extra_delivery(
    session: AsyncSession,
    *,
    kind: str,
    severity: str | None,
    project_id: uuid.UUID | None,
) -> ExtraDelivery:
    """Async twin of :func:`resolve_extra_delivery_sync`, for API callers."""
    if project_id is None:
        return _NOTHING

    scope = (await session.execute(_scope_query(project_id))).one_or_none()
    if scope is None:
        return _NOTHING
    team_id, organization_id = scope

    rules = list(
        (await session.execute(_active_rules_query(organization_id, team_id)))
        .scalars()
        .all()
    )
    return _collect(rules, kind=kind, severity=severity, project_id=project_id)


# ---------------------------------------------------------------------------
# Writing rules
#
# Scope decides who may write: an organization rule is a statement about the
# whole deployment, so a super admin writes it; a team rule is that team's
# business, so its administrator does. The same split the gate policy uses.
# ---------------------------------------------------------------------------


class RoutingRuleError(Exception):
    """Base for the failures the router turns into Problem Details."""


class RoutingRuleForbidden(RoutingRuleError):
    """Caller may not write at this scope."""


class RoutingRuleScopeNotFound(RoutingRuleError):
    """The team, organization or rule does not exist, or is hidden."""


def _is_super_admin(actor: CurrentUser) -> bool:
    return actor.is_superuser or actor.role == "super_admin"


def _may_administer_team(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    """Rank rather than equality, so a grade added above team_admin still
    passes. Asking "is this grade team_admin" answered no for any higher one
    and had to be corrected elsewhere in this codebase once already."""
    if _is_super_admin(actor):
        return True
    grade = actor.team_roles.get(team_id)
    if grade is None:
        return False
    return _ROLE_PRIORITY.get(grade, 0) >= _ROLE_PRIORITY["team_admin"]


async def _organization_of(session: AsyncSession, team_id: uuid.UUID) -> uuid.UUID:
    row = (
        await session.execute(select(Team.organization_id).where(Team.id == team_id))
    ).scalar_one_or_none()
    if row is None:
        raise RoutingRuleScopeNotFound(f"team {team_id} not found")
    return row


async def _assert_project_is_in_scope(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    organization_id: uuid.UUID,
    team_id: uuid.UUID | None,
) -> None:
    """A rule may only name a project it could ever fire for.

    Without this a team administrator could write a rule naming another
    team's project and, since rules add recipients, point that project's
    alerts at an address of their choosing. The rule would never fire, because
    resolution scopes by the project's own team first, but the row would sit
    there reading as though it might.
    """
    scope = (
        await session.execute(
            select(Team.id, Team.organization_id)
            .join(Project, Project.team_id == Team.id)
            .where(Project.id == project_id)
        )
    ).one_or_none()
    if scope is None:
        raise RoutingRuleScopeNotFound(f"project {project_id} not found")
    project_team_id, project_org_id = scope
    if project_org_id != organization_id:
        raise RoutingRuleScopeNotFound(f"project {project_id} not found")
    if team_id is not None and project_team_id != team_id:
        raise RoutingRuleScopeNotFound(f"project {project_id} not found")


async def create_rule(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    organization_id: uuid.UUID | None,
    team_id: uuid.UUID | None,
    payload: NotificationRoutingRuleIn,
) -> NotificationRoutingRule:
    """Write one rule at the organization or a team.

    Exactly one of ``organization_id`` and ``team_id`` is given; the router
    decides which from the path it served.
    """
    if team_id is not None:
        resolved_org = await _organization_of(session, team_id)
        if not _may_administer_team(actor, team_id):
            raise RoutingRuleForbidden(
                f"actor may not write notification rules for team {team_id}"
            )
    else:
        if organization_id is None:
            raise RoutingRuleScopeNotFound("no scope given")
        exists = (
            await session.execute(
                select(Organization.id).where(Organization.id == organization_id)
            )
        ).scalar_one_or_none()
        if exists is None:
            raise RoutingRuleScopeNotFound(f"organization {organization_id} not found")
        if not _is_super_admin(actor):
            raise RoutingRuleForbidden(
                "only a super admin may write organization-wide notification rules"
            )
        resolved_org = organization_id

    if payload.project_id is not None:
        await _assert_project_is_in_scope(
            session,
            project_id=payload.project_id,
            organization_id=resolved_org,
            team_id=team_id,
        )

    rule = NotificationRoutingRule(
        organization_id=resolved_org,
        team_id=team_id,
        name=payload.name,
        kinds=list(payload.kinds),
        min_severity=payload.min_severity,
        project_id=payload.project_id,
        channels=list(payload.channels),
        email_recipients=[str(address).strip().lower() for address in payload.email_recipients],
        is_active=payload.is_active,
    )
    session.add(rule)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # The one constraint a caller can trip: a rule that names neither a
        # channel nor a recipient does nothing, and a row that does nothing is
        # one an operator will later read as broken.
        raise RoutingRuleInvalid(
            "a rule must name at least one channel or one recipient"
        ) from exc
    await session.refresh(rule)
    log.info(
        "notification_routing_rule_created",
        rule_id=str(rule.id),
        organization_id=str(resolved_org),
        team_id=str(team_id) if team_id else None,
        actor_id=str(actor.id),
    )
    return rule


class RoutingRuleInvalid(RoutingRuleError):
    """The rule as written cannot do anything."""


async def list_rules(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    organization_id: uuid.UUID | None,
    team_id: uuid.UUID | None,
) -> list[NotificationRoutingRule]:
    """Rules at one scope.

    A team's list includes the organization's rules, because those apply to
    the team and an administrator reading "what reaches my team" should not
    have to know they are stored one level up.
    """
    if team_id is not None:
        resolved_org = await _organization_of(session, team_id)
        if not _is_super_admin(actor) and team_id not in actor.team_ids:
            # Hidden rather than refused: somebody outside the team has no
            # business learning which teams exist from this endpoint.
            raise RoutingRuleScopeNotFound(f"team {team_id} not found")
        condition = or_(
            NotificationRoutingRule.team_id == team_id,
            NotificationRoutingRule.team_id.is_(None),
        )
    else:
        if organization_id is None:
            raise RoutingRuleScopeNotFound("no scope given")
        if not _is_super_admin(actor):
            raise RoutingRuleScopeNotFound(f"organization {organization_id} not found")
        resolved_org = organization_id
        condition = NotificationRoutingRule.team_id.is_(None)

    return list(
        (
            await session.execute(
                select(NotificationRoutingRule)
                .where(
                    NotificationRoutingRule.organization_id == resolved_org,
                    condition,
                )
                .order_by(
                    NotificationRoutingRule.created_at.asc(),
                    NotificationRoutingRule.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )


async def delete_rule(
    session: AsyncSession, actor: CurrentUser, *, rule_id: uuid.UUID
) -> None:
    """Remove a rule. Whoever could write it at its scope may remove it."""
    rule = (
        await session.execute(
            select(NotificationRoutingRule).where(NotificationRoutingRule.id == rule_id)
        )
    ).scalar_one_or_none()
    if rule is None:
        raise RoutingRuleScopeNotFound(f"rule {rule_id} not found")

    if rule.team_id is not None:
        if not _may_administer_team(actor, rule.team_id):
            raise RoutingRuleScopeNotFound(f"rule {rule_id} not found")
    elif not _is_super_admin(actor):
        raise RoutingRuleScopeNotFound(f"rule {rule_id} not found")

    await session.delete(rule)
    await session.commit()
    log.info(
        "notification_routing_rule_deleted",
        rule_id=str(rule_id),
        actor_id=str(actor.id),
    )
