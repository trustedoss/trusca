# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Admin API request/response schemas — Phase 4 PR #13.

Pydantic v2. Schemas split into Users / Teams. Every shape that comes off the
ORM uses ``model_config = ConfigDict(from_attributes=True)`` so the router
can pass model instances directly into ``model_validate``.

Adversarial input notes:
  - Team ``slug`` is constrained to ``[a-z0-9][a-z0-9-]*`` to match the DB
    column shape and reject control chars / unicode RTL / null bytes /
    SQL keywords by construction.
  - Team ``name`` allows broader unicode but caps at 255 (the DB column
    width). Whitespace-only names are rejected after strip.
  - Search strings are bounded at 255; unbounded ``ILIKE`` arguments are
    a DoS vector.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from schemas.auth import _reject_weak_password

# ---------------------------------------------------------------------------
# Common
# ---------------------------------------------------------------------------

# Closed role set — must match the user_role ENUM created in 0002_auth_schema.
_ROLE_VALUES = ("super_admin", "team_admin", "developer", "viewer")
_TEAM_ROLE_VALUES = ("team_admin", "developer", "viewer")
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _strip_or_raise(value: str, *, field: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field} must not be blank")
    return stripped


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class TeamMembershipPublic(BaseModel):
    """Team-membership info embedded in admin user/team responses."""

    model_config = ConfigDict(from_attributes=True)

    team_id: uuid.UUID
    team_name: str
    role: str


class AdminUserListItem(BaseModel):
    """Row in the paginated list response (lightweight).

    H-2: ``role`` / ``team_count`` are a membership *rollup* (highest-effective
    role + membership count) computed by ``list_users`` in one aggregate query,
    so the role column / team count no longer require opening the detail
    drawer. Full memberships stay on :class:`AdminUserDetail`.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str | None = None
    is_active: bool
    is_superuser: bool
    role: Literal["super_admin", "team_admin", "developer", "viewer"] = "developer"
    team_count: int = 0
    last_login_at: datetime | None = None
    created_at: datetime


def _reject_weak_password_if_given(value: str | None) -> str | None:
    """The signup rule, applied to the optional field.

    Reuses the validator the public registration schema uses rather than
    restating it. An import path with its own idea of what a weak password is
    would be a second policy, and the one that admits more wins.
    """
    if value is None:
        return None
    return _reject_weak_password(value)


class AdminUserCreateIn(BaseModel):
    """One person an administrator is adding.

    The same shape whether it arrives alone or as one row of a bulk import, so
    a rule written once applies to both. That matters more here than the
    duplication it saves: a bulk path with its own validation is how an import
    ends up creating accounts the single path would have refused.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(
        default=None,
        min_length=8,
        max_length=256,
        description=(
            "Omit on a deployment where people sign in through an identity "
            "provider: the account is created with no password set, so it "
            "cannot be signed into until somebody sets one through the reset "
            "flow. A password given here is held to the same policy as one "
            "chosen at signup."
        ),
    )
    team_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "The team to put them on. Omitted leaves them on no team, which is "
            "a real state: they can sign in and see nothing until somebody "
            "adds them."
        ),
    )
    role: Literal["team_admin", "developer", "viewer"] | None = Field(
        default=None,
        description=(
            "Their grade on that team. Omitted follows the deployment's "
            "DEFAULT_MEMBER_ROLE. super_admin is not assignable here."
        ),
    )

    _check_password_strength = field_validator("password")(_reject_weak_password_if_given)

    @field_validator("full_name")
    @classmethod
    def _reject_control_characters(cls, value: str | None) -> str | None:
        """A name is text, and a NUL is not.

        Postgres refuses NUL in a text column with an error the row-by-row
        import cannot attribute to a row, so this stops it one layer earlier,
        where the answer names the row. Directory exports carry embedded NULs
        more often than anybody expects.
        """
        if value is None:
            return None
        if any(c in value for c in ("\x00", "\r", "\n")):
            raise ValueError("full_name must not contain control characters")
        return value


class BulkUserCreateIn(BaseModel):
    """A batch of rows, capped so one request cannot become a long transaction."""

    model_config = ConfigDict(extra="forbid")

    users: list[AdminUserCreateIn] = Field(min_length=1, max_length=500)


class BulkDeactivateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class BulkRowResult(BaseModel):
    """What happened to one row.

    Every row gets one of these, in the order they were sent, including the
    ones that worked. A response that lists only failures makes the caller
    infer success by subtraction, and an import of 400 people is exactly where
    that inference goes wrong.
    """

    index: int = Field(description="Position in the submitted list, from zero.")
    identifier: str = Field(
        description="The email or user id this row named, echoed for matching up."
    )
    status: Literal["created", "deactivated", "skipped", "failed"]
    user_id: uuid.UUID | None = None
    #: Stable token, not prose. The message the API writes is English, and this
    #: is the row a Korean administrator reads to decide what to fix.
    reason: str | None = None
    detail: str | None = None


class BulkResultOut(BaseModel):
    """The whole batch.

    HTTP 200 even when rows failed. A batch is not an all-or-nothing request:
    refusing the lot because one address was already taken would make an
    administrator bisect their own file, and a 4xx would say the request was
    malformed when it was understood exactly.
    """

    total: int
    succeeded: int
    failed: int
    results: list[BulkRowResult]


