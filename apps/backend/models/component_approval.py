"""
Component Approval Workflow models — Phase 5 PR #15.

Table: component_approvals.

Conventions (CLAUDE.md core rules + db-designer agent guide):
  - PostgreSQL only. UUID PKs default to gen_random_uuid() (pgcrypto extension
    enabled in 0002_auth_schema).
  - TIMESTAMPTZ for every timestamp; created_at on insert, no updated_at (the
    row transitions through status; individual field timestamps capture history).
  - Every FK column gets an explicit Index — Postgres does not auto-create them.
  - Closed enum (ComponentApproval.status) uses a native Postgres ENUM type
    ('approval_status') created in the migration; model binds with
    create_type=False so SQLAlchemy never tries to (re)create it.
  - version column: optimistic concurrency gate for the ETag pattern
    (SELECT FOR UPDATE + if_match echo per MEMORY.md).
  - No environment access at import time (CLAUDE.md core rule #11).

Cross-domain relationships:
  - FK columns reference components.id, projects.id, teams.id (scan domain)
    and users.id (auth domain). ORM relationships are declared one-way
    (component_approval → scan, component_approval → auth) with no back-refs
    added to the upstream models, keeping the dependency graph acyclic.

Partial unique index:
  - ix_component_approvals_unique_open enforces "at most one active approval
    per (component, project)" at the DB layer (WHERE status IN
    ('pending','under_review')). Service code can rely on the unique-violation
    as the canonical "an approval is already open" signal.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")

# Closed approval status set — native Postgres ENUM so invalid values are
# rejected at the DB layer. The migration owns CREATE TYPE; we bind via name=
# with create_type=False to prevent SQLAlchemy from emitting its own DDL.
APPROVAL_STATUS_VALUES = ("pending", "under_review", "approved", "rejected")


def _approval_status_enum() -> PG_ENUM:
    return PG_ENUM(
        *APPROVAL_STATUS_VALUES,
        name="approval_status",
        create_type=False,  # the migration owns CREATE TYPE
    )


# ---------------------------------------------------------------------------
# Python-side enum (mirrors the Postgres ENUM for type-safe service code)
# ---------------------------------------------------------------------------


class ApprovalStatus(str, enum.Enum):
    """Python mirror of the ``approval_status`` Postgres ENUM.

    Using ``str`` as the mixin lets Pydantic / JSON serialisation treat the
    value as a plain string without extra coercion.
    """

    pending = "pending"
    under_review = "under_review"
    approved = "approved"
    rejected = "rejected"


# ---------------------------------------------------------------------------
# ComponentApproval
# ---------------------------------------------------------------------------


class ComponentApproval(Base):
    """
    An approval request for a component (package) within a specific project.

    Lifecycle:
      pending       → (team_admin or super_admin reviews) →
      under_review  → approved | rejected

    A component+project pair may only have one *open* (pending or under_review)
    approval at a time — enforced by ``ix_component_approvals_unique_open``.
    Once decided the row is terminal; a new request creates a fresh row.

    ETag / optimistic concurrency:
      ``version`` is incremented by the service on every state transition.
      The API reads it as an ETag header and demands the client echo it back
      via If-Match, preventing lost-update races (MEMORY.md optimistic
      concurrency pattern).

    Tenancy:
      Every row carries ``team_id`` so that multi-tenant list queries can
      always filter by the caller's team without a join.  Compound indexes
      lead with ``team_id`` per CLAUDE.md §1.2 "Tenancy" rule.
    """

    __tablename__ = "component_approvals"

    # ------------------------------------------------------------------
    # Primary key
    # ------------------------------------------------------------------
    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    # ------------------------------------------------------------------
    # Foreign keys
    # ------------------------------------------------------------------
    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("components.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ------------------------------------------------------------------
    # Request details
    # ------------------------------------------------------------------
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    # ------------------------------------------------------------------
    # Status / decision
    # ------------------------------------------------------------------
    status: Mapped[str] = mapped_column(
        _approval_status_enum(),
        nullable=False,
        server_default=text("'pending'"),
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ------------------------------------------------------------------
    # Optimistic concurrency gate (ETag)
    # ------------------------------------------------------------------
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    # ------------------------------------------------------------------
    # Timestamps
    # ------------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    # ------------------------------------------------------------------
    # ORM relationships (one-way: approval → upstream domains)
    # ------------------------------------------------------------------
    # Lazy imports via string references keep circular-import risk zero.
    component: Mapped[Component] = relationship(  # type: ignore[name-defined]  # noqa: F821
        foreign_keys=[component_id],
        lazy="raise",
    )
    project: Mapped[Project] = relationship(  # type: ignore[name-defined]  # noqa: F821
        foreign_keys=[project_id],
        lazy="raise",
    )
    requested_by_user: Mapped[User | None] = relationship(  # type: ignore[name-defined]  # noqa: F821
        foreign_keys=[requested_by_user_id],
        lazy="raise",
    )
    decided_by_user: Mapped[User | None] = relationship(  # type: ignore[name-defined]  # noqa: F821
        foreign_keys=[decided_by_user_id],
        lazy="raise",
    )

    # ------------------------------------------------------------------
    # Table-level constraints and indexes
    # ------------------------------------------------------------------
    __table_args__ = (
        # ---- FK indexes (Postgres does not auto-create them) ----
        Index("ix_component_approvals_component_id", "component_id"),
        Index("ix_component_approvals_project_id", "project_id"),
        Index("ix_component_approvals_requested_by_user_id", "requested_by_user_id"),
        Index("ix_component_approvals_decided_by_user_id", "decided_by_user_id"),
        # ---- Tenant-leading compound indexes ----
        # Hot path: approvals list page — filter by team, optionally by status.
        Index("ix_component_approvals_team_status", "team_id", "status"),
        # ---- Status + time-range — admin queue "oldest pending first" ----
        Index("ix_component_approvals_status_requested_at", "status", "requested_at"),
        # ---- Partial unique: one open approval per (component, project) ----
        # "open" means status IN ('pending','under_review'). Once resolved the
        # constraint no longer applies and a fresh request may be opened.
        # The migration creates this via raw op.execute() DDL; declaring it
        # here as well keeps alembic check clean (metadata matches DB).
        Index(
            "ix_component_approvals_unique_open",
            "component_id",
            "project_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'under_review')"),
        ),
    )
