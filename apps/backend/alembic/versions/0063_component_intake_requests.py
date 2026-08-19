# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""component_intake_requests

Revision ID: 0063
Revises: 0062
Create Date: 2026-08-19

Phase: asking before using, for organizations that work that way (N3)
Kind: schema (additive: one table; no data migration)
Forward-only: yes

What:
  A request to use a package, made before anything has been scanned. Carries
  the package as a purl string rather than a component id, because the whole
  point is that the component does not exist here yet: nobody has pulled it
  in, so no scan has produced a row for it.

  Status uses the same ``approval_status`` type as the after-the-fact
  approvals. One vocabulary, deliberately: a request and an approval are the
  same question asked at different times, and two enums with the same four
  names would drift with nothing failing until a report joined them.

Why:
  Approvals today only exist after a scan finds something, which suits an
  organization that reviews what its code already depends on. An organization
  that asks first has nowhere to record the asking, and the answer arrives
  when the dependency is already in the build.

  Whether to work that way is not the portal's decision, so the whole surface
  is off unless a deployment turns it on, and off means the routes are not
  there at all rather than present and empty.

Reversal:
  Forward-only, and unreachable while the setting is off, which is the
  default. Nothing reads this table unless somebody has asked for it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0063"
down_revision: str | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "component_intake_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The package, as a purl. Not a component id: the component does not
        # exist until something scans it, and asking first is the point.
        sa.Column("purl", sa.String(length=512), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="approval_status", create_type=False),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "decided_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('approved', 'rejected') OR decided_at IS NOT NULL",
            name="ck_component_intake_requests_decided_at",
        ),
    )
    # One open request per package per project, the same shape the per-project
    # approvals use. Two people asking about the same package would otherwise
    # give a reviewer two questions that are one question.
    op.execute(
        """
        CREATE UNIQUE INDEX ix_component_intake_requests_unique_open
        ON component_intake_requests (project_id, purl)
        WHERE status IN ('pending', 'under_review')
        """
    )
    op.create_index(
        "ix_component_intake_requests_team_status",
        "component_intake_requests",
        ["team_id", "status"],
    )
    # The lookup the scan pipeline makes when a package finally shows up.
    op.create_index(
        "ix_component_intake_requests_project_purl",
        "component_intake_requests",
        ["project_id", "purl"],
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    pass
