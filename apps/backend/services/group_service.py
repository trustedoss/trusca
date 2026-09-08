# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Group-hierarchy permission cascade — Phase 2 PR 2-A (definitions only).

This module defines the pure functions and SQL predicates a permission
cascade needs to resolve "what role does this actor have at this group" and
"which groups can this actor reach" against the tree ``parent_group_id`` /
``path`` (migrations 0090/0091, Phase 1) describe. **Nothing in the running
application calls anything in this module yet.** ``core/authz.py`` still
does exactly what it did before this file existed; wiring this module into
it is PR 2-C's job, not this one's. The point of shipping the cascade logic
on its own, unreachable from any route, is to let it be tested exhaustively
in isolation before anything depends on it.

Vocabulary, matching the task's design note:

- **Direct membership**: a row in ``memberships`` exactly as it is today.
- **Effective role at group G**: walk from G itself up to the root,
  ancestor by ancestor, and take the role of the FIRST direct membership
  found. The nearest ancestor wins — including a *demotion*: a
  ``group_admin`` at a parent does not survive at a child that has its own,
  lower-role, direct membership. Sibling branches are never consulted; a
  membership at group X says nothing about a sibling of X, however deep.
  This is the CWE-863 (incorrect authorization) invariant this whole design
  exists to hold, and the parametrized test in this PR's test file pins it
  directly (case 1: sibling access).
- **Accessible set**: the union of the subtrees rooted at every group the
  actor has a direct membership in.

