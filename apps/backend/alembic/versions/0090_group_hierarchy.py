# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""groups: add parent_group_id / path for unlimited nesting

Revision ID: 0090
Revises: 0089
Created: 2026-09-08

Phase: 1 (group-hierarchy rollout)
PR: group-hierarchy-p0-1 (Phase 1 of 5)
Kind: schema
Forward-only: yes

What:
  - ``groups.parent_group_id``, nullable self-FK to ``groups.id``,
    ``ON DELETE RESTRICT``. NULL means "root group". This is the single
    source of truth for the tree shape; everything else in this migration is
    a cache or a guard derived from it.
  - ``groups.path``, ``uuid[] NOT NULL DEFAULT '{}'``, a materialised-path
    cache of ancestor ids from the root down to (but excluding) this row.
    Populated by the trigger added in 0091, not by this migration; every
    existing row gets ``'{}'`` (root) via the column default, which is
    correct for every pre-Phase-1 row since none of them have a parent yet.
  - ``ix_groups_parent_group_id``, plain btree on the new FK column. Every
    FK column gets an explicit index in this schema; Postgres does not
    create one for you.
  - ``ix_groups_path_gin``, GIN on ``path`` (``array_ops``, the default
    opclass for an array column). This index accelerates
    ``path @> ARRAY[:id]::uuid[]`` (descendant lookup: "every group whose
    path contains this ancestor") and ``path && ARRAY[...]`` (subtree-union
    lookup across several roots at once). It does NOT accelerate
    ``:id = ANY(path)``, measured on this database: ``EXPLAIN`` on that
    form is a sequential scan regardless of this index, because
    ``= ANY(array_column)`` compiles to a ``ScalarArrayOpExpr`` and
    ``array_ops`` GIN only implements ``&&``, ``@>``, ``<@`` and ``=``
    (array-equals-array, not scalar-in-array). Every descendant query this
    schema enables must be written with ``@>``; see the ``Group`` model
    docstring, which repeats this so a reader of the model does not have to
    find this migration to learn it.
  - ``uq_groups_parent_slug``, ``UNIQUE (parent_group_id, slug)``: no two
    children of the same parent share a slug.
  - ``uq_groups_root_slug``, partial unique index,
    ``UNIQUE (organization_id, slug) WHERE parent_group_id IS NULL``: no two
    root groups in the same org share a slug. A plain ``UniqueConstraint``
    on ``(parent_group_id, slug)`` alone cannot do this job, because
    PostgreSQL treats every NULL as distinct from every other NULL. Since two
    root groups both have ``parent_group_id = NULL``, a non-partial
    unique constraint would see two different (NULL, slug) pairs even when
    the slugs collide, and let the collision through silently. This
    repository has already hit exactly this NULL-distinctness gap twice
    before (``uq_license_policies_org_default``,
    ``uq_gate_policies_org_default``, see ``models/license_policy.py`` and
    ``models/gate_policy.py``), both fixed the same way: a partial unique
    index over the NULL subset instead of relying on the table-level
    constraint.
  - ``ck_groups_not_self_ancestor``, ``CHECK (NOT (id = ANY(path)))``. This
    is a row-level predicate evaluated per-row by the constraint machinery,
    not a query executed through the planner, so the GIN-index limitation
    above does not apply to it: ``= ANY()`` here is fine.

A pre-existing constraint this migration deliberately does not touch:
  ``uq_groups_org_slug`` (``UNIQUE (organization_id, slug)``, predates this
  PR) already enforces slug uniqueness across every group in the org
  regardless of nesting depth. That is strictly stronger than both
  ``uq_groups_parent_slug`` and ``uq_groups_root_slug`` above: the two new
  constraints are provably redundant with it today. They are added anyway,
  per the Phase 1 design: the org-wide constraint is a Phase 1
  simplification (this phase does not change what the UI or API allow), and
  the sibling/root invariant this migration adds is what a later phase
  would keep if ``uq_groups_org_slug`` is ever relaxed to allow the same
  slug under two different parents. Dropping ``uq_groups_org_slug`` is out
  of scope here: no behavioural change, pure schema addition.

Why:
  Group-hierarchy rollout Phase 1: let a group nest under another group
  without limit, in preparation for Phase 5's reparent (subtree move)
  service. ``path`` exists so a descendant/subtree query is a single
  indexed lookup instead of a recursive walk on every request. Recursive
  CTEs are reserved for migrations and the diagnostic function added in
  0091, never the request path (see 0091's docstring).

Notes:
  - Breaking-change note: none. Every column added here is nullable or has
    a default, so this migration needs no expand/contract split and no
    backfill step: the DEFAULT '{}' on ``path`` applies to existing rows
    for free (a constant default on ``ADD COLUMN`` is a metadata-only
    change on PostgreSQL 11+, not a table rewrite).
  - ``path`` is a derived cache with no application writer. The DB trigger
    that derives it (``trg_groups_derive_path``) is added in 0091, not
    here, because the trigger's ``WHEN`` gate needs its own explanation
    that would otherwise crowd out this migration's schema notes. Until
    0091 lands, every row's ``path`` is the column default (``'{}'``); this
    migration alone does not populate it from ``parent_group_id`` for any
    row, including rows inserted between 0090 and 0091 in the same
    deployment (not a concern here, both land in the same PR).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0090"
down_revision: str | None = "0089"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "groups",
        sa.Column(
            "parent_group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "groups",
        sa.Column(
            "path",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
    )

    op.create_index("ix_groups_parent_group_id", "groups", ["parent_group_id"])

    # array_ops GIN: supports `path @> ARRAY[:id]::uuid[]` and
    # `path && ARRAY[...]`; does NOT support `:id = ANY(path)` (see module
    # docstring).
    op.create_index(
        "ix_groups_path_gin", "groups", ["path"], postgresql_using="gin"
    )

    op.create_unique_constraint(
        "uq_groups_parent_slug", "groups", ["parent_group_id", "slug"]
    )

    # Partial unique index, not a table UniqueConstraint: PostgreSQL cannot
    # express "unique except this subset" in a plain UNIQUE constraint, only
    # in an index with a WHERE clause.
    op.create_index(
        "uq_groups_root_slug",
        "groups",
        ["organization_id", "slug"],
        unique=True,
        postgresql_where=sa.text("parent_group_id IS NULL"),
    )

    op.create_check_constraint(
        "ck_groups_not_self_ancestor", "groups", "NOT (id = ANY(path))"
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
