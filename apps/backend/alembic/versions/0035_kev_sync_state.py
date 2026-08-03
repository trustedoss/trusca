"""kev_sync_state — single-row KEV catalog refresh status table

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-03

Phase: KEV (ops closeout — admin/health KEV feed panel, C1)
PR: feat/kev-ops-closeout
Kind: schema (new empty table; no data migration)
Forward-only: yes

What:
  - Create table ``kev_sync_state``::
        id              BOOLEAN PK DEFAULT true   -- with CHECK (id): single-row
        last_synced_at  TIMESTAMPTZ               -- last SUCCESSFUL reconcile
        last_result     VARCHAR(16)               -- synced|skipped
        skipped_reason  VARCHAR(64)               -- disabled|feed_unavailable|
                                                  -- feed_below_sanity_floor|
                                                  -- unexpected:<ExceptionName>
        feed_count      INTEGER                   -- entries parsed from the feed
        listed          INTEGER                   -- rows flagged this run
        delisted        INTEGER                   -- rows cleared this run
        duration_ms     INTEGER
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()  -- last attempt
  - CHECK constraint ``ck_kev_sync_state_singleton`` (``CHECK (id)``): the PK
    forbids a second ``true`` row and the CHECK forbids ``false``, so the
    table holds at most the one row ``id=true``.

Why:
  - tasks/kev_catalog_refresh (0034's data-fill companion) emits its per-tick
    summary only as a structlog event; the admin/health KEV feed panel needs
    a durable "latest outcome" it can read. The Trivy DB panel derives
    freshness from on-disk DB metadata, but the KEV reconcile leaves no file
    behind — it writes straight into ``vulnerabilities`` — so a DB status row
    is the only durable source.
  - Single row (UPSERT on the PK) rather than an append-only log: the panel
    needs exactly the latest outcome, and history already lives in the log
    stream. Reads are a PK lookup; the table never grows.
  - ``last_synced_at`` tracks last SUCCESS only, ``updated_at`` tracks last
    ATTEMPT (skips update it via onupdate), so the panel can render both.

Notes:
  - No repo precedent for single-row tables — the bool-PK + CHECK pattern is
    introduced here (documented in models/kev_sync_state.py).
  - ``last_result`` / ``skipped_reason`` stay VARCHAR (not native ENUM): the
    vocabularies are owned by the task summary dict, same reasoning as 0033's
    ``result`` / ``source_format``.
  - No extra indexes: single-row table, PK is the only access path. No FK
    columns, no JSONB, no tenant scoping (system-global operational metadata,
    Super Admin read surface).
  - No seed row: the writer UPSERTs, so the first tick creates the row;
    "row absent" = "never ran", which the panel renders as such.
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises
    ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "kev_sync_state",
        sa.Column("id", sa.Boolean(), primary_key=True, server_default=sa.text("true")),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", sa.String(length=16), nullable=True),
        sa.Column("skipped_reason", sa.String(length=64), nullable=True),
        sa.Column("feed_count", sa.Integer(), nullable=True),
        sa.Column("listed", sa.Integer(), nullable=True),
        sa.Column("delisted", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=NOW,
        ),
        # Single-row enforcement (see module docstring "What").
        sa.CheckConstraint("id", name="ck_kev_sync_state_singleton"),
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
