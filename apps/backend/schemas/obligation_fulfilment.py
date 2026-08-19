# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Wire shapes for obligation fulfilment records."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from models.obligation_fulfilment import OBLIGATION_FULFILMENT_STATUSES


class ObligationFulfilmentIn(BaseModel):
    """What the state of this obligation is now, for this project."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(
        description=(
            "One of: " + ", ".join(OBLIGATION_FULFILMENT_STATUSES) + ". "
            "'not_applicable' is a real answer rather than a way of hiding "
            "one: an obligation that binds a shipped binary need not bind an "
            "internal service, and saying so is more useful than an untouched "
            "row, which reads as nobody having looked."
        )
    )
    assignee_user_id: uuid.UUID | None = Field(
        default=None,
        description="Who is doing it. Must be an active person on the project's team.",
    )
    due_on: date | None = Field(default=None, description="When it is needed by.")
    evidence_note: str | None = Field(
        default=None,
        max_length=4000,
        description="What was done, in the words of whoever did it.",
    )
    evidence_url: str | None = Field(
        default=None,
        max_length=2048,
        description=(
            "Where to look: a release page, a file in the repository, a "
            "ticket. A link rather than an upload, so the portal does not "
            "become a second place the evidence can be wrong."
        ),
    )


class ObligationFulfilmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    obligation_id: uuid.UUID
    status: str
    assignee_user_id: uuid.UUID | None
    due_on: date | None
    evidence_note: str | None
    evidence_url: str | None
    completed_at: datetime | None
    completed_by_user_id: uuid.UUID | None
    version: int
    created_at: datetime
    updated_at: datetime


class ObligationFulfilmentListOut(BaseModel):
    items: list[ObligationFulfilmentOut]
    total: int


class ObligationFulfilmentSummary(BaseModel):
    """The fulfilment attached to an obligation in the list and detail reads.

    A narrower shape than the record itself: those responses are about the
    obligation, and everything here is there to answer "has anybody done this"
    at a glance without turning an obligation list into a task list.
    """

    id: uuid.UUID
    status: str
    assignee_user_id: uuid.UUID | None = None
    due_on: date | None = None
    evidence_note: str | None = None
    evidence_url: str | None = None
    completed_at: datetime | None = None
    completed_by_user_id: uuid.UUID | None = None
    version: int
    updated_at: datetime
