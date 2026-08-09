# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""record the license a project is distributed under

Revision ID: 0050
Revises: 0049
Create Date: 2026-08-09

Phase: outbound-license conflict (gap #27)
Kind: schema (additive — one nullable column; no data migration)
Forward-only: yes

What:
  ``projects.declared_license`` — the SPDX id or expression the project itself
  is distributed under ("Apache-2.0", "MIT OR Apache-2.0"). Nullable, 255
  chars, no default.

Why:
  A license conflict only exists relative to something. The existing license
  axis asks "is this dependency's license allowed here", which a policy can
  answer on its own; "can this dependency be combined with what WE ship" cannot
  be answered without knowing what we ship. ``services/license_conflict.py``
  reads this column and produces nothing at all when it is NULL.

Why nullable with no default:
  NULL means "not declared", which is not the same as any particular license
  and must not be guessed. A default would make every existing project claim an
  outbound license nobody chose, and the verdicts computed from it would look
  like findings rather than an artifact of the migration. An absent value
  yields an absent verdict — never a clean one.

Why not derived from the scanned SBOM:
  ``metadata.component.licenses`` is available on many uploaded documents and
  upstream does use it. A project here accumulates scans from several sources,
  so picking one document's declaration would show users verdicts against a
  premise they never stated. The value is entered deliberately, in project
  settings.

Sizing:
  255 chars. A single SPDX id is short; the longest realistic value is a
  multi-way expression, and ``schemas.scan`` bounds the input to the same
  length before it reaches the column. The expression evaluator's own ceiling
  (``MAX_EXPRESSION_LENGTH`` = 4096) is far above what is storable, so a value
  that fits here always parses without hitting the length guard.

Migration policy (CLAUDE.md §6):
  - Additive only; no backfill, no data migration.
  - Forward-only: ``downgrade()`` raises ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("declared_license", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migration (CLAUDE.md §6)")
