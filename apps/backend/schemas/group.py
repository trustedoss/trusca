# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Group read-API schemas (group-hierarchy Phase 4 PR 4-A).

Pydantic v2. Every ORM-derived shape uses ``model_config =
ConfigDict(from_attributes=True)``.

``GroupBreadcrumbEntry`` is deliberately narrow (id/name/slug only) and is
shared by two call sites that must never widen it into the fuller group
shape:

  - :attr:`GroupDetail.ancestors`: a group's own chain of ancestors.
  - ``schemas.scan.ProjectPublic.group_path``: a project's owning group's
    chain, root to leaf (see ``services.project_list_enrichment``).

Reusing ONE narrow model for both, rather than each call site building its
own ad-hoc dict, is what makes "no member/project/stat field can leak into a
breadcrumb" a property the type checker (and Pydantic's own field set)
enforces rather than something a reviewer has to eyeball at each call site.
Nothing else in this module may add a field to it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Breadcrumb: the narrow, shared shape (see module docstring)
# ---------------------------------------------------------------------------


class GroupBreadcrumbEntry(BaseModel):
    """One ancestor (or self) hop in a group's path. id/name/slug ONLY.

    Security-review note: this model's field set is the enforcement
    mechanism for "breadcrumb responses do not leak member/project/stat
    data", do not add fields here without re-reading the module docstring.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class GroupListItem(BaseModel):
    """Row in ``GET /v1/groups``: either a flat search hit or one child in
    the drill-down view, per the endpoint's ``q`` / ``parent_id`` modes."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None = None
    parent_group_id: uuid.UUID | None = Field(
        default=None,
        description="Null for a root group.",
    )
    child_group_count: int = Field(
        default=0,
        ge=0,
        description="Direct child groups (not the whole subtree).",
    )
    project_count: int = Field(
        default=0,
        ge=0,
        description="Projects owned directly by this group (not the whole subtree).",
    )
    member_count: int = Field(
        default=0,
        ge=0,
        description="Direct memberships on this group (not inherited).",
    )
    updated_at: datetime


class GroupListPage(BaseModel):
    """Page envelope for ``GET /v1/groups``."""

    items: list[GroupListItem]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


class GroupSummaryStats(BaseModel):
    """30-day activity summary for a group, ALWAYS covering its full subtree.

    Every count here is a subtree aggregate, not a "this group only" count,
    the field names say so explicitly because the two read very differently
    (a leaf group's own count vs. its whole branch's). See
    ``services.group_directory_service`` for the aggregation queries and the
    two pitfalls each one specifically avoids (a project-less ``Scan`` join
    that must reach the whole subtree, not just this group's own projects;
    and ``audit_logs.group_id`` under-counting component-approval decisions
    because that write path never calls ``bind_audit_team``).
    """

    window_days: int = Field(default=30, description="Length of the trailing window.")
    subtree_scan_count: int = Field(
        ge=0,
        description="Scans started, across every project in this group's subtree, in the window.",
    )
    subtree_approvals_processed_count: int = Field(
        ge=0,
        description=(
            "Component-approval decisions (approved or rejected) recorded, across "
            "every group in this group's subtree, in the window."
        ),
    )
    subtree_new_member_count: int = Field(
        ge=0,
        description=(
            "New direct memberships created, across every group in this group's "
            "subtree, in the window."
        ),
    )


class GroupDetail(BaseModel):
    """``GET /v1/groups/{group_id}`` response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None = None
    parent_group_id: uuid.UUID | None = None
    ancestors: list[GroupBreadcrumbEntry] = Field(
        default_factory=list,
        description="Root-first chain of ancestors, NOT including this group itself.",
    )
    child_group_count: int = Field(default=0, ge=0, description="Direct child groups.")
    project_count: int = Field(default=0, ge=0, description="Projects owned directly.")
    member_count: int = Field(default=0, ge=0, description="Direct memberships.")
    stats: GroupSummaryStats
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


class GroupMemberEntry(BaseModel):
    """A direct membership row on the group being read."""

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    email: str
    full_name: str | None = None
    role: str
    is_service_account: bool = False


class GroupInheritedMemberEntry(BaseModel):
    """A membership the group's cascade grants access through, from an ancestor.

    Only ever non-empty when :func:`core.config.group_cascade_enabled` is on:
    with the cascade off an ancestor's membership confers no access to a
    descendant group at all, so listing one here would claim a reach the
    person does not actually have. ``role`` is the role that membership row
    itself carries (nearest-ancestor-wins already applied, see
    ``services.group_directory_service._inherited_members``), not
    necessarily the group's own vocabulary.
    """

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    email: str
    full_name: str | None = None
    role: str
    is_service_account: bool = False
    source_group_id: uuid.UUID = Field(
        description="The ancestor group this membership actually lives on."
    )
    source_group_name: str = Field(
        description="Display name of source_group_id, for the UI to attribute the row."
    )


class GroupMembersResponse(BaseModel):
    """``GET /v1/groups/{group_id}/members`` response: direct vs inherited."""

    direct: list[GroupMemberEntry] = Field(default_factory=list)
    inherited: list[GroupInheritedMemberEntry] = Field(default_factory=list)


__all__ = [
    "GroupBreadcrumbEntry",
    "GroupDetail",
    "GroupInheritedMemberEntry",
    "GroupListItem",
    "GroupListPage",
    "GroupMemberEntry",
    "GroupMembersResponse",
    "GroupSummaryStats",
]
