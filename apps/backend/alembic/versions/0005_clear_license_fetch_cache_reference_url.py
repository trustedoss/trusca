"""clear license_fetch_cache.reference_url

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-07

Phase: 2 (chore PR #7)
PR: chore PR #7
Kind: data (schema unchanged)
Forward-only: yes

What:
  - ``UPDATE license_fetch_cache SET reference_url = NULL
     WHERE reference_url IS NOT NULL``.
  - The ``reference_url`` column itself is preserved (forward-only
    schema, per CLAUDE.md §6 "마이그레이션 정책"). We only clear the
    *data* sitting in it.

Why:
  - security-reviewer Medium #2 (chore PR #5 handoff §1.2). Until this
    PR, the Maven fetcher wrote the publisher-supplied POM ``<url>``
    and the pkg.go.dev fetcher wrote the constructed pkg.go.dev panel
    URL into the cache. Rows written before the fetcher change in
    commit ``7e2e8fa`` can still hold phishing URLs that, without this
    migration, would linger up to 24h (the cache TTL) after deploy.
  - The companion code change in chore PR #7 makes every fetcher emit
    ``reference_url=None`` going forward, so once the existing rows
    are cleared the column will not be re-populated naturally.
  - Splitting schema and data per CLAUDE.md §6: the column survives
    (forward-only), and the data wipe ships as its own revision so
    the schema and data stories evolve independently.

Idempotency:
  - The WHERE clause filters out rows that already have
    ``reference_url IS NULL``. A second run on a post-migration table
    is a no-op: every row was either cleared by the first run or
    refreshed by post-deploy fetcher activity (which writes
    ``reference_url=None``).
  - We deliberately do NOT reset ``fetched_at``. The phishing URL was
    the only attacker-controlled bit; ``spdx_id`` was always SPDX-
    normalised by the fetcher pipeline and is safe to keep. Forcing a
    re-fetch wave (~200 components × per-host rate limit) on deploy
    would be 30+ minutes of wall-clock cost for no security gain.

Rollback:
  - ``downgrade()`` raises ``NotImplementedError`` per the forward-only
    policy. The cleared URLs are intentionally unrecoverable — the
    security review asked us to drop them.

Deploy ordering (security-reviewer Low, chore PR #7):
  - Roll the worker pods to the chore PR #7 image **first** (so all
    fetcher writes set ``reference_url=None``), then run this
    migration. With Docker Compose::

        docker-compose -f docker-compose.yml up -d --no-deps celery-worker
        # wait for the new worker to drain old tasks (~scan timeout)
        docker-compose -f docker-compose.yml exec backend alembic upgrade head

    Reversed ordering opens a small race window where an old worker
    can re-write a phishing URL between the UPDATE and the worker
    rollout completing. The risk is operationally bounded (<1 min on
    a normal rollout) but we document the ordering for completeness.
  - The Helm chart upgrade hook (chore PR #8 candidate) should encode
    this ordering as a pre-install init job that waits for worker
    rollout completion.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE license_fetch_cache
            SET reference_url = NULL
            WHERE reference_url IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
