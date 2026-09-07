# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""groups: derive path from parent_group_id via a gated trigger

Revision ID: 0091
Revises: 0090
Created: 2026-09-08

Phase: 1 (group-hierarchy rollout)
PR: group-hierarchy-p0-1 (Phase 1 of 5)
Kind: schema
Forward-only: yes

What:
  - ``groups_derive_path()`` — sets ``NEW.path`` from ``NEW.parent_group_id``:
    ``'{}'`` for a root group, or the parent's own ``path || parent.id`` for
    a child. ``pg_temp`` search-path pin + schema-qualified table reference,
    per the rule below.
  - ``trg_groups_derive_path_insert`` — ``BEFORE INSERT ON groups``,
    unconditional (every inserted row needs its ``path`` derived, and there
    is no ``OLD`` row on INSERT to gate against).
  - ``trg_groups_derive_path_update`` — ``BEFORE UPDATE OF parent_group_id
    ON groups``, gated by
    ``WHEN (NEW.parent_group_id IS DISTINCT FROM OLD.parent_group_id)``.
    This second trigger, not the single combined one the design sketch
    proposed, is the load-bearing part of this migration — see "Why two
    triggers, not one gated trigger" below for why the combined form does
    not run.
  - ``verify_group_paths()`` — a diagnostic SQL function, not used by any
    request path. Walks ``parent_group_id`` with a recursive CTE to compute
    what every row's ``path`` should be, and returns one row per mismatch
    (empty result set = every stored ``path`` agrees with what
    ``parent_group_id`` implies). Used by this PR's own trigger tests
    (Phase 1) and intended for an operator-facing diagnostic command in a
    later phase; not wired to any endpoint here (no API/service change this
    phase).

Why two triggers, not one gated trigger
-----------------------------------------
The design this migration implements called for a single trigger,
``BEFORE INSERT OR UPDATE OF parent_group_id ON groups ... WHEN (TG_OP =
'INSERT' OR NEW.parent_group_id IS DISTINCT FROM OLD.parent_group_id)``.
That statement does not run: ``TG_OP`` is a variable PostgreSQL exposes
inside a PL/pgSQL trigger *function body*, not inside a trigger's ``WHEN``
clause, which the trigger manager evaluates itself before the function is
ever invoked — measured on this database (PostgreSQL 17.2),
``CREATE TRIGGER`` with that ``WHEN`` fails at creation time with
``column "tg_op" does not exist``. ``WHEN`` can only reference columns of
the row and PL/pgSQL-independent expressions, which rules out every
``TG_*`` variable, not just this one.

Splitting into two triggers reaches the same gate through PostgreSQL's own
per-event mechanics instead of a runtime ``TG_OP`` check:
  - INSERT has no ``OLD`` row to compare against, so there is nothing to
    gate — every INSERT must derive ``path``, unconditionally.
  - UPDATE already carries a real ``OLD``, so its trigger can use exactly
    the comparison the design wanted: ``NEW.parent_group_id IS DISTINCT
    FROM OLD.parent_group_id`` (``IS DISTINCT FROM`` rather than ``<>``
    because a root group has ``parent_group_id IS NULL``, and ``<>``
    against NULL is NULL, which ``WHEN`` treats as "don't fire" — the
    correct outcome here, but only if the comparison is NULL-safe).
  - The event spec ``UPDATE OF parent_group_id`` adds a second layer on
    top of the ``WHEN`` clause: PostgreSQL only fires a column-list trigger
    when the triggering statement's ``SET`` clause names one of the listed
    columns at all, regardless of whether the value changes. A Phase-5
    descendant UPDATE that sets only ``path`` never lists
    ``parent_group_id``, so it does not fire this trigger even before
    ``WHEN`` is evaluated.

``path`` is a cache; ``parent_group_id`` is the only source of truth. If
the UPDATE trigger fired unconditionally (a bare ``BEFORE UPDATE ON
groups``), Phase 5's subtree-move service would have nowhere to put its
own write: reparenting a subtree means moving one group's
``parent_group_id``, then UPDATE-ing every descendant's ``path`` directly
to reflect the new ancestor chain — those descendant rows do NOT change
``parent_group_id``. An unconditional trigger would intercept that
descendant UPDATE, recompute ``path`` from the descendant's own (unchanged)
``parent_group_id``, and overwrite the service's new value with the exact
old one — no error, the UPDATE simply reports success and changes nothing.
That failure mode was found in this PR's design review before any Phase 5
code existed, which is why the gate matters even though the design's exact
DDL for it needed to be replaced with the two-trigger form above to run at
all.

