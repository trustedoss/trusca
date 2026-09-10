"""ticket_credentials + vulnerability_findings ticket status columns (#385)

Revision ID: 0092
Revises: 0091
Create Date: 2026-09-10

Kind: schema (new empty table + new nullable columns; no data migration)
Forward-only: yes

What:
  - Create table ``ticket_credentials``::
        id                     UUID PK DEFAULT gen_random_uuid()
        organization_id        UUID NOT NULL REFERENCES organizations ON DELETE CASCADE
        host                   VARCHAR(255) NOT NULL   -- 'mycompany.atlassian.net'
        auth_scheme            VARCHAR(32) NOT NULL    -- 'jira_basic' today
        username               VARCHAR(255)            -- NULL for a bearer scheme
        api_token_encrypted    TEXT NOT NULL           -- Fernet ciphertext
        created_by_user_id     UUID REFERENCES users ON DELETE SET NULL
        created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
        updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
    UNIQUE (organization_id, host). Index on organization_id. GRANT SELECT,
    INSERT, UPDATE, DELETE to ``trustedoss_app`` where present.
  - Add four nullable columns to ``vulnerability_findings``:
        ticket_status          VARCHAR(64)     -- tracker's own status name
        ticket_resolved        BOOLEAN         -- derived closed-vocabulary reading
        ticket_checked_at      TIMESTAMPTZ
        ticket_check_error     TEXT

Why:
  - #385: a finding can carry a ``ticket_url`` (already existed), but nothing
    ever read the ticket back, so a finding whose ticket was closed elsewhere
    still showed as open here. This adds the credential a caller needs to ask
    an external tracker, and the columns the answer lands in.
  - ``ticket_credentials`` mirrors ``registry_credentials`` (0079) for the
    same reason: a tracker is shared infrastructure (one Jira site per
    company, not one per team), so per-team rows would mean the same token
    pasted once per team.
  - ``api_token_encrypted`` holds Fernet ciphertext from
    ``core.crypto.encrypt_secret``, the same mechanism
    ``registry_credentials.password_encrypted`` uses. The column name is
    registered in ``core.audit._SENSITIVE_COLUMNS`` so an add / rotate /
    delete never copies the ciphertext into ``audit_logs.diff``.
  - ``ticket_resolved`` is separate from ``ticket_status`` because a tracker's
    status NAMES are per-workflow (a customer's renamed "Done" column), but
    Jira's ``statusCategory.key`` is a closed vocabulary underneath any
    rename; this column stores that normalized reading, ``ticket_status``
    the display string.
  - DELETE is granted on ``ticket_credentials``, matching ``registry_
    credentials``: an operator removing a tracker must be able to remove its
    credential.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0092"
down_revision: str | None = "0091"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "ticket_credentials",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("auth_scheme", sa.String(length=32), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("api_token_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "organization_id",
            "host",
            name="uq_ticket_credentials_org_host",
        ),
    )
    op.create_index(
        "ix_ticket_credentials_organization_id",
        "ticket_credentials",
        ["organization_id"],
    )

    op.add_column(
        "vulnerability_findings",
        sa.Column("ticket_status", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "vulnerability_findings",
        sa.Column("ticket_resolved", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "vulnerability_findings",
        sa.Column("ticket_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "vulnerability_findings",
        sa.Column("ticket_check_error", sa.Text(), nullable=True),
    )

    # A new table inherits no privileges, so without this the API cannot
    # manage rows under the least-privilege app role. The new
    # vulnerability_findings columns need no grant of their own; the
    # existing table-level GRANT already covers them.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trustedoss_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE
                    ON ticket_credentials TO trustedoss_app;
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
