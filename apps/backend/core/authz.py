# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Authorization helpers shared across services.

Pieces in this module:

- :func:`can_access_group`, the canonical, cascade-aware "can this actor
  read team/group-scoped resources for this group?" predicate: async, takes
  a session (the cascade check needs to read the target group's ``path``,
  see :func:`services.group_service.can_access_group`), super-admin bypass
  handled here. With :func:`core.config.group_cascade_enabled` ON (the
  default since PR 5-C), it also admits any DESCENDANT of a group the actor
  directly belongs to. OFF, this is exactly the pre-Phase-2 flat "is
  *group_id* literally one of the actor's direct memberships" check, see
  that function's own docstring for the byte-for-byte equivalence argument.

  Phase 2 PR 2-D folded the older, flat-only ``can_access_team`` into this
  function (it used to be a separate sync predicate with no session, kept
  deliberately un-cascaded because converting it meant every caller had to
  become ``async`` + session-carrying). That migration is now done: every
  caller in this module's docstring history (project / project_detail /
  vulnerability / license / obligation / component_approval / ..., ~40 call
  sites across ~22 files, all of them already ``async def`` service
  functions with a session in scope) now awaits ``can_access_group``
  directly or through :func:`assert_team_access` below. There is exactly one
  "can this actor reach this group" primitive in the codebase now, not two
  that can drift out of sync.

- :func:`assert_team_access`, convenience wrapper that does the
  ``if not await can_access_group(...): log + raise`` dance every
  single-resource cross-team gate performs. Centralizing it pins the
  structure of the ``authz.cross_team_attempt`` log event across modules so
  SOC tooling sees a single shape regardless of which surface emitted it.
  Async since Phase 2 PR 2-D, for the same reason ``can_access_group`` is:
  it needs a session to resolve the cascade.

- :func:`is_super_admin`, :func:`super_admin_reaches`,
  :func:`super_admin_group_filter`, :func:`group_visibility_filter`: the one
  place a super admin's bypass is decided. Identity and reach are separate
  questions: on the public read-only demo a super admin reaches only the demo
  organisation, and nothing organisation-wide. Services do not define their
  own ``_is_super_admin``; ``test_no_local_super_admin_shortcuts`` in
  ``tests/unit/test_authz_super_admin_reach.py`` keeps it that way.

- :func:`team_scope_filter`, the single choke-point for *list / fan-out*
  reads (global search, portfolio dashboards, project listings). Phase 2
  PR 2-C made this cascade-aware by delegating its member branch to
  :func:`services.group_service.project_subtree_predicate`; the flag
  :func:`core.config.group_cascade_enabled` lives inside that call, not here;
  this function's own shape (superuser bypass, empty-membership handling)
  is unchanged.

The helpers are deliberately small and side-effect-free apart from the log
line in ``assert_team_access``; services keep their own raise sites for the
domain-specific exceptions (``ProjectForbidden``, ``VulnerabilityNotFound``,
``LicenseFindingNotFound``, ``ObligationNotFound``). The ``deny`` callable
returns the exception so the caller controls the visible message.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from core.config import demo_read_only
from core.security import CurrentUser
from models import Project
from services import demo_scope, group_service


def is_super_admin(actor: CurrentUser) -> bool:
    """Whether *actor* holds the super-admin grade. Identity only, not reach.

    This answers "who is this", never "may they act on that". Every decision
    that lets a super admin past a team, project, or organisation boundary
    goes through :func:`super_admin_reaches` (one resource) or
    :func:`super_admin_group_filter` (a list query), because on a public demo
    the published super-admin accounts reach only the demo organisation, and a
    bare identity check skips that boundary. Use this function directly only
    for presentation (which response shape to render) after the reach decision
    has already been made, or as the first half of a check that then calls one
    of the two reach helpers.

    Both the flag and the derived role are honoured, matching the bypass every
    service used before the helpers existed, so a non-demo deployment decides
    exactly as it did. :func:`team_scope_filter` is the one deliberate
    exception, see its comment.
    """
    return bool(actor.is_superuser) or actor.role == "super_admin"


