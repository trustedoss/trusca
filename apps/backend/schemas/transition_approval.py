# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Wire shapes for a status change that needs two people.

The response carries both names because the record is the point of the feature:
an audit asking who accepted a risk gets the person who asked and the person who
agreed, from one row, without joining the audit log.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

#: The shortest justification a request may carry. Matches the bar the direct
#: VEX transitions already hold callers to, so routing through an approval is
#: not a way to record a thinner reason.
MIN_JUSTIFICATION_LEN = 10


class TransitionApprovalRequestIn(BaseModel):
    """Ask for a finding to move into a status that needs a second person."""

    finding_id: uuid.UUID
    target_status: str = Field(
        description="The status being asked for. Must be one the policy names."
    )
    justification: str = Field(
        min_length=MIN_JUSTIFICATION_LEN,
        description=(
            "Why the change is being asked for. Required, and required to be "
            "substantive: the approver has nothing else to judge."
        ),
    )


class TransitionApprovalDecisionIn(BaseModel):
    """Agree or refuse, as somebody other than the requester."""

    approve: bool
    note: str | None = Field(
        default=None,
        description="Optional reasoning. Kept whether the answer was yes or no.",
    )


class TransitionApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    finding_id: uuid.UUID
    team_id: uuid.UUID
    target_status: str
    justification: str
    requested_by_user_id: uuid.UUID | None
    state: str
    decided_by_user_id: uuid.UUID | None
    decision_note: str | None
    decided_at: datetime | None
    created_at: datetime


class TransitionApprovalListOut(BaseModel):
    """The queue. Oldest first, so the longest wait is answered first."""

    items: list[TransitionApprovalOut]
    total: int
