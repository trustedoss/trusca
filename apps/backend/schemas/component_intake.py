# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Wire shapes for the ask-before-using queue."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from services.component_intake_service import MIN_JUSTIFICATION_LEN


class IntakeRequestCreateIn(BaseModel):
    """Ask to use a package that nothing has scanned yet."""

    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID
    purl: str = Field(
        min_length=5,
        max_length=512,
        description=(
            "The package, as a purl: 'pkg:npm/lodash', 'pkg:pypi/requests'. "
            "A purl and not a name, because it is what a later scan will "
            "match the answer against."
        ),
    )
    justification: str = Field(
        min_length=MIN_JUSTIFICATION_LEN,
        description=(
            "Why this package. The reviewer is being asked about something "
            "that is not in the codebase yet, so this is all they have."
        ),
    )


class IntakeRequestTransitionIn(BaseModel):
    """Answer a request, or move it along."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="under_review, approved or rejected.")
    note: str | None = Field(
        default=None, description="Optional reasoning, kept either way."
    )


class IntakeRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    team_id: uuid.UUID
    purl: str
    justification: str
    status: str
    requested_by_user_id: uuid.UUID | None
    decided_by_user_id: uuid.UUID | None
    decision_note: str | None
    decided_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class IntakeRequestListOut(BaseModel):
    items: list[IntakeRequestOut]
    total: int
