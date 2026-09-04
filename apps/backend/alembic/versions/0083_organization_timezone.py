"""organizations: the timezone a written deadline is read in

Revision ID: 0083
Revises: 0082
Create Date: 2026-09-04

Kind: schema (one column)
Forward-only: yes

What was wrong
--------------
A deadline somebody types is a calendar date, and the code turned it into an
instant by taking the start of the next day in UTC. For anyone west of UTC
that instant arrives while the deadline is still today where they are: at
UTC-5 a finding due on the 7th goes overdue at 19:00 on the 7th, local. East
of UTC the error runs the other way and hands people extra hours, which
nobody reports. The half that matters is the half that takes time away.

The instant is now the start of the next day in the organization's timezone.
``UTC`` is the default, so a deployment that sets nothing keeps exactly the
behaviour it has.

Why a column rather than the ``settings`` JSONB
-----------------------------------------------
``organizations.settings`` has existed since 0002 and holds nothing; this was
the obvious first tenant for it. It is the wrong home for this particular
value, and the reason generalises:

**A setting that a query or a verdict reads gets a column. A setting that is
displayed, or is an opaque blob, goes in ``settings``.**

A misspelt JSONB key fails silently. ``settings ->> 'time_zone'`` returns
NULL, the code falls back to UTC, nothing errors, every screen looks right,
and the only symptom is that western users keep going overdue a few hours
early. That is the shape of defect this repository spent a day removing. A
misspelt column name fails where it is written: SQLAlchemy cannot find the
attribute and Postgres cannot find the column.

Validation stays in the application either way. A CHECK constraint cannot
consult ``pg_timezone_names`` (no subqueries in CHECK), so the column takes
any text; ``schemas`` rejects anything ``zoneinfo`` will not load, and the
read path treats an unloadable value as UTC rather than raising during a
sweep.

``settings`` is still empty after this migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0083"
down_revision: str | None = "0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOT NULL with a default: existing rows get UTC, which is what they were
    # already being treated as, and no read path needs a NULL branch.
    op.add_column(
        "organizations",
        sa.Column(
            "timezone",
            sa.String(64),
            nullable=False,
            server_default="UTC",
        ),
    )


def downgrade() -> None:
    # Forward-only per CLAUDE.md §6.
    raise NotImplementedError("dropping organizations.timezone is not supported")
