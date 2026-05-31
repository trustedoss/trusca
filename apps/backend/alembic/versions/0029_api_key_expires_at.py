"""api_key_expires_at — optional API-key expiry (TTL)

Revision ID: 0029
Revises: 0028
Created: 2026-05-30

Phase: post-v2.4 (api-key auth follow-up)
Kind: schema
Forward-only: yes

What:
  - Add nullable ``expires_at TIMESTAMPTZ`` to ``api_keys``.

Why:
  - API keys had no expiry — a ``tos_`` credential lived until manual revocation
    (security-reviewer Low on the api-key-scan-auth PR). Now that the CI
    scan-action drives state-changing endpoints with these keys, a leaked CI key
    (pipeline log, forked-PR runner) should be able to expire on its own.
  - NULL = never expires (existing keys keep working unchanged — non-breaking).
    ``services.api_key_service.authenticate_api_key`` excludes a key once now()
    passes ``expires_at``, on the same query-layer path as revoked keys.

Backfill:
  - None. Existing rows get ``expires_at = NULL`` (never expires).

Downgrade:
  - Forward-only per CLAUDE.md §6.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
