# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Who else hears about a notification (N9).

Every notification today goes to whoever the producer named, filtered by that
person's own channel toggles. That answers "tell me about my things" and has
no answer for "the security team hears about every critical finding", which is
the rule an organization actually writes down somewhere and then implements by
forwarding mail.

A rule adds recipients and channels; it never removes them. Two mechanisms
deciding the same question would mean the one that silences wins an argument
nobody had, and a person who stops receiving their own notifications because
somebody wrote an organization rule has no way to find out why.

Scope follows the licence and gate policies: a row with no team belongs to the
organization and covers every team in it, a row with a team covers that team.
Unlike those two, rules do not fall through and do not override each other.
Every rule whose condition matches contributes, because each one is somebody
saying "also tell us", and the union is the only reading of that which does
not silently drop one of them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")


class NotificationRoutingRule(Base):
    """One "also tell these people about that" line."""

    __tablename__ = "notification_routing_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=GEN_UUID
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: None means the whole organization.
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK, ForeignKey("teams.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Empty means every kind. JSONB rather than an enum array so adding a
    #: notification kind needs no migration here; a contract test holds this
    #: vocabulary against the dispatcher's instead.
    kinds: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    #: None means severity is not part of the condition. Set, it matches that
    #: severity and everything above it.
    min_severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: None means every project in scope.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    channels: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    email_recipients: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    __table_args__ = (
        CheckConstraint(
            "min_severity IS NULL OR min_severity IN "
            "('critical', 'high', 'medium', 'low', 'info', 'unknown')",
            name="ck_notification_routing_rules_min_severity",
        ),
        CheckConstraint(
            "jsonb_array_length(channels) > 0 OR jsonb_array_length(email_recipients) > 0",
            name="ck_notification_routing_rules_says_something",
        ),
        Index(
            "ix_notification_routing_rules_scope",
            "organization_id",
            "team_id",
            "is_active",
        ),
    )
