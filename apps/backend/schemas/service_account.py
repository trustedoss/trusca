# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Wire shapes for automation identities.

Deliberately narrow. These rows live in ``users`` so the permission model and
every foreign key keep working unchanged, but nothing about a person's shape
belongs on the wire here: no password, no verification state, no superuser
flag. What a caller needs is what it is called, what it may reach, and who is
answerable for it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ServiceAccountCreateIn(BaseModel):
    """Create an automation identity inside one team."""

    model_config = ConfigDict(extra="forbid")

    team_id: uuid.UUID
    slug: str = Field(
        min_length=3,
        max_length=64,
        description=(
            "Lowercase letters, digits and hyphens. Becomes the account's "
            "identifier, so it is refused rather than reshaped when it does "
            "not fit."
        ),
    )
    display_name: str = Field(
        min_length=1,
        max_length=255,
        description="What this automation is, in words, for whoever reads the audit log.",
    )
    role: str = Field(
        default="developer",
        description=(
            "The account's role within the team, exactly as a person's would "
            "be: 'viewer', 'developer' or 'team_admin'."
        ),
    )


class ServiceAccountStewardIn(BaseModel):
    """Hand an account to somebody who will answer for it."""

    model_config = ConfigDict(extra="forbid")

    steward_user_id: uuid.UUID


class ServiceAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str = Field(
        description=(
            "The synthetic address this identity carries. Undeliverable by "
            "construction; it exists because the audit log prints it."
        )
    )
    full_name: str | None
    is_active: bool
    managed_by_user_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "The person answerable for it. Null when nobody is, in which case "
            "existing keys keep working and no new key may be issued."
        ),
    )
    created_at: datetime


class ServiceAccountListOut(BaseModel):
    items: list[ServiceAccountOut]
    total: int