async def super_admin_reaches(
    session: AsyncSession,
    actor: CurrentUser,
    group_id: uuid.UUID | None,
) -> bool:
    """Whether *actor*'s super-admin authority covers the group *group_id*.

    The single decision for "may a super admin skip the membership check here".
    Callers that used to write ``if actor.is_superuser or actor.role ==
    "super_admin": return True`` now write ``if await super_admin_reaches(
    session, actor, team_id): return True`` and fall through to their own
    membership or role check otherwise.

    - Not a super admin: ``False``.
    - Normal deployment: ``True`` for any target, including ``None``.
    - Public read-only demo (``DEMO_READ_ONLY``), target is a group: ``True``
      only for a group of the demo organisation.
    - Public read-only demo, no target group (``None``): ``False``. Target-less
      resources are organisation-wide ones: an org-scoped API key
      (``team_id`` NULL), organisation settings and templates, organisation
      rulings, the global scan schedule list. Nothing ties them to the demo
      dataset, and the operator's own rows look the same, so a published
      super-admin account is denied the bypass for them. The caller then falls
      through to its ordinary path, which for an organisation-wide write means
      it is refused, and for a read means the caller sees what its own
      memberships allow.

    The ``None`` form is also the question "is this super admin's reach
    unbounded", which is what a list read asks before it drops its tenant
    filter altogether: ``True`` exactly when a normal deployment's super admin
    is asking, so the list runs the unfiltered query it always ran.
    """
    if not is_super_admin(actor):
        return False
    if not demo_read_only():
        return True
    if group_id is None:
        return False
    return await demo_scope.is_demo_group(session, group_id)


def super_admin_reach_predicate(group_column: Any) -> ColumnElement[bool]:
    """The rows a super admin reaches, as a ``WHERE`` clause on *group_column*.

    ``true()`` on a normal deployment. On the public demo, the column must be
    a demo group: a NULL column (an organisation-wide row) compares as NULL
    and is dropped, the list form of the "no target, deny" rule in
    :func:`super_admin_reaches`.
    """
    if demo_read_only():
        predicate: ColumnElement[bool] = group_column.in_(demo_scope.demo_group_ids())
        return predicate
    return sa.true()


def super_admin_group_filter(actor: CurrentUser, group_column: Any) -> ColumnElement[bool] | None:
    """The list-query form of :func:`super_admin_reaches`.

    ``None`` when *actor* is not a super admin: the caller applies its own
    membership filter. Otherwise the clause selecting the rows the super
    admin reaches, which the caller ORs with its membership filter (so a demo
    super admin still sees, say, the keys it issued itself) or uses alone.
    """
    if not is_super_admin(actor):
        return None
    return super_admin_reach_predicate(group_column)


def group_visibility_filter(actor: CurrentUser, group_column: Any) -> ColumnElement[bool] | None:
    """``WHERE`` clause for a list of group-owned rows, or ``None`` for "all".

    Replaces the pattern every list read used to repeat::

        if not (actor.is_superuser or actor.role == "super_admin"):
            stmt = stmt.where(group_scoped_subquery_predicate(col, actor.team_ids))

    - Super admin, normal deployment: ``None``. The caller adds no clause, so
      the query is the one it ran before.
    - Super admin, public demo: rows in a demo group, or in a group the actor
      belongs to. Rows with a NULL group (organisation-wide) are dropped.
    - Everybody else: the membership predicate, cascade-aware.
    """
    member = group_service.group_scoped_subquery_predicate(group_column, actor.team_ids)
    reach = super_admin_group_filter(actor, group_column)
    if reach is None:
        return member
    if not demo_read_only():
        return None
    return sa.or_(reach, member)


async def can_access_group(
    session: AsyncSession,
    actor: CurrentUser,
    group_id: uuid.UUID,
) -> bool:
    """Cascade-aware ``True`` iff *actor* can reach *group_id*.

    The async, session-carrying "can this actor read team/group-scoped
    resources for *group_id*?" predicate. Phase 2 PR 2-C introduced this to
    replace seven ``_can_access_team`` / ``_actor_can_access_team`` /
    ``_may_read_project`` / ``_may_read_team`` / ``_is_team_member``-shaped
    local reimplementations that used to redo ``team_id in actor.team_ids``
    inline; Phase 2 PR 2-D finished the job by folding the module-level
    ``can_access_team`` (the older, flat-only, session-less sibling this
    function used to have) into this one too, so every caller (the original
    seven local reimplementations AND the ~22 files that used to call
    ``can_access_team`` / ``assert_team_access`` directly) now goes through
    exactly this primitive (or the :func:`assert_team_access` wrapper below,
    which now just calls this).

    Super-admin bypass is handled HERE, not in
    :func:`services.group_service.can_access_group`, that function is pure
    cascade logic with no notion of a super-admin, by design (PR 2-A). This
    is the gate layer that layers organization-wide policy (super-admin
    bypass today; a future "read-only org viewer" role tomorrow) on top of
    the pure membership/cascade primitive, so this is also the fix for the
    security-review finding that ``subtree_scope_filter`` alone has no
    super-admin escape hatch: it was never supposed to have one, the escape
    hatch belongs at this layer, and now it exists here.

    With :func:`core.config.group_cascade_enabled` ON (the default since
    PR 5-C), this also admits any descendant of a group in
    *direct_group_ids*: :func:`services.group_service.can_access_group`
    branches on the flag and walks the group's ``path`` for the cascade
    check. OFF, it reduces to exactly the old ``can_access_team``'s flat
    membership check, "is *group_id* literally in *direct_group_ids*".
    """
    if is_super_admin(actor):
        return await super_admin_reaches(session, actor, group_id)
    return await group_service.can_access_group(session, actor.team_ids, group_id)


