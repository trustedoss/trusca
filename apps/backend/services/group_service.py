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
import structlog
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from core.audit import audit_context
from core.config import group_cascade_enabled
from core.security import CurrentUser
from models import Project
from models.auth import Group

log = structlog.get_logger("group.service")

__all__ = [
    "GroupCrossOrganizationNotAllowed",
    "GroupCycleDetected",
    "GroupHierarchyError",
    "GroupHierarchyNotFound",
    "GroupSlugConflict",
    "can_access_group",
    "create_subgroup",
    "effective_role_at",
    "group_scoped_subquery_predicate",
    "project_subtree_predicate",
    "reparent",
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


# ---------------------------------------------------------------------------
# Subtree move / create — group-hierarchy Phase 5 PR 5-A
#
# Everything below is the first code path that actually WRITES
# ``parent_group_id`` after it has been created. Migrations 0090/0091
# (Phase 1) built the schema, the trigger and ``verify_group_paths()``
# specifically so this moment would have somewhere safe to land — see
# 0091's docstring, "Why two triggers, not one gated trigger", for the
# derivation this section depends on:
#
#   - Moving one group re-parents it: an UPDATE that sets ``parent_group_id``
#     fires ``trg_groups_derive_path_update``, which re-derives THAT ROW's
#     ``path`` from its (new) parent.
#   - Every descendant of the moved group keeps its OWN ``parent_group_id``
#     unchanged (a descendant's parent is still its immediate parent, wherever
#     that parent now lives) — only its ``path`` cache needs to shift to
#     reflect the new ancestor chain above the moved group. That UPDATE names
#     only ``path`` in its SET list, so it does not fire either trigger (the
#     INSERT trigger only fires on INSERT; the UPDATE trigger is scoped to
#     ``UPDATE OF parent_group_id`` and gated by ``WHEN (NEW.parent_group_id
#     IS DISTINCT FROM OLD.parent_group_id)``, neither of which this
#     statement satisfies) — see 0091's docstring for why an unconditional
#     trigger would have clobbered exactly this write.
# ---------------------------------------------------------------------------


class GroupHierarchyError(Exception):
    status_code: int = 400
    title: str = "Group Hierarchy Error"
    extensions: dict[str, object] = {}


class GroupHierarchyNotFound(GroupHierarchyError):
    """404 — the moved group or the requested new parent does not exist.

    Same status for both cases (no distinguishing message field beyond the
    detail text) — this is an admin-only, super_admin-gated surface (unlike
    ``group_directory_service``'s existence-hide, which defends against a
    non-member PROBING for a group's existence), so there is no adversarial
    reason to collapse the two further; 404 here is simply "the id you gave
    me is wrong," not a security control.
    """

    status_code = 404
    title = "Group Not Found"


class GroupCycleDetected(GroupHierarchyError):
    """409 — moving *group_id* under *new_parent_id* would make a group its
    own ancestor (including the degenerate case ``new_parent_id ==
    group_id``).

    ``ck_groups_not_self_ancestor`` (migration 0090) is the DB-level
    backstop for this — a bug here would surface as a raw
    ``IntegrityError``/500, not silent corruption — but this check exists so
    the caller gets a legible, RFC 7807 ``cycle_detected`` extension instead
    of an opaque constraint-violation 500.
    """

    status_code = 409
    title = "Group Cycle Detected"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.extensions = {"cycle_detected": True}


class GroupCrossOrganizationNotAllowed(GroupHierarchyError):
    """422 — *group_id* and *new_parent_id* belong to different organizations.

    The tree is organization-bounded end to end today: every group carries
    its own ``organization_id`` (not derived from its parent at read time),
    license/gate policy resolution walks a group's ``path`` without ever
    crossing an org boundary (``get_effective_policy``), and the whole
    tenant-isolation model (``PersonalOrganizationNotAssignable``,
    ``MultipleOrganizationsConfigured`` in ``admin_team_service``) treats an
    Organization as the hard multi-tenancy boundary, not a Group. Letting a
    reparent silently walk a subtree across that boundary would attach one
    tenant's projects, policies and memberships (unaffected by the move, so
    now stale relative to their new ancestor chain) to another tenant's
    org-scoped catalogs the moment a policy lookup or a cascade check walks
    the new ``path`` — refusing outright is safer than trying to define what
    "moved cross-org" should mean for those axes. A future PR that wants
    cross-org moves needs its own design for what happens to org-scoped data
    the subtree carries, not a silent default here.
    """

    status_code = 422
    title = "Cross-Organization Move Not Allowed"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.extensions = {"cross_organization_move": True}


class GroupSlugConflict(GroupHierarchyError):
    """409 — a sibling of the target parent already uses this slug.

    ``uq_groups_parent_slug`` (migration 0090) is the DB-level backstop;
    this exists so a slug collision surfaces as a legible 409 rather than a
    raw ``IntegrityError``/500, matching ``AdminTeamSlugConflict``'s role for
    ``create_team``.
    """

    status_code = 409
    title = "Group Slug Conflict"


async def _parent_group_still_exists(session: AsyncSession, parent_group_id: uuid.UUID) -> bool:
    """Re-check *parent_group_id* after a failed ``create_subgroup`` commit,
    to tell apart the two things an ``IntegrityError`` there can mean.

    Deliberately NOT a SQLSTATE classification (e.g. "was this a
    foreign-key violation, 23503") the way similar helpers elsewhere in this
    codebase work (``github_app_service._is_foreign_key_violation``): a
    concurrent parent deletion does not actually surface as an FK violation
    here. ``groups_derive_path()`` (migration 0091, ``BEFORE INSERT``) looks
    up the parent's ``path`` to compute the child's, and a vanished parent
    makes that lookup return no row — so ``NEW.path`` comes out NULL, and
    Postgres rejects the row on the ``path NOT NULL`` constraint (23502)
    *before* the INSERT's own FK check ever runs, not on the FK constraint
    at all. Reproduced live: this is the actual error a concurrent-deletion
    race raises, not a hypothetical. A SQLSTATE-based classifier would have
    to hardcode that one trigger's specific failure mode instead of asking
    the question this function actually needs answered — re-querying
    *parent_group_id* directly answers it regardless of which constraint
    Postgres happened to reject the row on.
    """
    return (
        await session.execute(select(Group.id).where(Group.id == parent_group_id))
    ).scalar_one_or_none() is not None


def _bind_audit_group(group_id: uuid.UUID) -> None:
    """Bind ``team_id`` (the audit listener's key for ``groups.id`` — see
    ``core.audit``'s ``AuditLog.group_id`` note) into the request-scoped
    audit context before a mutating commit, mirroring
    ``admin_team_service._bind_audit_team``. Both modules keep their own
    copy rather than sharing one: neither imports the other, by design (see
    the module docstring on why ``services.group_service`` has no notion of
    "admin").
    """
    ctx = dict(audit_context.get() or {})
    ctx["team_id"] = str(group_id)
    audit_context.set(ctx)


# Descendant ``path`` propagation, in one statement (see the section banner
# above for why this is a ``path``-only UPDATE that deliberately never
# touches ``parent_group_id``).
#
# ``d.path @> ARRAY[:moved]`` (GIN-index-backed — index ``ix_groups_path_gin``,
# migration 0090) selects every strict descendant of the moved group; the
# moved row itself never matches (a group's own ``path`` never contains its
# own id — ``ck_groups_not_self_ancestor``), so this UPDATE never touches the
# row the ``parent_group_id`` UPDATE above it already re-parented.
#
# The SET expression replaces the OLD prefix of each descendant's path (every
# element up to and including the moved group's own id) with the moved
# group's NEW full path (root..moved), then keeps everything AFTER the moved
# group's id unchanged — i.e. "splice in the new ancestor chain above the
# move point, keep the chain below it." ``array_position(d.path, :moved)``
# finds where the moved group sits in each descendant's path; the slice
# ``d.path[pos+1 : array_length(d.path, 1)]`` is empty (not an error) for a
# direct child of the moved group, where ``pos`` is the last index.
#
# Bind param type is set explicitly to ``uuid`` (:moved) rather than an
# inline ``::uuid`` cast in the SQL text: SQLAlchemy's ``text()`` bind-param
# regex (``(?<![:\w\\]):(\w+)(?!:)``) requires the character AFTER the
# parameter name not be a colon, and ``:moved::uuid`` fails that — the
# greedy match backtracks to ``:move`` (dropping the final ``d``), leaving a
# literal ``d::uuid`` in the compiled SQL, which is a syntax error at the
# database. Measured on this database (PostgreSQL 17.2, SQLAlchemy 2.0.36):
# confirmed by inspecting the compiled ``TextClause._bindparams`` directly —
# see this PR's own dev notes for the reproduction. Binding the type on the
# ``bindparam()`` instead removes every inline ``::uuid`` cast from the SQL
# text, so this failure mode cannot recur here regardless of what the
# parameter happens to be named.
_DESCENDANT_PATH_PROPAGATION_SQL = text(
    """
    UPDATE groups AS d
    SET path = (
            SELECT m.path || m.id FROM groups m WHERE m.id = :moved
        ) || d.path[array_position(d.path, :moved) + 1 : array_length(d.path, 1)]
    WHERE d.path @> ARRAY[:moved]
    """
).bindparams(sa.bindparam("moved", type_=PG_UUID(as_uuid=True)))


async def _lock_groups_in_id_order(
    session: AsyncSession, group_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Group]:
    """``SELECT ... FOR UPDATE`` every id in *group_ids*, ONE ROW PER ROUND
    TRIP, in ascending-id order — never a single ``WHERE id IN (...)``.

    Why not one query: Postgres's ``FOR UPDATE`` locks rows as the underlying
    scan visits them, and an ``IN (...)`` list combined with ``ORDER BY`` does
    not guarantee the lock is acquired in that order — only the OUTPUT order
    is guaranteed, and a bitmap/index scan is free to visit the matching heap
    pages in physical order regardless of the ids' numeric order. Two
    concurrent ``reparent`` calls trying to swap roles (A becomes B's parent;
    B becomes A's parent, the same pair of rows either way) would then race
    to lock the SAME TWO ROWS in whatever order each transaction's own scan
    happened to pick — a classic deadlock, not a clean serialise.

    Issuing one ``SELECT ... WHERE id = :id FOR UPDATE`` per id, in id-sorted
    order, fixes the lock ACQUISITION order at the application level: for any
    two group ids X < Y, every caller that needs both always requests X
    first. Two calls that only ever pass this function the SAME set of ids
    (regardless of which one is "moved" and which is "new parent" in each
    call) then contend on the FIRST lock only — whichever wins proceeds to
    completion and commits or raises; the other blocks until it does, then
    re-reads a fresh, post-commit row. See this PR's concurrency test
    (``test_group_reparent_concurrency.py``) for the two-transaction swap
    that pins exactly this.

    This function's own ordering guarantee does NOT, by itself, cover a
    caller that locks a small id set here and separately mutates a LARGER,
    not-fully-pre-locked set afterward (security review, Phase 5 PR 5-A) —
    see :func:`reparent`'s own docstring for why it passes this function the
    FULL set of ids a transaction will touch, descendants included, rather
    than just *group_id* and *new_parent_id*.

    A missing id is simply absent from the returned dict (no row = nothing
    to lock) — the caller turns that into ``GroupHierarchyNotFound``.
    """
    ordered_ids = sorted(set(group_ids))
    locked: dict[uuid.UUID, Group] = {}
    for group_id in ordered_ids:
        row = (
            await session.execute(select(Group).where(Group.id == group_id).with_for_update())
        ).scalar_one_or_none()
        if row is not None:
            locked[group_id] = row
    return locked


async def reparent(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    group_id: uuid.UUID,
    new_parent_id: uuid.UUID | None,
) -> Group:
    """Move *group_id* (and its whole subtree) to sit under *new_parent_id*.

    ``new_parent_id`` may be ``None`` — moves *group_id* to the root of its
    own organization.

    One transaction, four steps (see the section banner above for the
    trigger mechanics this depends on):

      1. Lock *group_id*, *new_parent_id*, AND every current descendant of
         *group_id* — all in one ascending-id-ordered pass (see
         :func:`_lock_groups_in_id_order`). The descendant ids come from an
         unlocked read taken first (``path @> ARRAY[group_id]``); a group
         that stops being a descendant between that read and the lock pass
         is locked anyway (harmless — locking a row this call turns out not
         to need is not a correctness problem, only a slightly wider lock
         set). Locking the descendants explicitly, in the SAME
         globally-ordered pass as *group_id*/*new_parent_id*, is what makes
         step 4's bulk descendant UPDATE below touch ONLY rows this
         transaction already holds — that bulk UPDATE's own implicit
         row-locking (in whatever order Postgres's GIN scan visits matching
         rows, not id order) is exactly what let a concurrent reparent of
         one of those descendants deadlock against this call before this
         fix (security review, Phase 5 PR 5-A — reproduced live: moving A
         to B while concurrently moving A's grandchild Z to A, with
         Z.id < A.id, deadlocked when only {A, B} were pre-locked). A
         narrower residual window remains -- a concurrent reparent that
         adds a brand-new descendant to *group_id*'s subtree in the gap
         between this call's unlocked descendant read and its lock pass
         isn't in this call's locked set, so a deadlock over THAT row is
         still possible in principle. Postgres detects any such deadlock
         and aborts one side cleanly (a 500, not corruption or a hang) --
         ``test_group_reparent_concurrency.py``'s mutual-swap test and the
         deep-subtree-move test both pin the cases this fix closes.
      2. Validate against the LOCKED, up-to-date rows: existence, the
         organization boundary (:class:`GroupCrossOrganizationNotAllowed`),
         and the cycle check (:class:`GroupCycleDetected`) — *group_id*
         would become its own ancestor if *new_parent_id* IS *group_id*, or
         if *group_id* already appears in *new_parent_id*'s own ``path``
         (i.e. *new_parent_id* is itself a descendant of *group_id* — moving
         a group under its own descendant).
      3. UPDATE *group_id*'s row's ``parent_group_id`` — fires the DB
         trigger, which re-derives *group_id*'s own ``path`` from its new
         parent.
      4. In the SAME transaction, run the one-statement descendant ``path``
         propagation (:data:`_DESCENDANT_PATH_PROPAGATION_SQL`) — every
         group in *group_id*'s OLD subtree gets its ``path`` spliced to
         reflect the new ancestor chain above the move point, without
         touching any descendant's own ``parent_group_id``.

    A move to *group_id*'s current parent (no-op, including ``None`` ->
    ``None`` for an already-root group) short-circuits after step 2 and
    returns the group unchanged — no UPDATE, no audit row, no trigger fire.

    Concurrency-sensitive downstream effects that read the tree AFTER this
    commits, without this function doing anything special for them:

      - ``core.authz.can_access_group`` / ``services.group_service.
        subtree_scope_filter`` (Phase 2) re-derive the accessible set from
        ``path`` on every call — no cache to invalidate, so an ancestor's
        admin loses (and the new ancestor's admin gains) access to
        *group_id*'s subtree the instant this commits, with
        ``GROUP_CASCADE_ENABLED=true``.
      - ``services.license_policy_service.get_effective_policy`` /
        ``services.gate_policy_service`` (Phase 3) walk ``path`` fresh on
        every resolution too — a move under a different-policy ancestor
        changes what applies to every project in the subtree immediately,
        with no explicit recompute step.

    Audit: the ``parent_group_id`` UPDATE is a plain ORM attribute
    assignment on an already-loaded row, so ``core.audit``'s ``before_flush``
    listener captures it as an audit_logs row automatically once ``team_id``
    is bound (:func:`_bind_audit_group`, called before the mutation per
    ``core/audit.py``'s contract). The descendant ``path`` propagation
    (step 4) is a Core bulk UPDATE, not an ORM row mutation, so it produces
    NO per-descendant audit rows — deliberately: ``path`` is a derived
    cache, not a fact an operator changed, and one audit row naming the
    move (*group_id* -> *new_parent_id*) already says what happened; an
    audit trail entry per incidentally-repathed descendant would be noise
    that implies each of those groups was itself edited, which is not true.
    """
    # Unlocked read, on purpose: this only WIDENS the lock set below to
    # include every current descendant, so this call's own bulk descendant
    # UPDATE (step 4) never has to acquire a NEW lock Postgres would have to
    # order for itself. See _lock_groups_in_id_order's docstring and this
    # function's step-1 docstring above for why this closes the deadlock a
    # security review found and reproduced live.
    descendant_ids = (
        await session.execute(select(Group.id).where(Group.path.op("@>")([group_id])))
    ).scalars().all()
    lock_target_set = {group_id, *descendant_ids}
    if new_parent_id is not None:
        lock_target_set.add(new_parent_id)
    locked = await _lock_groups_in_id_order(session, list(lock_target_set))

    moved = locked.get(group_id)
    if moved is None:
        raise GroupHierarchyNotFound(f"group {group_id} not found")

    if new_parent_id is not None:
        new_parent = locked.get(new_parent_id)
        if new_parent is None:
            raise GroupHierarchyNotFound(f"group {new_parent_id} not found")

        if new_parent.organization_id != moved.organization_id:
            raise GroupCrossOrganizationNotAllowed(
                f"group {group_id} (organization {moved.organization_id}) and "
                f"new parent {new_parent_id} (organization "
                f"{new_parent.organization_id}) belong to different organizations"
            )

        if new_parent_id == group_id or group_id in new_parent.path:
            raise GroupCycleDetected(
                f"moving group {group_id} under {new_parent_id} would make "
                f"{group_id} its own ancestor"
            )

    if moved.parent_group_id == new_parent_id:
        log.info(
            "group.reparent_noop",
            actor_id=str(actor.id),
            group_id=str(group_id),
            new_parent_id=str(new_parent_id) if new_parent_id else None,
        )
        return moved

    _bind_audit_group(group_id)

    moved.parent_group_id = new_parent_id
    # Flush BEFORE the descendant propagation below: that statement's SET
    # clause reads *group_id*'s NEW ``path`` back out of the table
    # (``SELECT m.path || m.id FROM groups m WHERE m.id = :moved``), which
    # only exists once the trigger above has run against this UPDATE. Both
    # statements share this session's connection/transaction, so the second
    # sees the first's uncommitted write without needing an intervening
    # commit.
    await session.flush()

    await session.execute(_DESCENDANT_PATH_PROPAGATION_SQL, {"moved": group_id})

    await session.commit()
    await session.refresh(moved)

    log.info(
        "group.reparented",
        actor_id=str(actor.id),
        group_id=str(group_id),
        new_parent_id=str(new_parent_id) if new_parent_id else None,
    )
    return moved


async def create_subgroup(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    parent_group_id: uuid.UUID,
    name: str,
    slug: str,
    description: str | None = None,
) -> Group:
    """Create a new group directly under *parent_group_id*.

    Inherits the parent's ``organization_id`` unconditionally — a child
    cannot belong to a different organization than its parent, for the same
    tenant-isolation reason :class:`GroupCrossOrganizationNotAllowed` refuses
    a cross-org :func:`reparent`. There is no "create with an explicit
    ``organization_id``" override here, unlike ``AdminTeamCreate`` for a ROOT
    team: a subgroup's org is never ambiguous, it is always its parent's.

    Sibling-slug uniqueness is enforced by ``uq_groups_parent_slug``
    (migration 0090); this pre-checks with a SELECT so the common case gets
    a legible 409 (:class:`GroupSlugConflict`) rather than a raw
    ``IntegrityError``, and still catches the constraint at commit time as a
    safety net against a concurrent sibling insert racing the same slug in
    the gap between the pre-check and the commit — the DB constraint, not
    the pre-check, is what actually makes this TOCTOU-safe.

    The other pre-check, *parent_group_id* existing, has the same gap: the
    parent can be deleted between this function's own SELECT and its
    commit. There, the failing INSERT's error at commit is NOT a
    foreign-key violation — ``groups_derive_path()``'s ``BEFORE INSERT``
    trigger (migration 0091) tries to look up the vanished parent's
    ``path`` first and gets no row, so Postgres rejects the row for a NULL
    ``path`` (23502) before the FK constraint is ever reached. On any
    ``IntegrityError`` at commit, :func:`_parent_group_still_exists`
    re-checks the parent directly rather than trying to classify which
    constraint fired, so this stays correct even if a future migration
    changes which constraint ends up rejecting the row.
    """
    parent = (
        await session.execute(select(Group).where(Group.id == parent_group_id))
    ).scalar_one_or_none()
    if parent is None:
        raise GroupHierarchyNotFound(f"group {parent_group_id} not found")

    existing_sibling = (
        await session.execute(
            select(Group.id).where(
                Group.parent_group_id == parent_group_id, Group.slug == slug
            )
        )
    ).scalar_one_or_none()
    if existing_sibling is not None:
        raise GroupSlugConflict(
            f"a group with slug {slug!r} already exists under parent {parent_group_id}"
        )

    child = Group(
        organization_id=parent.organization_id,
        parent_group_id=parent.id,
        name=name,
        slug=slug,
        description=description,
    )
    session.add(child)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if not await _parent_group_still_exists(session, parent_group_id):
            raise GroupHierarchyNotFound(
                f"group {parent_group_id} not found"
            ) from exc
        raise GroupSlugConflict(
            f"a group with slug {slug!r} already exists under parent {parent_group_id}"
        ) from exc

    await session.refresh(child)
    log.info(
        "group.subgroup_created",
        actor_id=str(actor.id),
        parent_group_id=str(parent_group_id),
        group_id=str(child.id),
        slug=child.slug,
    )
    return child
