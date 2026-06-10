"""github_app_credentials: live-only partial unique for (team_id, app_id)

Revision ID: 0031
Revises: 0030
Created: 2026-06-10

Phase: validation-remediation (recheck §4-3)
Kind: schema
Forward-only: yes

What:
  - Drop the full-table ``uq_github_app_credentials_team_app`` unique
    constraint on ``(team_id, app_id)``.
  - Create the partial unique index
    ``uq_github_app_credentials_team_app_active`` on ``(team_id, app_id)
    WHERE revoked_at IS NULL``.

Why:
  - Revocation is a soft delete (``revoked_at`` flips, the row stays for the
    audit trail — model docstring: "a re-register after revoke is a NEW row").
    The full-table unique counted revoked rows too, so revoking a credential
    and re-registering the same GitHub App (normal key rotation) was a
    permanent 409. The partial unique enforces ONE live registration per
    (team, app) while letting revoked history accumulate. Mirrors the
    ``api_keys`` precedent (``ix_api_keys_active``, mig 0009).

Backfill:
  - None. The full unique guaranteed no (team_id, app_id) duplicates exist at
    upgrade time, so the partial unique (a strictly weaker predicate over live
    rows) cannot fail to build.

Downgrade:
  - Forward-only per CLAUDE.md §6. Re-adding the full unique could fail once
    revoked duplicates exist, so there is no safe automatic reverse.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_github_app_credentials_team_app",
        "github_app_credentials",
        type_="unique",
    )
    op.create_index(
        "uq_github_app_credentials_team_app_active",
        "github_app_credentials",
        ["team_id", "app_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6): restoring the full-table unique would fail
    # once a (team_id, app_id) pair has revoked duplicates.
    pass
