# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""rename user_role enum value team_admin to group_admin

Revision ID: 0089
Revises: 0088
Create Date: 2026-09-07

Phase: 0 (group-hierarchy rollout, PR 0-1)
Kind: schema (one enum label rename, no new value, no data migration)
Forward-only: yes

What:
  ``ALTER TYPE user_role RENAME VALUE 'team_admin' TO 'group_admin'``.

  ``user_role`` is created in 0002 as
  ``ENUM ('super_admin', 'team_admin', 'developer')`` and 0055 adds
  ``'viewer'``. ``team_admin`` is the only enum label anywhere in this schema
  that spells the old vocabulary, and it is not repeated in any CHECK
  constraint, default value or other DDL (confirmed by grepping every
  ``alembic/versions/*.py`` file for the literal ``team_admin``; only 0002,
  the CREATE TYPE statement itself, matches besides this one).

  ``RENAME VALUE`` relabels the existing catalog entry in place: every
  ``memberships`` row that currently reads ``role = 'team_admin'`` reads
  ``role = 'group_admin'`` the instant this migration commits, with no row
  rewritten and no data migration needed. Unlike ``ADD VALUE`` (see 0055),
  ``RENAME VALUE`` has no same-transaction restriction and needs no
  ``IF NOT EXISTS`` guard: it fails loudly if the label is already renamed,
  which is the correct behaviour for a rename rather than an addition.

Why:
  Group-hierarchy rollout PR 0-1 (see 0088's docstring for the full plan).
  ``team_admin`` is the role name for "administers one group"; once ``teams``
  is ``groups`` the role name should say so too, for the same reason the
  table and its FK columns do.

A load-bearing difference from 0088, worth stating plainly
-----------------------------------------------------------
0088's six column renames are invisible to running application code because
every renamed column keeps a ``team_id`` synonym (see
``models/auth.py``, ``models/scan.py``, ``models/api_key.py``,
``models/license_policy.py``, ``models/gate_policy.py``): ``Model.team_id``
still reads and writes the same underlying storage under either name, so nothing
that read ``.team_id`` before this PR notices a difference.

An enum label has no equivalent bridge. PostgreSQL does not support two labels
that alias the same value, and SQLAlchemy's ``synonym`` only remaps a Python
attribute name to a column, not a stored string to another string. Every place
in the application layer that compares a role against the literal string
``"team_admin"`` (``core/security.py``'s privilege table, every schema and API
module that lists roles, both seed scripts, 47 non-test files in total by a
plain grep) starts comparing against a value the database no longer produces
the moment this migration lands. That is a real behavioural change, not a
naming one, and it does not have PR 0-1's "callers are unaffected" property
that the six column renames do.

This migration ships because the task that requested it asked for it
explicitly and by name; it is called out here, deliberately loudly, as a
question for whoever sequences 0-1 through 0-4: merging 0089 well before the
call-site PRs land would break every one of those 47 files' role comparisons
in a running deployment, in a way 0088 provably does not. Whether that means
holding this revision until the call-site PR that updates
``core/security.py`` and friends, or merging it in the same release as that
PR, is a merge-order decision for that PR, not a schema decision.

Notes:
  - Enum labels cannot be un-renamed without a full type rebuild, so this is
    forward-only like every other ``ALTER TYPE`` in this tree (0055).
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0089"
down_revision: str | None = "0088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE user_role RENAME VALUE 'team_admin' TO 'group_admin'")


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
