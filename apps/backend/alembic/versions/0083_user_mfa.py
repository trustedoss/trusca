# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""users: TOTP enrolment, replay state, and one-time recovery codes.

Four columns on ``users`` and one table.

  - ``mfa_secret_encrypted`` holds Fernet ciphertext from
    ``core.crypto.encrypt_secret(purpose="totp")``. A derived subkey rather
    than the shared credential key: a stolen TOTP secret generates a second
    factor for ever, and it should not fall out of the same leak that exposes
    a forge credential.
  - ``mfa_enabled`` is separate from the secret on purpose. A secret is stored
    the moment somebody scans the QR code, before they have proved their app
    can produce a code from it. Enabling at that point locks out anyone who
    closed the tab mid-setup: the next sign-in asks for a code they cannot
    produce. The flag turns on only after a code has been verified once.
  - ``mfa_last_counter`` is the TOTP step most recently accepted. A code stays
    valid for its whole thirty-second step, so without remembering the step,
    somebody who observes a code can present it again until the step ends.
  - ``mfa_changed_at`` invalidates sessions the way ``password_changed_at``
    does, and is a second column rather than a rename of that one. Renaming a
    column a just-shipped security check reads, with a guard test pinned to it,
    buys tidiness at the price of an expand-migrate-contract on the
    authentication path. Two columns also record *why* a session ended.

``user_recovery_codes`` is one row per code, holding a bcrypt hash and a
``used_at``. A spent row is kept rather than deleted so the account page can
say how many are left and when each was used.

Regenerating deletes the *unused* rows and issues a new set. Only the unused
ones, because a spent code is already refused and deleting it would erase the
history the page shows; and all of the unused ones, because somebody
regenerating usually believes the old set leaked, and leaving any of them live
would defeat the reason they asked.

An administrator clearing somebody's second factor has to undo the whole
enrolment, not just the flag: ``mfa_enabled`` down, ``mfa_secret_encrypted``
cleared, ``mfa_last_counter`` cleared, every remaining recovery code deleted,
and ``mfa_changed_at`` stamped so existing sessions end. Leaving the secret
behind would let the account re-enable with the same one, and the reason
somebody asks for this is usually that the device or the secret is gone.

Forward-only: ``downgrade`` raises. Dropping ``mfa_secret_encrypted`` would
destroy the enrolment, and the codes cannot be reissued from their hashes.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0083"
down_revision: str | None = "0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("users", sa.Column("mfa_secret_encrypted", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("mfa_last_counter", sa.BigInteger(), nullable=True))
    op.add_column(
        "users",
        sa.Column("mfa_changed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "user_recovery_codes",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Verifying an unknown code compares it against every unused row for that
    # user, so the read is always "this user's live codes" and never anything
    # else. Partial on used_at so spent rows, which are kept for the account
    # page, do not widen it.
    op.create_index(
        "ix_user_recovery_codes_user_unused",
        "user_recovery_codes",
        ["user_id"],
        postgresql_where=sa.text("used_at IS NULL"),
    )

    # A new table inherits no privileges, so without this the API cannot manage
    # rows under the least-privilege app role.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trustedoss_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE
                    ON user_recovery_codes TO trustedoss_app;
            ELSE
                RAISE NOTICE 'trustedoss_app role not found - '
                    'single-role legacy mode (no-op)';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "forward-only: dropping mfa_secret_encrypted destroys the enrolment and "
        "recovery codes cannot be reissued from their hashes"
    )
