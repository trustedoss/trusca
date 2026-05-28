"""projects.git_credential_encrypted column — feature #18 Part B (private-repo scanning)

Revision ID: 0024
Revises: 0023
Create Date: 2026-05-25

Phase: feature #18 (Part B — private-repo scanning; this is the SCHEMA half only)
PR: feat/18b-git-credential-schema
Kind: schema (additive — expand step; no data migration)
Forward-only: yes

What:
  - Add one nullable column to the existing ``projects`` table::
      git_credential_encrypted  TEXT NULL

Why:
  - Feature #18 Part B lets a team scan a PRIVATE git repository. To clone it,
    TrustedOSS needs a per-project git credential (a Personal Access Token /
    deploy token today; an SSH private key later). That credential is a
    REVERSIBLE secret — unlike an API key (bcrypt-hashed, only ever verified),
    we must recover the plaintext to inject it into the clone command — so it is
    stored as a Fernet ciphertext, exactly like
    ``github_app_credentials.private_key_encrypted`` (0019). The plaintext is
    NEVER persisted; encryption / decryption lives in ``core.crypto``.
  - This revision is the SCHEMA half only. The backend service that writes /
    rotates the credential, the clone-time injection, and the project-settings
    UI land in later #18-B steps. No service / API / clone logic here.

Security / encryption-at-rest:
  - ``git_credential_encrypted`` (NULLABLE) holds Fernet ciphertext only — the
    plaintext PAT / token / key never touches the database.
  - The audit listener masks this column out of ``audit_logs.diff`` via
    ``core.audit._SENSITIVE_COLUMNS`` (defence-in-depth: a credential add /
    rotate / clear UPDATE on ``projects`` must never copy ciphertext into the
    diff). Added to that set in the same change as this revision.

Notes:
  - **Expand step only** (CLAUDE.md §6 expand → migrate-data → contract),
    matching 0015 / 0016 / 0017 / 0018. The column is NULLABLE with no server
    default: most projects are public / have no credential, so NULL is a
    legitimate, permanent value meaning "no git credential configured". There
    is no backfill and no contract step planned.
  - ``TEXT`` (no length) mirrors ``github_app_credentials.private_key_encrypted``
    — Fernet tokens are urlsafe-base64 strings of unbounded length.
  - No CHECK and no index: the column is read alongside the project row it lives
    on (project-settings / clone paths already key by ``id`` / ``team_id`` which
    are indexed) and is never a filter / sort / containment predicate. An index
    now would be speculative.
  - Pure additive DDL (ADD COLUMN NULL is metadata-only on PG 11+; no table
    rewrite, no backfill scan). No raw SQL → no asyncpg ``::`` / TIMESTAMPTZ
    bind concerns.
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("git_credential_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