async def assert_team_access(
    session: AsyncSession,
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

    Phase 2 PR 2-D: this is now a thin wrapper around
    :func:`can_access_group` (previously it wrapped the flat, session-less
    ``can_access_team``, removed in this PR). It is ``async`` and takes a
    *session* for exactly the reason ``can_access_group`` does: the cascade
    check needs to read the target group's ``path``. Every existing caller
    already had a session in scope (they are all ``async def`` service /
    router functions that already load the resource they are gating), so the
    migration is ``assert_team_access(actor, team_id, ...)`` ->
    ``await assert_team_access(session, actor, team_id, ...)``.

    Parameters
    ----------
    session:
        The caller's active :class:`AsyncSession`, passed through to
        :func:`can_access_group`, which needs it to resolve the cascade.
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
    if await can_access_group(session, actor, team_id):
        return
    log.warning(
        "authz.cross_team_attempt",
        actor_id=str(actor.id),
        target_team_id=str(team_id),
        resource=resource,
        resource_id=resource_id,
    )
    raise deny()


def team_scope_filter(actor: CurrentUser) -> ColumnElement[bool]:
    """The single team-isolation predicate for *list / cross-project* reads.

    Where :func:`can_access_group` / :func:`assert_team_access` gate ONE
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
    project-access == team-membership (see ``services.assignee.
    list_assignable_members``), precisely because the cascade this function
    now honours breaks that old assumption.)

    Contract:

    - super-admin (``actor.is_superuser``, see the comment in the body) ->
      :func:`sqlalchemy.true` on a normal deployment (no restriction; sees every
      team's rows), the demo organisation's groups on the public demo.
    - a member, cascade ON (the default since PR 5-C) -> also sees projects
      owned by any DESCENDANT group of a group in ``actor.team_ids``
      (:func:`services.group_service.project_subtree_predicate`;
      :func:`core.config.group_cascade_enabled` is consulted there, not in
      this function).
    - a member, cascade OFF -> result-equivalent to the pre-PR-2-C
      ``Project.team_id IN (actor.team_ids)`` predicate, though not the same
      compiled SQL text: the cascade-aware path routes through a subquery
      against ``groups`` (``Project.team_id IN (SELECT groups.id FROM groups
      WHERE groups.id IN (actor.team_ids))``) rather than a literal IN-list.
      The two are provably equivalent given ``Membership.group_id``'s
      ``ON DELETE CASCADE`` FK to ``groups.id`` and that ``actor.team_ids`` is
      always built from live ``Membership`` rows: every id in it already
      resolves to an existing ``Group`` row, so the extra join never drops or
      adds anything. Security review confirmed this with a passing
      cascade-off matrix test rather than by inspection alone.
    - a member with NO memberships -> matches nothing (an empty-set ``IN``
      subquery, see ``project_subtree_predicate`` / ``subtree_scope_filter``
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
    #
    # This is the one place that keeps the stricter identity rather than
    # calling ``is_super_admin``: the two differ only for a principal whose
    # role reads ``super_admin`` without the flag, which no write path produces.
    # Widening this filter to match would undo H-2; narrowing every single-
    # resource check to match would change who passes them on deployments
    # where tests and fixtures build principals from the role alone. The reach
    # (demo boundary included) is shared with every other check through
    # ``super_admin_reach_predicate``, so only the identity differs.
    if actor.is_superuser:
        return super_admin_reach_predicate(Project.group_id)
    # Phase 2 PR 2-C: the member branch used to inline
    # ``Project.team_id.in_(actor.team_ids)`` here. It now delegates to
    # ``group_service.project_subtree_predicate``, which is this exact
    # predicate when ``group_cascade_enabled()`` is False (PR 2-A's
    # ``subtree_scope_filter`` docstring pins that equivalence) and expands to
    # the group subtree when the flag is on. This is the ONLY behavioural
    # change PR 2-C makes to this function.
    return group_service.project_subtree_predicate(actor.team_ids)


__all__ = [
    "assert_team_access",
    "can_access_group",
    "group_visibility_filter",
    "is_super_admin",
    "super_admin_group_filter",
    "super_admin_reach_predicate",
    "super_admin_reaches",
    "team_scope_filter",
]
