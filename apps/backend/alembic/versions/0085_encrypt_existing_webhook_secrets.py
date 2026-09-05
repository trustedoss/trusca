"""projects: encrypt the webhook secrets already stored (migrate data)

Revision ID: 0085
Revises: 0084
Create Date: 2026-09-05

Kind: data
Forward-only: yes
Idempotent: yes

Every project whose webhook is already active holds a plaintext secret that
GitHub or GitLab also holds. Regenerating it here would break those deliveries
until somebody pasted a new value into the SCM, and nothing would say so: the
gateway would refuse each delivery and the only symptom is that scans stop
starting. So the existing value is carried over rather than replaced.

Idempotent by predicate, not by a flag: only rows that still have plaintext and
do not yet have ciphertext are touched. Re-running does nothing.

What this cannot promise
------------------------
``encrypt_secret`` uses ``GITHUB_APP_ENCRYPTION_KEY`` when it is set and
otherwise derives a key from ``SECRET_KEY``, warning as it does. A deployment
that runs this with no dedicated key and later sets one has ciphertext it
cannot read, and the failure surfaces as webhook deliveries being refused.

That is the same exposure the GitHub App credentials and the per-project git
credential already carry, so this does not add a new one. It does widen it:
those are written one at a time by somebody who is present, and this writes
every project at once during an upgrade nobody is watching. The upgrade notes
say to set the key first.

The row count goes into the log rather than being returned, because an upgrade
that encrypted nothing (no active webhooks) and one that failed to find the
table look identical without it.
"""

from __future__ import annotations

import sqlalchemy as sa
import structlog

from alembic import op

revision: str = "0085"
down_revision: str | None = "0084"
branch_labels: str | None = None
depends_on: str | None = None

log = structlog.get_logger("alembic.0085")


def upgrade() -> None:
    # Imported inside the function: at module import time Alembic is still
    # building the revision map, and a failure to import application code then
    # is reported as a broken migration graph rather than as what it is.
    from core.crypto import encrypt_secret

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, webhook_secret FROM projects "
            " WHERE webhook_secret IS NOT NULL "
            "   AND webhook_secret_encrypted IS NULL"
        )
    ).fetchall()

    for row in rows:
        bind.execute(
            sa.text(
                "UPDATE projects SET webhook_secret_encrypted = :ciphertext "
                " WHERE id = :project_id"
            ),
            {"ciphertext": encrypt_secret(row.webhook_secret), "project_id": row.id},
        )

    log.info("webhook_secret_encrypted_backfill", projects=len(rows))


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    raise NotImplementedError("0085 is forward-only")
