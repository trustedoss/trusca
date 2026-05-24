"""license policy — license_policies (per-team/org dynamic license policy, v2.2 c1)

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-24

Phase: v2.2 (Track C — c1 "per-team/org dynamic license policy model")
PR: feat/v2.2-c1-license-policy
Kind: schema (additive — new table; no data migration)
Forward-only: yes

What:
  - Create a new ``license_policies`` table holding, per (organization, team)
    scope, how SPDX license identifiers map to the
    ``allowed | conditional | forbidden`` risk categories, plus the gate posture
    for uncatalogued / compound licenses and an explicit exception allow-list.

Why:
  - The license classification is currently a STATIC, global catalog baked into
    ``tasks/scan_source.py`` (``_LICENSE_CATEGORY_DEFAULTS`` +
    ``_classify_license_category``). A team cannot re-categorise a license or
    grant a time-boxed legal waiver without a code change + redeploy. This table
    makes the classification *data*, scoped per team and per org.
  - c1 (THIS migration) ships the data model + CRUD API only. c2 wires the
    policy gate to consult this table (and hardens compound-SPDX parsing);
    ``services.policy_gate`` is untouched here.

Scope semantics:
  - ``team_id IS NULL``     → org-level DEFAULT policy. At most one per org,
    enforced by the partial unique index ``uq_license_policies_org_default``
    (UNIQUE on organization_id WHERE team_id IS NULL). NULLs are distinct in a
    plain UniqueConstraint, so the constraint alone does NOT bound the org
    default — the partial index is the gate.
  - ``team_id IS NOT NULL`` → that team's policy. ``uq_license_policies_org_team``
    (UNIQUE organization_id, team_id) keeps it one-per-team.

JSONB column shapes (the contract c2 / c3 consume — see models.license_policy
for the full prose):
  - ``category_overrides``          object: SPDX id → "allowed"|"conditional"|"forbidden".
  - ``license_exceptions``          array of {"spdx_id","reason","expires_at"?,"component_purl"?}.
  - ``compound_operator_strategy``  object: "AND"|"OR"|"WITH" →
                                    "most_restrictive"|"least_restrictive".

Notes:
  - **Expand step only** (CLAUDE.md §6) — pure additive CREATE TABLE + indexes,
    matching 0017/0018. No data migration.
  - JSONB server defaults are emitted as inline DDL literals
    (``server_default=sa.text("'{}'::jsonb")``) inside ``op.create_table`` — NOT
    via raw ``op.execute`` with binds — so the asyncpg ``::`` / TIMESTAMPTZ bind
    pitfall (MEMORY: feedback_asyncpg_double_colon_param) never applies.
  - The closed gate-posture set is a CHECK-constrained VARCHAR rather than a
    native ENUM (mirrors ``api_keys.scope``).
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "license_policies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column(
            "category_overrides",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "license_exceptions",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "unknown_license_category",
            sa.String(length=16),
            server_default=sa.text("'conditional'"),
            nullable=False,
        ),
        sa.Column(
            "compound_operator_strategy",
            postgresql.JSONB(),
            server_default=sa.text(
                '\'{"AND": "most_restrictive", '
                '"OR": "least_restrictive", '
                '"WITH": "most_restrictive"}\'::jsonb'
            ),
            nullable=False,
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "organization_id",
            "team_id",
            name="uq_license_policies_org_team",
        ),
        sa.CheckConstraint(
            "unknown_license_category IN ('allowed', 'conditional', 'forbidden')",
            name="ck_license_policies_unknown_category_values",
        ),
    )

    # Single org-default per org: UNIQUE on organization_id over the org-default
    # rows (team_id IS NULL) only.
    op.create_index(
        "uq_license_policies_org_default",
        "license_policies",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("team_id IS NULL"),
    )
    op.create_index(
        "ix_license_policies_team_id",
        "license_policies",
        ["team_id"],
    )
    op.create_index(
        "ix_license_policies_created_by_user_id",
        "license_policies",
        ["created_by_user_id"],
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
