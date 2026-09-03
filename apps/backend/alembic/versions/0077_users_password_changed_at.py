# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""users: record when the password last changed, so old tokens can be refused.

Changing a password did not end the sessions that were already open. Access
tokens are verified by signature, expiry and a lookup of the subject, and
nothing in that path consulted the password, so a token minted before the
change kept working until it expired on its own (thirty minutes by default).

Refresh tokens were already revoked on a reset, so an attacker could not renew
a session. They did not need to: the access token they already held stayed
valid for the rest of its life. Changing a password is what somebody does when
they think a credential has leaked, and it is the one moment where that window
matters most.

This column is what closes it. Every access token carries ``iat``, so a token
minted before this timestamp can be refused with no per-token revocation list,
no store to keep and no extra query: the value is read from the user row the
request already loads.

Nullable, and left NULL for every existing row. Backfilling it with the
deployment time would invalidate every token issued before the upgrade, and
logging out every user of a running deployment is not something an upgrade may
do without being asked. NULL reads as "never changed", and the check is
skipped, so existing sessions survive and the protection applies from each
user's next password change onward.

No grant is needed: ``trustedoss_app`` already holds UPDATE on ``users``, and
this adds a column to a table the role can already write.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0077"
down_revision: str | None = "0076"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "When the password was last changed. Access tokens issued "
                "before this are refused. NULL means never changed since the "
                "column was added, and the check is skipped."
            ),
        ),
    )


def downgrade() -> None:
    """Forward-only (CLAUDE.md §6). Dropping this would silently un-revoke."""
    raise NotImplementedError("migrations are forward-only")
