"""component_versions version-currency columns (offline half).

What
  Adds the version-currency signal to the shared ``component_versions``
  catalog — a sibling of the EOL columns (0038) derived from the SAME
  endoflife.date match. Where EOL answers "is this release line dead?",
  currency answers "is this version behind the newest patch of its release
  line?".

    currency_state               VARCHAR(16) NULL  -- 'current'|'outdated'|'unknown'
    currency_latest              VARCHAR(64) NULL  -- cycle's newest patch (snapshot `latest`)
    currency_latest_release_date DATE NULL         -- snapshot `latestReleaseDate`
    currency_evaluated_at        TIMESTAMPTZ NULL  -- last currency stamp

Why here (not ScanComponent)
  Currency is a property of a ``purl_with_version`` shared across every scan
  observing it — the same rationale as the EOL and KEV catalog columns.

Notes
  - Offline half only: values come from the vendored endoflife.date snapshot's
    per-cycle ``latest`` (zero network at scan time). The deps.dev
    "absolute newest across the ecosystem / N releases behind" half is a
    separate opt-in egress path, not this revision.
  - Partial index mirrors the EOL one: the ``?currency=outdated`` filter and
    the overview count read only the outdated minority. Not created
    CONCURRENTLY — the index covers zero rows at build time (0038 note).
  - Stamping is done at the application layer (services/eol/eol_catalog.py
    ``stamp_component_version`` on both the scan-persist hook and the weekly
    refresh beat) — kept out of this schema revision per schema/data
    separation.
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises NotImplementedError.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "component_versions",
        sa.Column("currency_state", sa.String(16), nullable=True),
    )
    op.add_column(
        "component_versions",
        sa.Column("currency_latest", sa.String(64), nullable=True),
    )
    op.add_column(
        "component_versions",
        sa.Column("currency_latest_release_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "component_versions",
        sa.Column("currency_evaluated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_component_versions_currency_outdated",
        "component_versions",
        ["currency_state"],
        unique=False,
        postgresql_where=sa.text("currency_state = 'outdated'"),
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migration (CLAUDE.md §6)")