class AdminUserDetail(BaseModel):
    """Full detail view used by the right-side drawer in the Users admin."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str | None = None
    is_active: bool
    is_superuser: bool
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    scan_count: int = 0
    memberships: list[TeamMembershipPublic] = Field(default_factory=list)


class AdminUserListPage(BaseModel):
    """Paginated list envelope."""

    items: list[AdminUserListItem]
    total: int
    page: int
    page_size: int


class AdminUserRoleUpdate(BaseModel):
    """Body for ``PATCH /v1/admin/users/{id}/role``."""

    role: str = Field(description="One of super_admin / team_admin / developer.")
    team_id: uuid.UUID | None = Field(
        default=None,
        description="Required when role is team_admin or developer; ignored for super_admin.",
    )

    @field_validator("role")
    @classmethod
    def _validate_role(cls, value: str) -> str:
        if value not in _ROLE_VALUES:
            raise ValueError(f"role must be one of {_ROLE_VALUES}")
        return value


# ---------------------------------------------------------------------------
# Organizations (read-only: an Organization is created implicitly by
# self-signup or OAuth signup, never through this API -- see
# GET /v1/admin/organizations' docstring for why there is no POST here.)
# ---------------------------------------------------------------------------


class AdminOrganizationListItem(BaseModel):
    """Row in the organization list -- lets an admin discover the id to pass
    as ``AdminTeamCreate.organization_id`` once more than one exists."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    is_personal: bool = Field(
        description=(
            "True for an organization created as a side effect of a signup "
            "(self-registration or OAuth), one per user. AdminTeamCreate."
            "organization_id refuses these -- surfaced here so a caller can "
            "tell before choosing, not just find out from the 422."
        )
    )
    team_count: int = 0
    created_at: datetime


class AdminOrganizationListPage(BaseModel):
    items: list[AdminOrganizationListItem]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


class AdminTeamListItem(BaseModel):
    """Row in the paginated team list — includes counts for the admin table."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None = None
    member_count: int = 0
    project_count: int = 0
    created_at: datetime


class AdminTeamMember(BaseModel):
    """Embedded member row in the team detail response."""

    user_id: uuid.UUID
    email: str
    full_name: str | None = None
    role: str
    is_service_account: bool = Field(
        default=False,
        description=(
            "True for an automation identity. Shown rather than hidden, "
            "because its role is real reach into this team and an admin "
            "reviewing who can touch the team needs to see it. Labelled so "
            "nobody mistakes it for somebody they can write to."
        ),
    )


class AdminTeamDetail(BaseModel):
    """Full detail view used by the right-side drawer in the Teams admin."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None = None
    project_count: int = 0
    members: list[AdminTeamMember] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class AdminTeamListPage(BaseModel):
    items: list[AdminTeamListItem]
    total: int
    page: int
    page_size: int


class AdminTeamCreate(BaseModel):
    """Body for ``POST /v1/admin/teams``."""

    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=1024)
    organization_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Which Organization the team belongs to. Required once a deployment "
            "has more than one (self-signup creates a personal Organization per "
            "user, so a demo SaaS deployment usually does): the create call "
            "refuses with 422 rather than silently guessing. Omit on a "
            "single-organization deployment; see GET /v1/admin/organizations "
            "to find the id."
        ),
    )

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        return _strip_or_raise(value, field="name")

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SLUG_PATTERN.fullmatch(normalized):
            raise ValueError(
                "slug must start with [a-z0-9] and contain only lower-case letters, "
                "digits, or '-' (max 64 chars)"
            )
        return normalized

    @field_validator("description")
    @classmethod
    def _normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AdminTeamUpdate(BaseModel):
    """Body for ``PATCH /v1/admin/teams/{id}``."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=1024)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _strip_or_raise(value, field="name")

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not _SLUG_PATTERN.fullmatch(normalized):
            raise ValueError(
                "slug must start with [a-z0-9] and contain only lower-case letters, "
                "digits, or '-' (max 64 chars)"
            )
        return normalized

    @field_validator("description")
    @classmethod
    def _normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AdminTeamMemberAdd(BaseModel):
    """Body for ``POST /v1/admin/teams/{id}/members``."""

    user_id: uuid.UUID
    role: str = Field(description="One of team_admin, developer or viewer.")

    @field_validator("role")
    @classmethod
    def _validate_role(cls, value: str) -> str:
        if value not in _TEAM_ROLE_VALUES:
            raise ValueError(f"role must be one of {_TEAM_ROLE_VALUES}")
        return value


__all__ = [
    "AdminOrganizationListItem",
    "AdminOrganizationListPage",
    "AdminTeamCreate",
    "AdminTeamDetail",
    "AdminTeamListItem",
    "AdminTeamListPage",
    "AdminTeamMember",
    "AdminTeamMemberAdd",
    "AdminTeamUpdate",
    "AdminUserDetail",
    "AdminUserListItem",
    "AdminUserListPage",
    "AdminUserRoleUpdate",
    "TeamMembershipPublic",
]
