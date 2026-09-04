"""DELIBERATELY BROKEN. Verification only, for ER66. Do not merge.

Revision ID: 0083
Revises: 0082
Create Date: 2026-09-05

This revision exists to answer one question: when a pull request breaks a
migration, does `test (backend-integration)` report red, or does it report
green because every test that needed the schema skipped?

It is shaped like the real case rather than like an arbitrary failure. It sits
at the head, so revisions 0001 through 0082 apply normally and the schema the
tests use is present; only this last step fails. An arbitrary break earlier in
the chain would leave no schema at all, tests would fail for a different
reason, and the answer would be the opposite one for the wrong cause.

The pull request carrying this file is a draft, is titled as a probe, and is
closed as soon as the checks have reported.
"""

from __future__ import annotations

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    raise RuntimeError(
        "ER66 probe: this migration fails on purpose to see what CI reports."
    )


def downgrade() -> None:
    pass