Why the accessible set is computed as a query-time predicate, not a cached
list of ids: caching an expanded id list means invalidating that cache
correctly on every membership or hierarchy change (a demotion three levels
up, a reparent, a group deleted out from under a cached entry) — exactly the
kind of cache-invalidation bug class this design avoids by construction.
:func:`subtree_scope_filter` re-derives the accessible set from
``direct_group_ids`` (which the caller already has — it is the set of
groups the actor holds a ``memberships`` row in, no caching problem there
because it changes only when the actor's own memberships change) on every
call, using an index-backed SQL predicate instead of an expanded id list.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from core.config import group_cascade_enabled
from models import Project
from models.auth import Group

__all__ = [
    "can_access_group",
    "effective_role_at",
    "group_scoped_subquery_predicate",
    "project_subtree_predicate",
    "subtree_roots",
    "subtree_scope_filter",
]


def subtree_roots(direct_group_ids: Sequence[uuid.UUID]) -> Sequence[uuid.UUID]:
    """Return *direct_group_ids* unchanged, regardless of the cascade flag.

    The name suggests this expands direct memberships into subtree roots or
    resolves them into some canonical form. It deliberately does neither —
    this function is pure identity, on or off. Read that twice, because it
    is the one surprising thing in this module: turning
    :func:`core.config.group_cascade_enabled` ON does not change what this
    function returns.

    "Expand a set of direct group ids into the subtree they cover" is real
    work this design needs, and it happens in :func:`subtree_scope_filter`
    below — as a SQL predicate evaluated at query time, not as a Python-side
    transformation of an id list. That split is the design's central
    decision: the moment a caller materializes "all ids in the accessible
    set" as a concrete list (in Python, in a cache, in a session claim), that
    list is stale the instant a membership or a group's parent changes, and
    every one of those staleness windows is a privilege-cascade bug waiting
    for a demotion to land inside it. Keeping the caller-visible surface at
    "these are my direct membership ids" (never "these are my accessible
    ids") and pushing the actual subtree union into the WHERE clause removes
    the list that would otherwise need invalidating.

    So what does this function do, if not that? It exists as the named seam
    a caller reaches for when it wants "the roots of my accessible subtrees"
    as a value rather than as a predicate — for example, to log which groups
    an actor's access is rooted at, or to pass into a future function that
    needs concrete ids rather than a filter. Today, with the cascade
    unwired, that value is exactly the direct membership ids, so identity is
    the correct answer in both flag states. If a later phase ever gives this
    function real expansion work to do, that is a deliberate, reviewed
    change to this docstring and this function — not a change hidden behind
    the flag it does not currently consult.
    """
    return direct_group_ids


def subtree_scope_filter(direct_group_ids: Sequence[uuid.UUID]) -> ColumnElement[bool]:
    """The group-isolation predicate for a query that filters ``groups.id``.

    Mirrors ``core.authz.team_scope_filter``'s shape (a boolean expression
    to drop into ``.where(...)``) but is scoped to ``Group.id`` rather than
    ``Project.team_id`` — callers reach a specific model's rows through
    :func:`group_scoped_subquery_predicate` / :func:`project_subtree_predicate`
    below, both of which delegate here for the actual group-membership test.

    Contract:

    - empty ``direct_group_ids`` -> :func:`sqlalchemy.false` (explicit,
      matching ``team_scope_filter``'s empty-membership case — not relying
      on ``IN ()`` empty-set behaviour).
    - :func:`core.config.group_cascade_enabled` OFF -> ``Group.id IN
      (direct_group_ids)`` and nothing else. This is exactly
      ``team_scope_filter``'s predicate shape today; turning the cascade
      off must reproduce today's flat behaviour bit-for-bit.
    - ON -> ``Group.id IN (direct_group_ids) OR Group.path && ARRAY[direct_group_ids]``.
      The ``IN`` half catches a direct membership group itself (whose own
      ``path`` does not contain its own id — see the ``Group`` model
      docstring's ``ck_groups_not_self_ancestor`` note); the ``&&`` half
      catches every descendant, in one indexed lookup, because a
      descendant's ``path`` contains every one of its ancestors and ``&&``
      is true the moment the two arrays share even one element.

    Query plan, measured on this database (PostgreSQL 17, GIN index
    ``ix_groups_path_gin`` from migration 0090): ``EXPLAIN (ANALYZE,
    BUFFERS)`` on the ON branch, with enough rows in ``groups`` that the
    planner has a reason to prefer an index, shows a ``Bitmap Index Scan`` /
    ``Index Scan`` on ``ix_groups_path_gin`` for the ``&&`` half and a
    ``Bitmap Index Scan`` on the primary key for the ``IN`` half, combined
    under a ``BitmapOr`` — no ``Seq Scan on groups`` in that plan. See this
    PR's test-report for the exact captured plan; if a future change to this
    predicate's shape turns that into a sequential scan, the fix is a
    ``UNION ALL`` of the two branches as separate subqueries rather than the
    single ``OR`` here (per the task's own note — this codebase has hit a
    GIN-index-shaped operator silently falling back to a sequential scan
    before, see the ``Group`` model docstring's ``= ANY(path)`` warning).
    """
    if not direct_group_ids:
        return sa.false()
    ids = list(direct_group_ids)
    if not group_cascade_enabled():
        return Group.id.in_(ids)
    return Group.id.in_(ids) | Group.path.op("&&")(ids)


def group_scoped_subquery_predicate(
    group_id_column: InstrumentedAttribute[uuid.UUID] | InstrumentedAttribute[uuid.UUID | None],
    direct_group_ids: Sequence[uuid.UUID],
) -> ColumnElement[bool]:
    """ "*group_id_column* points at a group in the accessible set", generic form.

    Any model with a column that is a foreign key into ``groups.id`` (today:
    ``Project.group_id``; PR 2-C reuses this for ``ComponentApproval``,
    ``GitHubAppCredential``, ``APIKey``, ``LicensePolicy``,
    ``ComponentIntakeRequest`` and ``TransitionApproval`` as they gain
    group-scoped list reads) can filter through this instead of re-deriving
    the subquery shape. The predicate is::

        group_id_column IN (SELECT groups.id FROM groups WHERE <subtree_scope_filter>)

    i.e. a correlated-free ``IN (subquery)`` — the inner query is exactly
    ``subtree_scope_filter``'s predicate applied to the whole ``groups``
    table, so every caller of this helper gets the cascade's ON/OFF
    behaviour and its index usage for free without repeating the ``&&``
    predicate at each call site.

    *group_id_column* also accepts a NULLABLE FK (``APIKey.team_id``,
    ``LicensePolicy.team_id`` — org-default rows carry no team). SQL's
    ``NULL IN (...)`` is ``NULL`` (falsy in a ``WHERE``), so a NULL-valued
    row is correctly excluded without a caller needing an explicit
    ``.is_not(None)`` guard first.
    """
    return group_id_column.in_(select(Group.id).where(subtree_scope_filter(direct_group_ids)))


def project_subtree_predicate(direct_group_ids: Sequence[uuid.UUID]) -> ColumnElement[bool]:
    """``group_scoped_subquery_predicate`` specialised to ``Project.group_id``.

    The common case named directly, per the task note that PR 2-C will use
    this exact pattern against ``Project`` first.
    """
    return group_scoped_subquery_predicate(Project.group_id, direct_group_ids)


async def can_access_group(
    session: AsyncSession,
    direct_group_ids: Sequence[uuid.UUID],
    group_id: uuid.UUID,
) -> bool:
    """Is *group_id* in the accessible set implied by *direct_group_ids*?

    One query: fetch *group_id*'s own ``id`` and ``path``, then decide in
    Python. A non-existent ``group_id`` is ``False`` — turning that into a
    404 (vs. a 403 for a real-but-inaccessible group) is the caller's job;
    this function only answers the access question, and answers it the same
    way (``False``) for "does not exist" and "not reachable" so a caller
    that wants existence-hiding gets it by doing nothing extra.

    With the cascade OFF this checks only "is *group_id* itself one of
    *direct_group_ids*" — a promoted ancestor's ``path`` overlap is never
    consulted, matching today's flat behaviour.

    With the cascade ON, *group_id* is accessible if it IS a direct
    membership group, OR if one of its ancestors (any id in its own
    ``path``) is a direct membership group — i.e. *group_id* sits inside
    the subtree of something the actor directly belongs to.
    """
    row = (
        await session.execute(select(Group.id, Group.path).where(Group.id == group_id))
    ).first()
    if row is None:
        return False
    direct_ids = set(direct_group_ids)
    if row.id in direct_ids:
        return True
    if not group_cascade_enabled():
        return False
    return any(ancestor_id in direct_ids for ancestor_id in row.path)


def effective_role_at(
    direct_roles: Mapping[uuid.UUID, str],
    group_path: Sequence[uuid.UUID],
    group_id: uuid.UUID,
) -> str | None:
    """The role a caller with *direct_roles* holds, evaluated AT *group_id*.

    *group_path* is *group_id*'s own ``path`` column — the ordered ancestor
    list from root down to (not including) *group_id* itself, exactly as
    the DB trigger derives it (0091). *direct_roles* maps a group id to the
    role of a direct membership row in that group (typically: every
    membership row the actor holds, keyed by ``group_id``).

    Algorithm: walk ``[*group_path, group_id]`` from the END (so *group_id*
    itself is checked first, then its immediate parent, then its
    grandparent, ... up to the root last) and return the role of the first
    id found in *direct_roles*. ``None`` if none of them are direct
    membership groups — the caller denies access on ``None``, it is not a
    "fall back to some default role" signal.

    This one algorithm runs UNCONDITIONALLY — it does not itself branch on
    :func:`core.config.group_cascade_enabled`. That is deliberate: turning
    the cascade on is meant to be a pure configuration change, not a switch
    between two different pieces of logic. With the cascade OFF, no caller
    populates *direct_roles* with anything beyond the actor's own direct
    memberships anyway (there is nothing upstream yet that would put an
    ancestor's role in that mapping speculatively), so the walk finds either
    *group_id* itself or nothing — the same flat answer today's code gives,
    reached by the same code path a cascade-enabled deployment uses. There
    is exactly one implementation of "what role applies here" in this
    codebase once PR 2-C wires this in, not two that have to be kept in
    sync.

    Nearest-ancestor-wins is exactly why a demotion at *group_id* itself (or
    at any of its ancestors, checked before an ancestor further up) beats a
    promotion further up ``group_path`` — the walk returns on the FIRST
    match from the *group_id* end, so a closer, lower-role membership is
    found and returned before a farther, higher-role one is ever looked at.
    """
    for candidate_id in reversed([*group_path, group_id]):
        role = direct_roles.get(candidate_id)
        if role is not None:
            return role
    return None
