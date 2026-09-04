"""vulnerability_findings: owner, deadline and ticket

Revision ID: 0081
Revises: 0080
Create Date: 2026-09-04

Kind: schema (four nullable columns + two partial indexes; no data migration)
Forward-only: yes

What:
  - Add to ``vulnerability_findings``::
        assignee_user_id  UUID REFERENCES users ON DELETE SET NULL
        due_on            DATE
        ticket_url        VARCHAR(2048)
        ticket_key        VARCHAR(255)
  - Partial index on ``assignee_user_id`` WHERE NOT NULL.
  - Partial index on ``due_on`` WHERE NOT NULL AND the status is still open.
  - No GRANT block: the table already exists and ``trustedoss_app`` already
    holds DML on it, and column privileges are not separately granted here.

Why:
  - A finding could be triaged but not OWNED. Without somewhere to put "who,
    by when, tracked where", the list is a report rather than a work queue,
    which is the first thing asked of it.
  - ``assignee_user_id`` is deliberately spelled exactly like
    ``obligation_fulfilments.assignee_user_id``: same ``users`` reference, the
    same ``ON DELETE SET NULL``, the same partial index. Two spellings of "who
    owns this" drift, and whatever user-lifecycle work does to one table has to
    do the same to the other. ``analyst_user_id`` on this same table already
    uses that ondelete, so the table stays internally consistent too.
  - ``SET NULL`` rather than blocking the delete: a person has to be removable.
    The cost is that deleting a user silently unassigns their findings, so the
    read path reports whether an assignee can still act rather than letting an
    unowned finding fall out of view.
  - ``due_on`` is a DATE, not a timestamp. A remediation deadline is a calendar
    commitment, and the SLA window it competes with is counted in days. The
    rule for which one governs is in ``services.due_date``.
  - Both indexes are partial because most findings are unassigned and undated,
    the same reason ``ix_vuln_findings_reachable`` is partial.

The closed-status list in the due index predicate:
  It duplicates ``services.policy_gate._CLOSED_FINDING_STATUSES`` into DDL,
  which cannot import it. A partial index whose predicate no longer matches the
  query is not an error: Postgres simply stops using it, and the only symptom
  is a query that got slower. ``test_finding_due_index_matches_closed_statuses``
  compares the two as sets so editing the Python tuple alone fails the build.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0081"
down_revision: str | None = "0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The closed statuses this migration's due index excludes, named here so a
#: test can compare them without parsing SQL.
#:
#: The predicate below repeats them as a literal rather than being built from
#: this tuple, which looks like duplication and is deliberate. Every other
#: partial index in this directory passes ``sa.text()`` a literal, and building
#: the string from a variable trips the ``avoid-sqlalchemy-text`` rule in the
#: SAST gate: the values here are hardcoded, but the rule cannot see that and
#: suppressing it would train the next reader to suppress it somewhere it
#: matters.
#:
#: The two copies cannot drift apart unnoticed, because they are pinned
#: SEPARATELY to the same third thing rather than to each other.
#: ``test_finding_due_index_contract`` compares this tuple with
#: ``services.policy_gate._CLOSED_FINDING_STATUSES``, and compares the LIVE
#: index predicate read back from ``pg_indexes`` with the same tuple. Editing
#: either copy alone fails one of those.
CLOSED_STATUSES_IN_DUE_INDEX = ("not_affected", "fixed", "false_positive")


def upgrade() -> None:
    op.add_column(
        "vulnerability_findings",
        sa.Column(
            "assignee_user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "vulnerability_findings", sa.Column("due_on", sa.Date(), nullable=True)
    )
    op.add_column(
        "vulnerability_findings",
        sa.Column("ticket_url", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "vulnerability_findings",
        sa.Column("ticket_key", sa.String(length=255), nullable=True),
    )

    # "What is assigned to me": most rows are unassigned, so the partial index
    # stays small while still serving the filter.
    op.create_index(
        "ix_vuln_findings_assignee",
        "vulnerability_findings",
        ["assignee_user_id"],
        postgresql_where=sa.text("assignee_user_id IS NOT NULL"),
    )
    # Dated and still open. A finding that is fixed, not affected or a false
    # positive has no deadline left to miss. `suppressed` is deliberately NOT
    # in that set, matching the gate and the SLA sweep, so a suppressed finding
    # keeps its deadline.
    op.create_index(
        "ix_vuln_findings_due",
        "vulnerability_findings",
        ["due_on"],
        postgresql_where=sa.text(
            "due_on IS NOT NULL AND status <> 'not_affected' "
            "AND status <> 'fixed' AND status <> 'false_positive'"
        ),
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md migration policy).
    raise NotImplementedError("downgrade is not supported")
