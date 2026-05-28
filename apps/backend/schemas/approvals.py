"""
Pydantic schemas for the Component Approval Workflow — Phase 4 PR #15.

Public shapes:
  - ApprovalOut       — single approval row returned to the caller
  - ApprovalListPage  — paginated list wrapper
  - ApprovalCreateIn  — request body for POST /v1/approvals
  - ApprovalTransitionIn — request body for PATCH /v1/approvals/{id}/transition

Design notes:
  - ApprovalOut uses ConfigDict(from_attributes=True) so SQLAlchemy ORM instances
    can be validated directly with model_validate(row).
  - ApprovalStatus is imported from the model so both layers share the same enum.
  - decision_note is bounded to 2000 chars at the schema layer; the DB column is
    unbounded TEXT, but we enforce the limit here before the row ever reaches
    Postgres.
  - Literal["under_review", "approved", "rejected"] on the transition action
    surfaces cleanly in OpenAPI and constrains the Pydantic validation to only the
    actor-trigerable transitions (pending is the server-assigned initial state, so
    callers never request it directly).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from models.component_approval import ApprovalStatus

if TYPE_CHECKING:
    from services.component_approval_service import ApprovalListRow


class ApprovalOut(BaseModel):
    """Single component-approval row, safe to serialise to the caller."""

    id: UUID
    component_id: UUID
    project_id: UUID
    team_id: UUID
    requested_by_user_id: UUID | None
    requested_at: datetime
    status: ApprovalStatus
    decided_by_user_id: UUID | None
    decided_at: datetime | None
    decision_note: str | None
    version: int
    # P1 #6 — denormalised display fields surfaced on every approval row so
    # the cross-project Approvals queue UI doesn't show raw UUIDs in the
    # Component / Project columns. All four are nullable because:
    #   * cross-domain ORM relationships are intentionally NOT declared on
    #     ComponentApproval (see model docstring) — the service layer fills
    #     these via batch IN(...) lookups,
    #   * a hardened-row whose referent was hard-deleted should still render
    #     as a known approval id rather than a 500.
    component_name: str | None = None
    component_purl: str | None = None
    project_name: str | None = None
    project_slug: str | None = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_list_row(cls, row: ApprovalListRow) -> ApprovalOut:
        """Build an ApprovalOut from a service-layer ``ApprovalListRow`` envelope.

        The envelope already carries the component / project display fields
        the list service resolved via batched IN(...) lookups; we just splice
        them onto the model-validated approval row.
        """
        pub = cls.model_validate(row.approval)
        pub.component_name = row.component_name
        pub.component_purl = row.component_purl
        pub.project_name = row.project_name
        pub.project_slug = row.project_slug
        return pub


class ApprovalListPage(BaseModel):
    """Paginated list of approval rows."""

    items: list[ApprovalOut]
    total: int
    page: int
    page_size: int


class ApprovalCreateIn(BaseModel):
    """Request body for creating a new approval request."""

    component_id: UUID
    project_id: UUID


class ApprovalTransitionIn(BaseModel):
    """
    Request body for transitioning an approval's status.

    ``action`` is limited to the three states a human reviewer can drive.
    The server assigns ``pending`` on create; it is never a valid action here.
    ``decision_note`` is optional for ``under_review`` but strongly encouraged
    for ``rejected`` (the UI should warn, not block).
    """

    action: Literal["under_review", "approved", "rejected"]
    decision_note: str | None = Field(None, max_length=2000)


__all__ = [
    "ApprovalCreateIn",
    "ApprovalListPage",
    "ApprovalOut",
    "ApprovalTransitionIn",
]
