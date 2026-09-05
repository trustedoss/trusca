"""projects: a column for the webhook secret as ciphertext (expand)

Revision ID: 0084
Revises: 0083
Create Date: 2026-09-05

Kind: schema (one column)
Forward-only: yes

What was wrong
--------------
This product stores the same kind of value in two places and encrypts one of
them. ``github_app_credentials.webhook_secret_encrypted`` is Fernet ciphertext
and has been since 0019. ``projects.webhook_secret`` is the plaintext shared
secret, in a ``VARCHAR(64)``, since 0009. Both are webhook HMAC secrets; both
let whoever holds them forge a delivery this deployment will accept.

So the judgement that this value is encrypted at rest was already made here.
It was not applied to one of the two places holding it. This revision is the
first of three that finish applying it.

Why not a hash
--------------
The value has to come back. GitHub signs the payload with it and the gateway
recomputes the HMAC (``verify_github_signature``); GitLab sends it verbatim and
the gateway compares in constant time (``verify_gitlab_token``). Neither works
against a digest. Reversible encryption is the treatment available.

The three steps
---------------
0084 (this one) adds the column. 0085 encrypts what is already there into it.
0086 drops the plaintext column. Split because a data migration that has to
call application code should not also be the thing that changes the shape, and
because the middle step is the one that can be re-run.

Nothing reads the new column yet, and nothing stops reading the old one, so a
deployment that stops here still works.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0084"
down_revision: str | None = "0083"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Text rather than a bounded VARCHAR. A Fernet token's length follows the
    # plaintext, and pinning a maximum here would be pinning it to today's
    # 64-character secret; a longer one later would fail on write.
    op.add_column(
        "projects",
        sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    raise NotImplementedError("0084 is forward-only")
