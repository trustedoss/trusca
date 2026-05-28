"""component_approvals — approval workflow for packages within a project

Revision ID: 0008
Revises: 0007
Created: 2026-05-08

Phase: 5
PR: #15
Kind: schema
Forward-only: yes

What:
  - Create Postgres ENUM type ``approval_status``
    ('pending', 'under_review', 'approved', 'rejected').
  - Create table ``component_approvals``::
        id                    UUID PRIMARY KEY DEFAULT gen_random_uuid()
        component_id          UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE
        project_id            UUID NOT NULL REFERENCES projects(id)   ON DELETE CASCADE
        team_id               UUID NOT NULL REFERENCES teams(id)      ON DELETE CASCADE
        requested_by_user_id  UUID           REFERENCES users(id)     ON DELETE SET NULL
        requested_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        status                approval_status NOT NULL DEFAULT 'pending'
        decided_by_user_id    UUID           REFERENCES users(id)     ON DELETE SET NULL
        decided_at            TIMESTAMPTZ
        decision_note         TEXT
        version               INTEGER NOT NULL DEFAULT 1
        created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
  - Explicit FK indexes (Postgres does not auto-create them):
        ix_component_approvals_component_id
        ix_component_approvals_project_id
        ix_component_approvals_requested_by_user_id
        ix_component_approvals_decided_by_user_id
  - Compound tenant-leading index:
        ix_component_approvals_team_status       (team_id, status)
  - Time-range index for admin queue:
        ix_component_approvals_status_requested_at  (status, requested_at)
  - Partial unique index (one open approval per component+project):
        ix_component_approvals_unique_open
            UNIQUE (component_id, project_id) WHERE status IN ('pending','under_review')

Why:
  - Phase 5 PR #15 introduces the component approval workflow
    (docs/v2-execution-plan.md §3.6 "컴포넌트 승인 워크플로우").
    Conditional-license components surface in the Approvals screen
    (pending → under_review → approved | rejected). Without a
    dedicated table the service would have to bolt extra columns onto
    the cross-scan ``components`` catalog, conflating catalog identity
    with per-project workflow state.
  - ``team_id`` is denormalised onto every row (mirrored from the parent
    project) so that approval list queries can filter by tenant without
    an extra join — CLAUDE.md §1.2 "Tenancy: compound indexes lead with
    the tenant column."
  - The partial unique index enforces the business rule "a component
    may only have one open approval request per project at a time" at
    the DB layer. Service code relies on the unique-violation as the
    canonical signal rather than a SELECT-then-INSERT (TOCTOU safe).
  - ``version`` enables the optimistic-concurrency / ETag pattern adopted
    throughout this codebase (MEMORY.md "Optimistic concurrency 패턴"):
    the reviewer endpoint demands If-Match == current version to prevent
    two concurrent approvals stomping each other.

Notes:
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises
    ``NotImplementedError``. Dropping the table and the ENUM type is a
    manual / scripted op if ever needed.
  - The partial unique index uses an IN-list predicate which is not
    expressible via ``op.create_index`` (Alembic does not support
    postgresql_where with IN). We emit it via ``op.execute()`` raw DDL —
    the same technique used in 0003 for ``ix_scans_project_active``.
  - No ``updated_at``: the row is effectively append-only from the
    database's perspective. State transitions set ``decided_at`` and
    ``decided_by_user_id``; the ``version`` bump is the concurrency
    signal. Adding ``updated_at`` later is a cheap additive migration.
  - No data migration needed — the table is new and empty.
  - ``team_id`` has no ``ix_component_approvals_team_id`` standalone
    index because the compound ``ix_component_approvals_team_status``
    (team_id, status) already covers equality lookups on team_id alone
    (leftmost prefix rule).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_PK = postgresql.UUID(as_uuid=True)
GEN_UUID = sa.text("gen_random_uuid()")
NOW = sa.text("now()")


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Postgres ENUM type
    # ------------------------------------------------------------------
    # Plain CREATE TYPE — consistent with all prior migrations (0003 etc.).
    # The migration is forward-only and runs exactly once per DB; no IF
    # NOT EXISTS guard is needed. The SQLAlchemy model binds with
    # create_type=False so the ORM never emits its own CREATE TYPE.
    # The dialect-level postgresql.ENUM(create_type=False) used in the
    # create_table step below also suppresses the SQLAlchemy table-event
    # hook that would otherwise issue a second CREATE TYPE.
    op.execute(
        "CREATE TYPE approval_status AS ENUM "
        "('pending', 'under_review', 'approved', 'rejected')"
    )

    # ------------------------------------------------------------------
    # 2. Main table
    # ------------------------------------------------------------------
    # Use postgresql.ENUM(..., create_type=False) for the status column
    # so SQLAlchemy's before_create table event does NOT attempt a second
    # CREATE TYPE. sa.Enum(..., create_type=False) is insufficient here —
    # only the dialect-specific postgresql.ENUM honours create_type=False
    # at the DDL-event level in SQLAlchemy 2.x.
    approval_status_col_type = postgresql.ENUM(
        "pending",
        "under_review",
        "approved",
        "rejected",
        name="approval_status",
        create_type=False,
    )

    op.create_table(
        "component_approvals",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        # --- foreign keys ---
        sa.Column("component_id", UUID_PK, nullable=False),
        sa.Column("project_id", UUID_PK, nullable=False),
        sa.Column("team_id", UUID_PK, nullable=False),
        sa.Column("requested_by_user_id", UUID_PK, nullable=True),
        # --- request ---
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=NOW,
        ),
        # --- status (type already created above via IF NOT EXISTS DDL) ---
        sa.Column(
            "status",
            approval_status_col_type,
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        # --- decision ---
        sa.Column("decided_by_user_id", UUID_PK, nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text, nullable=True),
        # --- optimistic concurrency ---
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        # --- audit ---
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=NOW,
        ),
        # --- FK constraints ---
        sa.ForeignKeyConstraint(
            ["component_id"],
            ["components.id"],
            name="fk_component_approvals_component_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_component_approvals_project_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_component_approvals_team_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name="fk_component_approvals_requested_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name="fk_component_approvals_decided_by_user_id",
            ondelete="SET NULL",
        ),
    )

    # ------------------------------------------------------------------
    # 3. FK indexes (Postgres does not auto-create them)
    # ------------------------------------------------------------------
    op.create_index(
        "ix_component_approvals_component_id",
        "component_approvals",
        ["component_id"],
    )
    op.create_index(
        "ix_component_approvals_project_id",
        "component_approvals",
        ["project_id"],
    )
    op.create_index(
        "ix_component_approvals_requested_by_user_id",
        "component_approvals",
        ["requested_by_user_id"],
    )
    op.create_index(
        "ix_component_approvals_decided_by_user_id",
        "component_approvals",
        ["decided_by_user_id"],
    )

    # ------------------------------------------------------------------
    # 4. Compound tenant-leading index
    # ------------------------------------------------------------------
    # Covers "list approvals for my team, optionally filtered by status".
    # Leading with team_id satisfies the tenancy rule; the leftmost-prefix
    # rule means this also covers plain team_id equality predicates.
    op.create_index(
        "ix_component_approvals_team_status",
        "component_approvals",
        ["team_id", "status"],
    )

    # ------------------------------------------------------------------
    # 5. Status + time-range index
    # ------------------------------------------------------------------
    # Admin / reviewer queue: "show me oldest pending requests first".
    # Also used by the approvals count widget on the dashboard.
    op.create_index(
        "ix_component_approvals_status_requested_at",
        "component_approvals",
        ["status", "requested_at"],
    )

    # ------------------------------------------------------------------
    # 6. Partial unique index — one open approval per (component, project)
    # ------------------------------------------------------------------
    # op.create_index does not support IN-list predicates in
    # postgresql_where, so we use raw DDL (same technique as
    # ix_scans_project_active in 0003_scan_schema.py).
    #
    # GIN note: no JSONB column on this table, so no GIN index required.
    op.execute(
        """
        CREATE UNIQUE INDEX ix_component_approvals_unique_open
            ON component_approvals (component_id, project_id)
            WHERE status IN ('pending', 'under_review')
        """
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
