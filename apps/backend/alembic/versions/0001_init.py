"""initial empty migration

Revision ID: 0001
Revises:
Create Date: 2026-05-05

The first migration is intentionally empty. Domain models land in Phase 1
(auth + RBAC) and Phase 2 (scan pipeline).

Forward-only policy per CLAUDE.md §6 (Migration policy): downgrade() raises
NotImplementedError.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
