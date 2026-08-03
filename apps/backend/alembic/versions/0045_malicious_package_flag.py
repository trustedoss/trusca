"""component_versions malicious-package flag (#26).

What
  Adds the known-malicious signal to the shared ``component_versions``
  catalog, beside the EOL (0038) and currency (0040) columns.

    malicious_state        VARCHAR(16) NULL  -- 'flagged'|'clear'
    malicious_id           VARCHAR(32) NULL  -- OSV advisory id ('MAL-2025-47141')
    malicious_source       VARCHAR(64) NULL  -- 'osv.dev@YYYY-MM-DD'
    malicious_evaluated_at TIMESTAMPTZ NULL  -- last malicious stamp

Why this is not another vulnerability column
  A CVE says an honest package has a flaw you can patch; the response is an
  upgrade. A malicious package was published to attack whoever installs it —
  typosquats, hijacked maintainer accounts, install-time payloads — and the
  response is removal plus rotation of every credential the build could reach.
  Recording it as a finding would put it on the severity axis and tell the
  reader to schedule an upgrade, which is the wrong action. It is a catalog
  fact about a ``purl_with_version``, shared by every scan observing it, so it
  lives here for the same reason EOL and KEV do.

Why NULL and 'clear' are different
  The EOL map is a whitelist, so an unmapped component is simply unknown and
  stays NULL. The malicious index is a DENY list: "absent from the index" is a
  real verdict — the snapshot looked and did not find it. NULL is reserved for
  "never evaluated" (no snapshot loaded, feature off, or a row predating this
  revision). Surfaces must therefore distinguish the two and must not draw a
  reassuring zero where nothing was assessed.

Notes
  - Column widths from the 2026-08-03 snapshot: advisory ids are at most 15
    chars ('MAL-2025-192894'), source is a fixed 18-char shape. Both sized with
    headroom, matching the EOL columns' convention.
  - Partial index mirrors 0038/0040: flagged rows are a vanishing minority (a
    healthy project matches none of the 232,747 known-malicious PURLs), while
    'clear' would index nearly every row for no query. Not created
    CONCURRENTLY — the index covers zero rows at build time (0038 note).
  - Stamping is done at the application layer
    (services/malicious/malicious_catalog.py ``stamp_component_version``, on
    the scan-persist hook and later the weekly re-stamp beat) — kept out of
    this schema revision per schema/data separation.
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises NotImplementedError.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "component_versions",
        sa.Column("malicious_state", sa.String(16), nullable=True),
    )
    op.add_column(
        "component_versions",
        sa.Column("malicious_id", sa.String(32), nullable=True),
    )
    op.add_column(
        "component_versions",
        sa.Column("malicious_source", sa.String(64), nullable=True),
    )
    op.add_column(
        "component_versions",
        sa.Column(
            "malicious_evaluated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_component_versions_malicious",
        "component_versions",
        ["malicious_state"],
        unique=False,
        postgresql_where=sa.text("malicious_state = 'flagged'"),
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only (CLAUDE.md §6).")
