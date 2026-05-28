"""remediation pull requests — remediation_pull_requests (auto-PR tracking, v2.2 2.2-b3)

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-25

Phase: v2.2 (Track B — b3 "opt-in automated remediation PR creation")
PR: feat/v2.2-b3-auto-pr
Kind: schema (additive — new table; no data migration)
Forward-only: yes

What:
  - Create a new ``remediation_pull_requests`` table recording every auto-PR the
    portal opens (or attempts to open) on a project's GitHub repository to bump
    its vulnerable npm dependencies. One row per attempt; the row carries the
    bump set (for audit), a stable fingerprint of that set (for idempotency),
    the branch / PR coordinates, and a status the service transitions
    (``creating`` → ``open`` / ``failed`` / ``superseded``).

Why:
  - b3 is the capstone of the manifest-remediation track: b2 computed the
    proposed ``package.json`` edit (dry-run, no side effects); b3 actually opens
    the PR using a GitHub App installation token. Opening a PR is a WRITE to an
    external system, so it MUST be persisted + audited (who, when, what bumps,
    against which repo) and it MUST be idempotent (a second identical request,
    or a retry after a transient failure, must not open a duplicate PR while the
    first is still open).

Security / opt-in model (the b1 → b3 contract):
  - The target repository is NEVER chosen by the caller. It is derived ONLY from
    the project's opted-in ``github_app_installations`` row (``project_id`` link,
    non-revoked credential, ``repository_full_name`` set). ``installation_row_id``
    here is a FK back to that installation row so the audit trail records exactly
    which opt-in authorised the write. ``ON DELETE SET NULL`` so unlinking the
    installation (or deleting its credential, which cascades) does not erase the
    historical PR record — only the back-pointer is severed.

Column shapes:
  - ``project_id``            FK → projects.id, CASCADE (a project's PR records go
                              away with the project).
  - ``installation_row_id``   FK → github_app_installations.id, SET NULL (see
                              above). NULLable.
  - ``ecosystem``             "npm" today (the column is here so pip/maven reuse
                              the table later).
  - ``repository_full_name``  "owner/repo" snapshot at creation time (so the
                              record is readable even after the installation is
                              unlinked / the repo renamed on GitHub).
  - ``head_branch``           the branch the portal created
                              (``trustedoss/remediation-<short-fingerprint>``).
  - ``base_branch``           the repo's default branch the PR targets.
  - ``pr_number`` / ``pr_url``  populated once GitHub returns the created PR;
                              NULL while ``status = 'creating'`` or on failure.
  - ``status``                CHECK-constrained
                              ``creating | open | failed | superseded``.
  - ``package_changes``       JSONB array of ``{"package","from","to"}`` — the
                              applied bump set, for audit + human review.
  - ``change_fingerprint``    stable hex digest of the sorted ``(package,to)``
                              set — the idempotency key.
  - ``created_by_user_id``    FK → users.id, SET NULL (keep the record when the
                              author is removed).

Idempotency:
  - Partial unique index ``uq_remediation_prs_open_fingerprint`` on
    ``(project_id, change_fingerprint) WHERE status IN ('creating', 'open')`` —
    at most one in-flight-or-open PR per (project, bump-set). Covering
    ``creating`` makes the early INSERT a lock so two racing requests cannot open
    two real GitHub PRs. A ``failed`` / ``superseded`` row does not block a fresh
    attempt; a duplicate request while a matching PR is open returns the existing
    row instead of opening a second.

Notes:
  - **Expand step only** (CLAUDE.md §6) — pure additive CREATE TABLE + indexes,
    matching 0017/0018/0020. No data migration.
  - JSONB server default emitted as an inline DDL literal
    (``server_default=sa.text("'[]'::jsonb")``) inside ``op.create_table`` so the
    asyncpg ``::`` bind pitfall never applies.
  - The closed status set is a CHECK-constrained VARCHAR (mirrors
    ``api_keys.scope`` / ``license_policies.unknown_license_category``).
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "remediation_pull_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The opted-in installation that authorised this write. SET NULL so an
        # unlink / credential-delete does not erase the historical record.
        sa.Column(
            "installation_row_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("github_app_installations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ecosystem", sa.String(length=32), nullable=False),
        # "owner/repo" snapshot — kept even after the installation is unlinked.
        sa.Column("repository_full_name", sa.String(length=512), nullable=False),
        sa.Column("head_branch", sa.String(length=255), nullable=False),
        sa.Column("base_branch", sa.String(length=255), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'creating'"),
            nullable=False,
        ),
        sa.Column(
            "package_changes",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("change_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('creating', 'open', 'failed', 'superseded')",
            name="ck_remediation_prs_status_values",
        ),
    )

    # Hot path: "list this project's remediation PRs (newest first)".
    op.create_index(
        "ix_remediation_prs_project_id",
        "remediation_pull_requests",
        ["project_id"],
    )
    op.create_index(
        "ix_remediation_prs_installation_row_id",
        "remediation_pull_requests",
        ["installation_row_id"],
    )
    op.create_index(
        "ix_remediation_prs_created_by_user_id",
        "remediation_pull_requests",
        ["created_by_user_id"],
    )
    # Idempotency gate: at most one IN-FLIGHT-or-OPEN PR per (project, bump-set
    # fingerprint). Covering BOTH 'creating' and 'open' makes the early
    # 'creating' INSERT a lock: a concurrent second request for the same bump set
    # fails to insert and short-circuits BEFORE doing any GitHub work, so two
    # racing requests cannot open two real GitHub PRs. A 'failed' / 'superseded'
    # row does not block a fresh attempt (the lock is released on failure).
    op.create_index(
        "uq_remediation_prs_open_fingerprint",
        "remediation_pull_requests",
        ["project_id", "change_fingerprint"],
        unique=True,
        postgresql_where=sa.text("status IN ('creating', 'open')"),
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
