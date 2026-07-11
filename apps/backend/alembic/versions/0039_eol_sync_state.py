"""eol_sync_state — single-row endoflife.date refresh status table

Revision ID: 0039
Revises: 0038
Create Date: 2026-07-11

Phase: EOL (ops closeout — refresh beat + admin/health EOL panel, Phase M)
PR: feat/eol-ops
Kind: schema (new empty table; no data migration)
Forward-only: yes

What:
  - Create table ``eol_sync_state``::
        id              BOOLEAN PK DEFAULT true   -- with CHECK (id): single-row
        last_synced_at  TIMESTAMPTZ               -- last SUCCESSFUL feed fetch
        last_result     VARCHAR(16)               -- synced|skipped (fetch half)
        skipped_reason  VARCHAR(64)               -- disabled|refresh_disabled|
                                                  -- feed_unavailable|
                                                  -- feed_below_sanity_floor|
                                                  -- unexpected:<ExceptionName>
        snapshot        JSONB                     -- fetched compact dataset (few KB)
        snapshot_date   DATE                      -- snapshot["_snapshot"], denormalised
        products_ok     INTEGER                   -- products fetched this sync
        products_failed INTEGER                   -- products that failed this sync
        stamped         INTEGER                   -- catalog rows (re)stamped this tick
        cleared         INTEGER                   -- stale stamps cleared this tick
        duration_ms     INTEGER
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()  -- last attempt
  - CHECK constraint ``ck_eol_sync_state_singleton`` — same single-row
    construction as 0035 (``kev_sync_state``).

Why:
  - Clone of the 0035 pattern with one structural addition: the fetched
    dataset itself rides the row (``snapshot`` JSONB). The KEV reconcile
    writes straight into ``vulnerabilities`` and needs no dataset store; the
    EOL weekly re-stamp pass must prefer the freshest dataset — fetched OR
    vendored — without a network call, so the fetched one needs a durable
    home. A few KB of JSONB on a single-row table is the cheapest one.
  - ``snapshot_date`` is denormalised out of the JSONB so the health panel's
    staleness computation (warn tone past 180 days) is a plain DATE read.
  - ``stamped`` / ``cleared`` update on EVERY tick — the re-stamp half runs
    even when the fetch is disabled (pure-local), unlike the KEV counters.

Notes:
  - Vocabularies stay VARCHAR (task-owned closed sets — 0035 reasoning).
  - No extra indexes: single-row table, PK is the only access path.
  - No seed row: first tick inserts; "row absent" = "never ran".
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises
    ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "eol_sync_state",
        sa.Column("id", sa.Boolean(), primary_key=True, server_default=sa.text("true")),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", sa.String(length=16), nullable=True),
        sa.Column("skipped_reason", sa.String(length=64), nullable=True),
        sa.Column("snapshot", JSONB(), nullable=True),
        sa.Column("snapshot_date", sa.Date(), nullable=True),
        sa.Column("products_ok", sa.Integer(), nullable=True),
        sa.Column("products_failed", sa.Integer(), nullable=True),
        sa.Column("stamped", sa.Integer(), nullable=True),
        sa.Column("cleared", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=NOW,
        ),
        sa.CheckConstraint("id", name="ck_eol_sync_state_singleton"),
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migration (CLAUDE.md §6)")
