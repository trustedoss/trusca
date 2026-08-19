# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Wire shapes for notification routing rules (N9)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from models.notification import NOTIFICATION_KIND_VALUES
from models.scan import VULN_SEVERITY_VALUES


class NotificationRoutingRuleIn(BaseModel):
    """One rule an organization or team is writing.

    Every condition is optional and an absent one matches everything, which is
    what makes a rule with no conditions the "tell us about all of it" that an
    organization writes first.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    kinds: list[str] = Field(
        default_factory=list,
        description=(
            "Notification kinds this rule covers. Empty means every kind."
        ),
    )
    min_severity: str | None = Field(
        default=None,
        description=(
            "Matches this severity and everything above it. Omitted means "
            "severity is not part of the condition. A rule naming one does "
            "not fire for a notification that carries no severity at all."
        ),
    )
    project_id: uuid.UUID | None = Field(
        default=None, description="One project. Omitted means every project in scope."
    )
    channels: list[str] = Field(
        default_factory=list,
        description="Channels this rule adds. Never removes one somebody enabled.",
    )
    email_recipients: list[EmailStr] = Field(
        default_factory=list, max_length=50, description="Addresses this rule adds."
    )
    is_active: bool = True

    @field_validator("kinds")
    @classmethod
    def _known_kinds(cls, value: list[str]) -> list[str]:
        """A kind the portal never emits is a rule that will never fire.

        Refused at write time rather than stored: an operator who mistypes one
        would otherwise wait for an alert that cannot arrive, and nothing
        anywhere would say why.
        """
        unknown = sorted(set(value) - set(NOTIFICATION_KIND_VALUES))
        if unknown:
            raise ValueError(
                "unknown notification kinds: "
                + ", ".join(unknown)
                + "; known kinds are "
                + ", ".join(sorted(NOTIFICATION_KIND_VALUES))
            )
        return value

    @field_validator("min_severity")
    @classmethod
    def _known_severity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        folded = value.strip().lower()
        if folded not in VULN_SEVERITY_VALUES:
            raise ValueError(
                "unknown severity: "
                + value
                + "; known severities are "
                + ", ".join(VULN_SEVERITY_VALUES)
            )
        return folded

    @field_validator("channels")
    @classmethod
    def _known_channels(cls, value: list[str]) -> list[str]:
        from notifications.dispatcher import _KNOWN_CHANNELS

        unknown = sorted(set(value) - set(_KNOWN_CHANNELS))
        if unknown:
            raise ValueError(
                "unknown channels: "
                + ", ".join(unknown)
                + "; known channels are "
                + ", ".join(sorted(_KNOWN_CHANNELS))
            )
        return value


class NotificationRoutingRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    team_id: uuid.UUID | None
    name: str
    kinds: list[str]
    min_severity: str | None
    project_id: uuid.UUID | None
    channels: list[str]
    email_recipients: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class NotificationRoutingRuleListOut(BaseModel):
    items: list[NotificationRoutingRuleOut]
    total: int
