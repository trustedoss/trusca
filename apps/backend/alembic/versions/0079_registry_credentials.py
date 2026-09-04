"""registry_credentials: per-organization private registry logins

Revision ID: 0079
Revises: 0078
Create Date: 2026-09-04

Kind: schema (new empty table; no data migration)
Forward-only: yes

What:
  - Create table ``registry_credentials``::
        id                   UUID PK DEFAULT gen_random_uuid()
        organization_id      UUID NOT NULL REFERENCES organizations ON DELETE CASCADE
        registry_host        VARCHAR(255) NOT NULL   -- 'ghcr.io', 'registry:5000'
        username             VARCHAR(255) NOT NULL
        password_encrypted   TEXT NOT NULL           -- Fernet ciphertext
        created_by_user_id   UUID REFERENCES users ON DELETE SET NULL
        created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
  - UNIQUE (organization_id, registry_host).
  - Index on organization_id for the per-scan lookup.
  - GRANT SELECT, INSERT, UPDATE, DELETE to ``trustedoss_app`` where present.

Why:
  - Container scans could only reach public registries. The adapter docstring
    said credentials come from ``~/.docker/config.json``, but nothing mounted
    one, so an enterprise image (nearly all of which are private) failed to
    pull. This is where the login lives so the worker can write that file per
    scan.
  - Scoped to the ORGANIZATION, not the team or the project. A private
    registry is deployment infrastructure that every team publishing from it
    shares; per-team rows would mean the same secret pasted once per team, and
    rotating it would mean finding all of them.
  - ``registry_host`` is part of the unique key and is what binds a credential
    to one registry. The worker writes it into the ``auths`` map keyed by that
    host, so Trivy offers it only when pulling from there and a credential for
    one registry is never sent to another.
  - ``password_encrypted`` holds Fernet ciphertext produced by
    ``core.crypto.encrypt_secret``, the same mechanism ``github_app_credentials
    .private_key_encrypted`` and ``projects.git_credential_encrypted`` use. The
    column name is registered in ``core.audit._SENSITIVE_COLUMNS`` so an
    add / rotate / delete never copies the ciphertext into ``audit_logs.diff``.
  - DELETE is granted here, unlike the sync-state tables: an operator removing
    a registry must be able to remove its credential.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0079"
down_revision: str | None = "0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "registry_credentials",
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
        sa.Column("registry_host", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
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
            "registry_host",
            name="uq_registry_credentials_org_host",
        ),
    )
    # The per-scan lookup is "every credential for this organization", so the
    # organization is the leading column.
    op.create_index(
        "ix_registry_credentials_organization_id",
        "registry_credentials",
        ["organization_id"],
    )

    # A new table inherits no privileges, so without this the API cannot manage
    # rows and the worker cannot read them under the least-privilege app role.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trustedoss_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE
                    ON registry_credentials TO trustedoss_app;
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