Phase 5 (not built here) is the intended beneficiary: a subtree move
becomes "UPDATE the moved group's ``parent_group_id``" (fires
``trg_groups_derive_path_update``, re-derives that one row's ``path``)
followed by "UPDATE every descendant's ``path`` directly" (fires neither
trigger, because those statements neither list nor change
``parent_group_id``) — this migration's job is only to make sure that
second UPDATE has somewhere to land.

search_path — same rule as 0082, applied to two new functions
---------------------------------------------------------------
Both functions below pin
``SET search_path = pg_catalog, public, pg_temp`` (``pg_temp`` named last,
not omitted) and qualify every table reference with ``public.`` — the
exact two-part rule 0082's docstring derives and 0088 already reused for
``audit_logs_prevent_mutation()``. Copied here rather than re-derived: a
trigger function runs with its caller's search path, PostgreSQL searches a
session's temporary schema before ``public`` unless the search path names
``pg_temp`` explicitly, and ``CREATE TEMP TABLE`` is grantable to any
authenticated role by default — so an unqualified ``FROM groups`` inside
``groups_derive_path()``, or a search path that pins ``pg_catalog, public``
without also naming ``pg_temp``, would let a caller shadow the real
``groups`` table with a same-named temp table and feed the trigger whatever
parent/path values the caller wants. ``verify_group_paths()`` gets the same
treatment even though it is a diagnostic, read-only function with no
security-relevant side effect of its own — the point of 0082's rule is that
every new database function gets this header without a caller having to
argue an exception is safe.

Notes:
  - No data migration in this revision: every row already has ``path =
    '{}'`` from 0090's column default, and every row in this schema (before
    Phase 2+ introduces a UI/API path to set ``parent_group_id``) is a root
    group, so ``'{}'`` is already the correct value for all of them. A
    future PR that lets ``parent_group_id`` be set for the first time on
    existing rows would need its own data-migration revision if it ever
    needs to backfill ``path`` for rows written before this trigger existed
    — not needed today because no such row exists yet.
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises
    ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0091"
down_revision: str | None = "0090"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DERIVE_PATH_FUNCTION = """
CREATE OR REPLACE FUNCTION groups_derive_path()
RETURNS TRIGGER AS $$
BEGIN
  NEW.path := CASE
    WHEN NEW.parent_group_id IS NULL THEN '{}'::uuid[]
    ELSE (SELECT g.path || g.id FROM public.groups g
          WHERE g.id = NEW.parent_group_id)
  END;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp;
""".strip()

_DERIVE_PATH_TRIGGER_INSERT = """
CREATE TRIGGER trg_groups_derive_path_insert
  BEFORE INSERT ON groups
  FOR EACH ROW
  EXECUTE FUNCTION groups_derive_path();
""".strip()

_DERIVE_PATH_TRIGGER_UPDATE = """
CREATE TRIGGER trg_groups_derive_path_update
  BEFORE UPDATE OF parent_group_id ON groups
  FOR EACH ROW
  WHEN (NEW.parent_group_id IS DISTINCT FROM OLD.parent_group_id)
  EXECUTE FUNCTION groups_derive_path();
""".strip()

_VERIFY_GROUP_PATHS_FUNCTION = """
CREATE OR REPLACE FUNCTION verify_group_paths()
RETURNS TABLE(id uuid, stored_path uuid[], expected_path uuid[])
LANGUAGE sql STABLE
SET search_path = pg_catalog, public, pg_temp
AS $$
  WITH RECURSIVE chain AS (
    SELECT g.id, g.parent_group_id, ARRAY[]::uuid[] AS computed_path
    FROM public.groups g
    WHERE g.parent_group_id IS NULL
    UNION ALL
    SELECT g.id, g.parent_group_id, c.computed_path || c.id
    FROM public.groups g
    JOIN chain c ON g.parent_group_id = c.id
  )
  SELECT c.id, g.path, c.computed_path
  FROM chain c
  JOIN public.groups g ON g.id = c.id
  WHERE g.path IS DISTINCT FROM c.computed_path;
$$;
""".strip()


def upgrade() -> None:
    op.execute(_DERIVE_PATH_FUNCTION)
    op.execute(_DERIVE_PATH_TRIGGER_INSERT)
    op.execute(_DERIVE_PATH_TRIGGER_UPDATE)
    op.execute(_VERIFY_GROUP_PATHS_FUNCTION)


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
