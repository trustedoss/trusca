# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Authorization helpers shared across services.

Pieces in this module:

- :func:`can_access_team` — the canonical, FLAT "is this actor a direct
  member of (or a super-admin over) this team?" predicate. Many services
  (project / project_detail / vulnerability / license / obligation / ...)
  route their single-resource cross-team checks through this (or its
  ``assert_team_access`` wrapper below) — the centralized version keeps the
  policy in one place so a future tweak (for example, adding a "read-only org
  viewer" role) only has to land here. **This function does not consult the
  group-hierarchy cascade** (:mod:`services.group_service`) — it is
  deliberately left as the flat, direct-membership-only check every existing
  caller of it already expects, because converting it to the cascade would
  require every one of its ~20 callers to switch from a sync boolean to an
  ``await``-able, session-carrying call. Phase 2 PR 2-C scopes the cascade
  wiring to :func:`team_scope_filter` (below) and to :func:`can_access_group`
  (new in this PR) — see that function's docstring for which call sites were
  actually migrated onto it and which were not.

- :func:`assert_team_access` — convenience wrapper that does the
  ``if not can_access_team(...): log + raise`` dance every cross-team gate
  performs. Centralizing it pins the structure of the
  ``authz.cross_team_attempt`` log event across modules so SOC tooling sees
  a single shape regardless of which surface emitted it.

- :func:`can_access_group` — Phase 2 PR 2-C. The cascade-aware sibling of
  ``can_access_team``: async, takes a session (the cascade check needs to
  read the target group's ``path`` — see
  :func:`services.group_service.can_access_group`), and is what the seven
  local ``_can_access_team``-shaped reimplementations this PR removes now
  call instead of re-deriving ``team_id in actor.team_ids`` locally.

- :func:`team_scope_filter` — the single choke-point for *list / fan-out*
  reads (global search, portfolio dashboards, project listings). Phase 2
  PR 2-C makes this cascade-aware by delegating its member branch to
  :func:`services.group_service.project_subtree_predicate`; the flag
  :func:`core.config.group_cascade_enabled` lives inside that call, not here
  — this function's own shape (superuser bypass, empty-membership handling)
  is unchanged.

The helpers are deliberately small and side-effect-free apart from the log
line in ``assert_team_access`` — services keep their own raise sites for the
domain-specific exceptions (``ProjectForbidden``, ``VulnerabilityNotFound``,
``LicenseFindingNotFound``, ``ObligationNotFound``). The ``deny`` callable
returns the exception so the caller controls the visible message.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from core.security import CurrentUser
from services import group_service


def can_access_team(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    """``True`` iff the actor can read team-scoped resources for *team_id*.

    Super-admins (either ``is_superuser`` or ``role == "super_admin"``) bypass
    the team-membership check; everyone else must have the team in
    ``actor.team_ids``.
    """
    if actor.is_superuser or actor.role == "super_admin":
        return True
    return team_id in actor.team_ids


def assert_team_access(
    actor: CurrentUser,
    team_id: uuid.UUID,
    *,
    log: structlog.stdlib.BoundLogger,
    resource: str,
    resource_id: str,
    deny: Callable[[], Exception],
) -> None:
    """Raise ``deny()`` (after emitting an ``authz.cross_team_attempt`` warning)
    if the actor cannot access *team_id*; otherwise return.

    Parameters
    ----------
    actor:
        The authenticated caller.
    team_id:
        Team owning the resource the caller is trying to read or mutate.
    log:
        Per-module logger so the emitted event carries the module name in
        its logger field. Callers pass their existing
        ``log = structlog.get_logger("...")`` instance.
    resource:
        Short string identifying the resource type (``"project"``,
        ``"vulnerability_finding"``, ``"license_finding"``, ``"obligation"``,
        …). Goes into the log line for SOC routing.
    resource_id:
        Stringified id of the resource the caller asked for. Goes into the
        log line.
    deny:
        Zero-arg callable returning the domain-specific exception to raise
        on denial. Two patterns:

        - 403-visible-existence: ``deny=lambda: ProjectForbidden(...)``
        - 404-existence-hide:    ``deny=lambda: SomeNotFound(...)``

        The helper invokes ``raise deny()``; using a callable instead of an
        eagerly-built exception avoids constructing one on the happy path.
    """
    if can_access_team(actor, team_id):
        return
    log.warning(
        "authz.cross_team_attempt",
        actor_id=str(actor.id),
        target_team_id=str(team_id),
        resource=resource,
        resource_id=resource_id,
    )
    raise deny()


async def can_access_group(
    session: AsyncSession,
    actor: CurrentUser,
    group_id: uuid.UUID,
) -> bool:
    """Cascade-aware ``True`` iff *actor* can reach *group_id*.

    The async, session-carrying counterpart to :func:`can_access_team` —
    Phase 2 PR 2-C's replacement for the seven ``_can_access_team`` /
    ``_actor_can_access_team`` / ``_may_read_project`` / ``_may_read_team`` /
    ``_is_team_member``-shaped local reimplementations that used to redo
    ``team_id in actor.team_ids`` inline. Callers that already had a session
    in scope (every one of those seven — they are all async service
    functions) switch to ``await can_access_group(session, actor, ...)``.

    Super-admin bypass is handled HERE, not in
    :func:`services.group_service.can_access_group` — that function is pure
    cascade logic with no notion of a super-admin, by design (PR 2-A). This
    is the gate layer that layers organization-wide policy (super-admin
    bypass today; a future "read-only org viewer" role tomorrow) on top of
    the pure membership/cascade primitive, so this is also the fix for the
    security-review finding that ``subtree_scope_filter`` alone has no
    super-admin escape hatch: it was never supposed to have one — the escape
    hatch belongs at this layer, and now it exists here.

    With :func:`core.config.group_cascade_enabled` OFF (the default — this PR
    does not flip it), this reduces to exactly ``can_access_team``'s flat
    membership check: :func:`services.group_service.can_access_group` itself
    branches on the flag and returns the same "is *group_id* literally in
    *direct_group_ids*" answer when the cascade is off.
    """
    if actor.is_superuser or actor.role == "super_admin":
        return True
    return await group_service.can_access_group(session, actor.team_ids, group_id)


def team_scope_filter(actor: CurrentUser) -> ColumnElement[bool]:
    """The single team-isolation predicate for *list / cross-project* reads.

    Where :func:`can_access_team` / :func:`assert_team_access` gate ONE
    resource whose ``team_id`` is already loaded, this returns a SQLAlchemy
    boolean expression to drop into ``.where(...)`` so a query only ever sees
    rows in teams the actor may read. It is the mandated choke-point for any
    endpoint that fans out across projects (global search, portfolio
    dashboards): every sub-query filters through THIS helper instead of
    re-deriving ``Project.team_id.in_(...)`` locally, so the isolation policy
    lives in exactly one place and a future tweak (org-wide viewer role, etc.)
    lands here only.

    One caller does not go through this and would be widened by such a tweak
    without touching it: ``GET /v1/projects/{project_id}/assignable-members``
    derives a team from a project and lists its members, which is safe only
    while reaching a project means being on its team. Enabling organization-wide
    visibility makes project access a weaker statement than team membership, so
    read that route before turning it on. (Phase 2 PR 2-C: that route has been
    rewritten to compute its own "effective member set" rather than assume
    project-access == team-membership — see ``services.assignee.
    list_assignable_members`` — precisely because the cascade this function
    now honours breaks that old assumption.)

    Contract:

    - super-admin (``actor.is_superuser`` OR ``actor.role == "super_admin"``)
      → :func:`sqlalchemy.true` (no restriction; sees every team's rows).
    - a member, cascade OFF (default) → result-equivalent to the pre-PR-2-C
      ``Project.team_id IN (actor.team_ids)`` predicate, though not the same
      compiled SQL text: the cascade-aware path routes through a subquery
      against ``groups`` (``Project.team_id IN (SELECT groups.id FROM groups
      WHERE groups.id IN (actor.team_ids))``) rather than a literal IN-list.
      The two are provably equivalent given ``Membership.group_id``'s
      ``ON DELETE CASCADE`` FK to ``groups.id`` and that ``actor.team_ids`` is
      always built from live ``Membership`` rows — every id in it already
      resolves to an existing ``Group`` row, so the extra join never drops or
      adds anything. Security review confirmed this with a passing
      cascade-off matrix test rather than by inspection alone.
    - a member, cascade ON → also sees projects owned by any DESCENDANT group
      of a group in ``actor.team_ids`` (:func:`services.group_service.
      project_subtree_predicate`; :func:`core.config.group_cascade_enabled`
      is consulted there, not in this function).
    - a member with NO memberships → matches nothing (an empty-set ``IN``
      subquery — see ``project_subtree_predicate`` / ``subtree_scope_filter``
      for why that is an explicit ``sqlalchemy.false()`` inside the subquery
      rather than relying on empty-set ``IN ()`` behaviour).

    The predicate references :class:`models.Project`, so every query that uses
    it MUST join ``Project`` into its FROM (directly or transitively). Callers
    that start from ``ScanComponent`` / ``VulnerabilityFinding`` reach
    ``Project`` via ``Scan.project_id`` — see
    :mod:`services.search_service`.
    """
    # Gate the unrestricted (cross-tenant) branch on ``is_superuser`` ALONE, not
    # on the derived ``role == "super_admin"`` string. ``role`` is
    # ``_highest_role``, which would read ``super_admin`` if any ``Membership.role``
    # row ever held that value (the ``user_role`` enum permits it, even though no
    # write path creates one today). Since this helper is the single chokepoint
    # for every cross-project fan-out surface (search now, portfolio views later),
    # keying the bypass on the authoritative ``users.is_superuser`` flag closes
    # that latent membership-role escalation path centrally (security-review H-2,
    # Low-2 defense-in-depth).
    if actor.is_superuser:
        return sa.true()
    # Phase 2 PR 2-C: the member branch used to inline
    # ``Project.team_id.in_(actor.team_ids)`` here. It now delegates to
    # ``group_service.project_subtree_predicate``, which is this exact
    # predicate when ``group_cascade_enabled()`` is False (PR 2-A's
    # ``subtree_scope_filter`` docstring pins that equivalence) and expands to
    # the group subtree when the flag is on. This is the ONLY behavioural
    # change PR 2-C makes to this function.
    return group_service.project_subtree_predicate(actor.team_ids)


__all__ = ["assert_team_access", "can_access_group", "can_access_team", "team_scope_filter"]
