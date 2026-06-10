"""
Admin API request/response schemas — Phase 4 PR #13.

Pydantic v2. Schemas split into Users / Teams. Every shape that comes off the
ORM uses ``model_config = ConfigDict(from_attributes=True)`` so the router
can pass model instances directly into ``model_validate``.

Adversarial input notes (memory ``feedback_adversarial_input_parametrize``):
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

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Common
# ---------------------------------------------------------------------------

# Closed role set — must match the user_role ENUM created in 0002_auth_schema.
_ROLE_VALUES = ("super_admin", "team_admin", "developer")
_TEAM_ROLE_VALUES = ("team_admin", "developer")
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
    role: Literal["super_admin", "team_admin", "developer"] = "developer"
    team_count: int = 0
    last_login_at: datetime | None = None
    created_at: datetime


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
    role: str = Field(description="Either team_admin or developer.")

    @field_validator("role")
    @classmethod
    def _validate_role(cls, value: str) -> str:
        if value not in _TEAM_ROLE_VALUES:
            raise ValueError(f"role must be one of {_TEAM_ROLE_VALUES}")
        return value


__all__ = [
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
