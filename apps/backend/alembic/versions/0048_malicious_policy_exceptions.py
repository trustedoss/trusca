"""license_policies.malicious_exceptions (#26 MAL-2).

Adds a JSONB array of temporary waivers for packages the malicious snapshot
flags: ``[{"component_purl", "reason", "expires_at"}, ...]``.

Why a separate array rather than a shape on ``license_exceptions``: that one
keys on ``spdx_id`` and this one on a package identifier, so a single array
would need a discriminator and both readers would have to filter. The expiry
rule differs too — ``expires_at`` is REQUIRED here.

Why the expiry is mandatory: a licence waiver can reasonably be permanent
(counsel cleared this dependency and the answer will not change), but a
malicious flag always resolves. Either the advisory is wrong, in which case
challenging it upstream drops the package from the next snapshot, or it is
right, in which case the package has to go. An open-ended waiver would only
park an unfinished decision out of sight.

The waiver removes a component from the gate count. It does not touch the
badge, the filter or the drawer — deferring a block is not the same as hiding
the signal.

Forward-only per CLAUDE.md §6.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "license_policies",
        sa.Column(
            "malicious_exceptions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only (CLAUDE.md §6).")
