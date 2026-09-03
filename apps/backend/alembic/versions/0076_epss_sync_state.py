"""epss_sync_state: single-row EPSS score sync status table

Revision ID: 0076
Revises: 0075
Create Date: 2026-09-03

Kind: schema (new empty table; no data migration)
Forward-only: yes

What:
  - Create table ``epss_sync_state``::
        id              BOOLEAN PK DEFAULT true   -- with CHECK (id): single-row
        last_synced_at  TIMESTAMPTZ               -- last SUCCESSFUL sync
        last_result     VARCHAR(16)               -- synced|skipped
        skipped_reason  VARCHAR(64)               -- disabled|feed_unavailable|
                                                  -- feed_below_sanity_floor|
                                                  -- unexpected:<ExceptionName>
        model_version   VARCHAR(64)               -- EPSS model the feed declared
        score_date      TIMESTAMPTZ               -- scoring time the feed declared
        feed_rows       INTEGER                   -- rows read from the document
        matched         INTEGER                   -- feed rows present in our catalog
        updated         INTEGER                   -- of those, how many changed
        duration_ms     INTEGER
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()  -- last attempt
  - CHECK constraint ``ck_epss_sync_state_singleton`` (``CHECK (id)``).
  - GRANT SELECT, INSERT, UPDATE to ``trustedoss_app`` where that role exists.

Why:
  - ``vulnerabilities.epss_score`` and ``epss_percentile`` have existed since
    v2.4 and nothing ever wrote them: the scanner does not emit EPSS on either
    the SBOM or the image path, so the Vulnerabilities tab's EPSS column, the
    ``min_epss`` filter, the ``sort=priority`` ranking and the optional
    ``GATE_EPSS_THRESHOLD`` gate were all reading empty columns. The daily
    sync fills them, and this table is where its outcome is durable.
  - Single row UPSERTed on the PK, not an append-only log: the admin panel
    needs the latest outcome and history is already in the log stream. Same
    pattern and rationale as ``kev_sync_state`` (0035) and ``eol_sync_state``
    (0039).
  - ``model_version`` / ``score_date`` are EPSS-specific. The scores come from
    a model that is re-run and re-published, so which run produced the values
    currently in the catalog is an operational question the numbers themselves
    cannot answer.

Notes:
  - No new value columns: ``epss_score`` / ``epss_percentile`` and the
    ``ix_vulnerabilities_epss_score`` index already exist on
    ``vulnerabilities``. This migration adds the status table only.
  - Grants are explicit. A new table gives ``trustedoss_app`` nothing, so the
    GRANT below is what makes the sync able to write at all. No DELETE: the
    row is created once and UPSERTed forever. Mirrors ``kev_sync_state``,
    which holds SELECT/INSERT/UPDATE and is registered the same way in
    ``tests/fixtures/app_role_privileges.json``.
  - No seed row: the writer UPSERTs, so the first tick creates it, and "row
    absent" reads as "never ran", which is what the panel shows.
  - ``last_result`` / ``skipped_reason`` stay VARCHAR rather than a native
    ENUM: the vocabularies are owned by ``models.sync_state`` and the task
    summary, same reasoning as 0035.
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises NotImplementedError.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0076"
down_revision: str | None = "0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "epss_sync_state",
        sa.Column("id", sa.Boolean(), primary_key=True, server_default=sa.text("true")),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", sa.String(length=16), nullable=True),
        sa.Column("skipped_reason", sa.String(length=64), nullable=True),
        sa.Column("model_version", sa.String(length=64), nullable=True),
        sa.Column("score_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("feed_rows", sa.Integer(), nullable=True),
        sa.Column("matched", sa.Integer(), nullable=True),
        sa.Column("updated", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=NOW,
        ),
        # Single-row enforcement (see module docstring "What").
        sa.CheckConstraint("id", name="ck_epss_sync_state_singleton"),
    )

    # A new table inherits no privileges, so without this the sync cannot
    # write its own status row under the least-privilege app role.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trustedoss_app') THEN
                GRANT SELECT, INSERT, UPDATE
                    ON epss_sync_state TO trustedoss_app;
            ELSE
                RAISE NOTICE 'trustedoss_app role not found - '
                    'single-role legacy mode (no-op)';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
