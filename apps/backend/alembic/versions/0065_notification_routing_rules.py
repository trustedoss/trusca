# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""notification_routing_rules

Revision ID: 0065
Revises: 0064
Create Date: 2026-08-20

Phase: somewhere to say who hears about what (N9)
Kind: schema (additive: one table; no data migration)
Forward-only: yes

What:
  Rules an organization or a team writes to add recipients and channels for
  notifications matching a condition: a kind, a minimum severity, a project.

Why:
  Today every notification goes to whoever the producer named, filtered by
  that person's own channel toggles. That works for "tell me about my own
  things" and has no answer for "the security team hears about every critical
  finding in these projects", which is the rule an organization actually
  writes down.

  Rules add; they never subtract. A person's toggles still decide what
  reaches that person, and a rule decides who else hears. Letting a rule
  remove a recipient would make two mechanisms answer the same question, and
  the one that silences would win an argument nobody had.

Reversal:
  Forward-only, and additive. With no rows the routing resolves to nothing
  extra and every notification goes exactly where it went before, which is
  asserted rather than assumed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_routing_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # NULL means the rule belongs to the organization and applies to every
        # team in it. A team id narrows it to that team, the same shape the
        # gate and licence policies use.
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        # Empty list means every kind. Stored as JSONB rather than an enum
        # array so adding a notification kind does not need a migration here;
        # a contract test holds the two vocabularies together instead.
        sa.Column(
            "kinds",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # NULL means severity is not part of this rule's condition. Set, it
        # matches that severity and everything above it.
        sa.Column("min_severity", sa.String(length=20), nullable=True),
        # NULL means every project in scope.
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "channels",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "email_recipients",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
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
            "min_severity IS NULL OR min_severity IN "
            "('critical', 'high', 'medium', 'low', 'info', 'unknown')",
            name="ck_notification_routing_rules_min_severity",
        ),
        # A rule that names neither a channel nor a recipient does nothing,
        # and a row that does nothing is one an operator will later read as
        # broken. Refused at the database so no writer can create one.
        sa.CheckConstraint(
            "jsonb_array_length(channels) > 0 OR jsonb_array_length(email_recipients) > 0",
            name="ck_notification_routing_rules_says_something",
        ),
        # A team rule must belong to that team's organization. Enforced in the
        # service; the index below is what makes the lookup cheap.
        sa.Index(
            "ix_notification_routing_rules_scope",
            "organization_id",
            "team_id",
            "is_active",
        ),
    )


def downgrade() -> None:
    """Forward-only (CLAUDE.md 마이그레이션 정책)."""
    raise NotImplementedError("0065 is forward-only")
