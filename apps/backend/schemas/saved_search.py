# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Saved-search wire shapes (S3)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SavedSearchCreate(BaseModel):
    """Request body for saving a search."""

    name: str = Field(min_length=1, max_length=60)
    kind: str = Field(
        description="Which search tab the params belong to: projects, "
        "components, vulnerabilities, or licenses."
    )
    params: dict[str, object] = Field(
        default_factory=dict,
        description=(
            "The saved query string, replayed verbatim on open. Opaque to the "
            "server — it is whatever filters the page carried at save time."
        ),
    )


class SavedSearchPublic(BaseModel):
    """One saved search."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: str
    params: dict[str, object]
    created_at: datetime


class SavedSearchListResponse(BaseModel):
    """All of the caller's saved searches.

    Unpaged on purpose: the per-user cap is small enough that the dashboard
    panel renders the whole list, and paging it would add a control nobody
    would ever use.
    """

    items: list[SavedSearchPublic] = Field(default_factory=list)
    total: int = 0
    #: The per-user ceiling, so the UI can disable "save" before the request
    #: fails rather than after.
    limit: int = 20


__all__ = [
    "SavedSearchCreate",
    "SavedSearchListResponse",
    "SavedSearchPublic",
]
