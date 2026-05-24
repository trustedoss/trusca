"""
Remediation pull-request model — v2.2 (Track B — b3 "opt-in automated
remediation PR creation").

Table:
  - ``remediation_pull_requests`` — one row per auto-PR the portal opens (or
    attempts to open) on a project's GitHub repository to bump its vulnerable
    npm dependencies. The row is the durable record of a WRITE to an external
    system: who triggered it, against which opted-in repo, with which bumps, and
    the resulting branch / PR coordinates + status.

Why this exists:
  b2 (``services.remediation_service``) computes the proposed ``package.json``
  edit as a side-effect-free dry-run. b3 (``services.remediation_pr_service``)
  actually opens the PR using a short-lived GitHub App installation token
  (minted by b1). Because opening a PR is a privileged write to a customer's
  repo, every attempt is persisted + audited and the operation is idempotent —
  this table is both the audit record and the idempotency ledger.

Opt-in / repo-derivation security model (b1 → b3 contract):
  - The target repository is NEVER chosen by the caller. It is derived ONLY from
    the project's opted-in ``github_app_installations`` row (the ``project_id``
    link on that table, a non-revoked credential, and a set
    ``repository_full_name``). ``installation_row_id`` here is a FK back to that
    installation row so the audit trail records exactly which opt-in authorised
    the write. ``ON DELETE SET NULL`` so unlinking the installation (or deleting
    its credential, which cascades the installation row away) does not erase the
    historical PR record — only the back-pointer is severed. ``repository_full_name``
    is snapshotted on the row so the record stays readable after an unlink.

Idempotency:
  - ``change_fingerprint`` is a stable hex digest of the sorted ``(package, to)``
    bump set. The partial unique index ``uq_remediation_prs_open_fingerprint``
    (UNIQUE on ``(project_id, change_fingerprint)`` WHERE
    ``status IN ('creating', 'open')``) enforces at most one in-flight-or-open PR
    per (project, bump-set). Covering ``creating`` makes the early INSERT a lock
    so two racing requests cannot open two real GitHub PRs: the second fails to
    insert and short-circuits. A duplicate request while a matching PR is open
    returns the existing row instead of opening a second. A ``failed`` /
    ``superseded`` row does not block a fresh attempt.

Status lifecycle (CHECK-constrained):
  - ``creating``   — the row is persisted before the GitHub writes start, so a
                     crash mid-flight leaves a visible trail (no silent partial).
  - ``open``       — GitHub returned a created PR; ``pr_number`` / ``pr_url`` set.
  - ``failed``     — a GitHub write failed; the row records the attempt.
  - ``superseded`` — reserved for a future "a newer PR replaces this one" flow.

Conventions (CLAUDE.md core rules + existing model files — mirrors
``models/github_app.py`` and ``models/license_policy.py``):
  - PostgreSQL only. UUID PKs default to ``gen_random_uuid()`` (pgcrypto).
  - TIMESTAMPTZ for ``created_at`` / ``updated_at`` with ``now()`` server default.
  - Every FK column gets an explicit Index — Postgres does not auto-create them.
  - JSONB server default via ``text("'[]'::jsonb")``.
  - Closed status set encoded as a CHECK-constrained VARCHAR (mirrors
    ``api_keys.scope``) rather than a native ENUM.
  - No environment access at import time (CLAUDE.md core rule #11).
  - Cross-domain relationships are one-way (remediation_pr → scan / github_app /
    auth). No ORM back-refs into those modules.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

# ---------------------------------------------------------------------------
# Helpers (mirror models/github_app.py + models/license_policy.py)
# ---------------------------------------------------------------------------

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")
EMPTY_JSONB_ARR = text("'[]'::jsonb")

# The closed status set the CHECK constraint and the service agree on.
REMEDIATION_PR_STATUS_VALUES = ("creating", "open", "failed", "superseded")


# ---------------------------------------------------------------------------
# RemediationPullRequest
# ---------------------------------------------------------------------------


class RemediationPullRequest(Base):
    """A single auto-remediation PR attempt against a project's GitHub repo.

    See the module docstring for the opt-in / repo-derivation security model,
    the idempotency contract, and the status lifecycle.
    """

    __tablename__ = "remediation_pull_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    # The project this PR remediates. CASCADE so a project's PR records go away
    # with the project.
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The opted-in installation row that authorised this write. SET NULL so an
    # unlink / credential-delete keeps the historical record (only the
    # back-pointer is severed). NULLable for the same reason.
    installation_row_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("github_app_installations.id", ondelete="SET NULL"),
        nullable=True,
    )

    # "npm" today; the column is here so pip/maven adapters reuse the table.
    ecosystem: Mapped[str] = mapped_column(String(32), nullable=False)

    # "owner/repo" snapshot at creation time — readable even after unlink/rename.
    repository_full_name: Mapped[str] = mapped_column(String(512), nullable=False)

    # The branch the portal created (trustedoss/remediation-<short-fingerprint>).
    head_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    # The repo default branch the PR targets.
    base_branch: Mapped[str] = mapped_column(String(255), nullable=False)

    # Populated once GitHub returns the created PR; NULL while creating/failed.
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # creating | open | failed | superseded (see module docstring).
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'creating'")
    )

    # JSONB array of {"package","from","to"} — the applied bump set, for audit.
    package_changes: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSONB_ARR
    )

    # Stable hex digest of the sorted (package, to) set — the idempotency key.
    change_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)

    # Author — SET NULL on user delete keeps the record (and its audit trail).
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW,
        onupdate=NOW,
    )

    __table_args__ = (
        # FK lookup indexes (Postgres does not auto-create them).
        Index("ix_remediation_prs_project_id", "project_id"),
        Index("ix_remediation_prs_installation_row_id", "installation_row_id"),
        Index("ix_remediation_prs_created_by_user_id", "created_by_user_id"),
        # Idempotency gate: at most one in-flight-or-open PR per (project,
        # bump-set). Covering 'creating' makes the early INSERT a lock so two
        # racing requests cannot open two real GitHub PRs. A failed/superseded
        # row does not block a fresh attempt.
        Index(
            "uq_remediation_prs_open_fingerprint",
            "project_id",
            "change_fingerprint",
            unique=True,
            postgresql_where=text("status IN ('creating', 'open')"),
        ),
        # Closed status set backstop at the DB layer.
        CheckConstraint(
            "status IN ('creating', 'open', 'failed', 'superseded')",
            name="ck_remediation_prs_status_values",
        ),
    )


__all__ = [
    "REMEDIATION_PR_STATUS_VALUES",
    "RemediationPullRequest",
]
