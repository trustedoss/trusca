# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Build-gate policy: what blocks a build, as rows rather than as deployment
configuration.

The gate has always been able to bend, but only through environment variables,
which means a change needs someone with access to the deployment and leaves no
record of who decided it. That is the wrong shape for a rule an organisation
sets: security policy belongs to the organisation, is agreed by people who do
not deploy, and is exactly the kind of change an audit later asks about.

Scoping mirrors ``license_policies`` deliberately: one org-default row with
``team_id IS NULL``, and one optional row per team that overrides it. The two
tables stay separate because a name that covered both would be a lie about one
of them; the licence axis and the vulnerability axis answer different questions
even though they end up in the same verdict.

The severity that blocks is deliberately absent. Raising it above critical
means changing what ``critical_cve_count`` counts, and that number is on the
wire and in the CI output, so the name and the meaning have to move together.
That is its own decision rather than a column added quietly here.

Every column is nullable, and that is the contract. NULL means "not decided
here", so resolution falls through team, then org, then the environment
variable, then the built-in default. A deployment that writes no rows behaves
exactly as it did before this table existed, which is what makes the table
safe to add ahead of any UI for it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")


class GatePolicy(Base):
    """One build-gate policy for an organization, or for a team within it."""

    __tablename__ = "gate_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    # NULL → the organization default; non-NULL → that team's override.
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=True,
    )

    name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Block when a finding's exploit-prediction score reaches this. NULL leaves
    # the condition off, which is the behaviour with no policy at all.
    epss_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Count only criticals an analyser proved reachable. This can only shrink
    # the blocking set, never widen it, so it is a relaxation an organization
    # opts into.
    reachable_critical_only: Mapped[bool | None] = mapped_column(nullable=True)

    # Whether a package the malicious snapshot flags blocks the build. Unlike
    # the two above this defaults ON in the environment, and a policy row may
    # only turn it off deliberately.
    malicious_blocks: Mapped[bool | None] = mapped_column(nullable=True)

    # Finding statuses one person may not reach alone, as a JSON array. NULL
    # means none, which is how the product behaved before this existed.
    #
    # The list is not fixed here on purpose. Closing a finding as suppressed
    # ends the obligation without a fix, and so does not-affected, but whether
    # either counts as accepting risk is a judgement an organization makes
    # about its own work rather than one the product can make for it.
    approval_required_statuses: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True
    )

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "team_id", name="uq_gate_policies_org_team"),
        # Postgres treats NULLs as distinct, so the constraint above does not
        # stop two org-default rows. This does, and the pair is the same
        # arrangement license_policies uses.
        Index(
            "uq_gate_policies_org_default",
            "organization_id",
            unique=True,
            postgresql_where=text("team_id IS NULL"),
        ),
        Index("ix_gate_policies_team_id", "team_id"),
        CheckConstraint(
            "epss_threshold IS NULL OR (epss_threshold >= 0 AND epss_threshold <= 1)",
            name="ck_gate_policies_epss_range",
        ),
    )
