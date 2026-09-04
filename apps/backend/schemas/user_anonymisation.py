# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Wire shapes for user anonymisation requests (ER32).

None of these carry the subject's email, name, or any other attribute of the
person being erased. They carry ids. A response body that helpfully echoed
"anonymise alice@example.com" would put that address into API logs, browser
history and any client that caches responses, on the one request whose entire
purpose is to remove it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AnonymisationRequestIn(BaseModel):
    """Open a request against one user."""

    subject_user_id: uuid.UUID
    reason: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "Why the erasure was asked for, for the operator who will run it "
            "and for whoever reviews the decision later. Free text, so do not "
            "put the subject's contact details in it."
        ),
    )


class AnonymisationRequestOut(BaseModel):
    """One request, as stored."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject_user_id: uuid.UUID
    requested_by_user_id: uuid.UUID
    approved_by_user_id: uuid.UUID | None
    state: str
    reason: str | None
    created_at: datetime
    expires_at: datetime
    approved_at: datetime | None
    executed_at: datetime | None


class AwaitingExecutionOut(BaseModel):
    """One approved request nobody has run yet.

    ``waiting_days`` is computed rather than left to the client. The number is
    the point of the screen, and a client that derived it from
    ``approved_at`` in the browser's timezone would disagree with the server
    about when a day boundary fell.
    """

    request_id: uuid.UUID
    subject_user_id: uuid.UUID
    #: Who asked and who agreed. An operator is about to do something
    #: irreversible on the strength of this row, and a row is only a row; these
    #: are the two people they can go and ask.
    requested_by_user_id: uuid.UUID
    approved_by_user_id: uuid.UUID | None
    approved_at: datetime
    waiting_days: int


class AwaitingExecutionListOut(BaseModel):
    """The backlog, oldest first, with its own size.

    ``count`` is here so a caller that pages, or that renders only a badge,
    does not have to fetch the list to learn it is empty.
    """

    items: list[AwaitingExecutionOut]
    count: int


__all__ = [
    "AnonymisationRequestIn",
    "AnonymisationRequestOut",
    "AwaitingExecutionListOut",
    "AwaitingExecutionOut",
]
