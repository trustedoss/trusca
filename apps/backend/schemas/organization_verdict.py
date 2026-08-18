# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Wire shapes for an organization's ruling on a component.

The effective shape carries ``scope`` alongside the status because a status
without it invites the wrong edit: somebody who reads "rejected" and assumes
their team decided it goes looking for a project row that is not there.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from services.organization_verdict_service import (
    DEFAULT_PAGE_SIZE,
    MIN_JUSTIFICATION_LEN,
)


class OrganizationVerdictOpenIn(BaseModel):
    """Start a ruling on one component for the whole organization."""

    component_id: uuid.UUID
    justification: str = Field(
        min_length=MIN_JUSTIFICATION_LEN,
        description=(
            "Why this is being ruled on centrally. Required: this answer "
            "reaches every project that has not decided for itself, and it is "
            "the sentence people will ask about later."
        ),
    )


class OrganizationVerdictTransitionIn(BaseModel):
    """Move a ruling along the same four states the per-project reviews use."""

    status: str = Field(description="under_review, approved or rejected.")
    note: str | None = Field(
        default=None, description="Optional reasoning, kept whichever way it went."
    )


class OrganizationVerdictOut(BaseModel):
    """A ruling as anybody in the organization may read it.

    ``justification`` is here because it is the published reason: somebody
    whose project inherited this answer has to be able to see why. The
    deliberation around it is not. ``decision_note`` is what an administrator
    wrote while deciding, and the names of the people involved are not needed
    to explain a status, so both are held back and returned only to callers who
    could have written them. The effective endpoint had this split from the
    start; the list is brought into line with it here.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    component_id: uuid.UUID
    status: str
    justification: str
    decided_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class OrganizationVerdictAdminOut(OrganizationVerdictOut):
    """The same ruling with the deliberation, for whoever may decide it."""

    requested_by_user_id: uuid.UUID | None = None
    decided_by_user_id: uuid.UUID | None = None
    decision_note: str | None = None


class OrganizationVerdictListOut(BaseModel):
    """One page of rulings, newest first, as the organization may read them."""

    items: list[OrganizationVerdictOut]
    total: int
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE


class OrganizationVerdictAdminListOut(OrganizationVerdictListOut):
    """The same page with the deliberation, for whoever may decide.

    A separate model rather than a subclass in the item list, because Pydantic
    serialises items by their declared type: putting admin rows into a list
    declared as the narrow shape drops exactly the fields the admin needed,
    and it does so silently.
    """

    items: list[OrganizationVerdictAdminOut]  # type: ignore[assignment]


class EffectiveVerdictOut(BaseModel):
    """What one project is actually judged by for one component."""

    project_id: uuid.UUID
    component_id: uuid.UUID
    status: str | None = Field(
        default=None, description="Null when nobody has decided at either scope."
    )
    scope: str = Field(
        description=(
            "'project' when the project decided for itself, 'organization' "
            "when it inherited, 'none' when neither has an answer."
        )
    )
    justification: str | None = Field(
        default=None,
        description="The organization's reason, when the answer came from there.",
    )
