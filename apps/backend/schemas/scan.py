"""
Project + Scan request/response schemas — Phase 2 PR #7.

Pydantic v2. The Project schemas are split into Create/Update/Public so:
  - inbound JSON cannot smuggle server-managed fields (`id`, `archived_at`,
    `latest_scan_id`, `created_*`);
  - mutating updates cannot rewrite identity fields (`team_id`, `slug`);
  - the public shape is the single response contract used by every endpoint.

Quality standard §4 (CLAUDE.md): validation failures here surface as 422
problem+json automatically via the RequestValidationError handler in
core.errors.

ENUM tuples (visibility, scan kind, scan status) come from `models.scan` so
the API and the DB ENUMs cannot drift.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from core.url_guard import GitUrlValidationError, validate_git_url

# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------

# Project slug: lowercase letters, digits, dashes. 1-64 chars. No leading/
# trailing dash. The DB column already enforces 64 char max via String(64).
_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")

# Loose git URL guard. We accept:
#   - https://host/path(.git)
#   - http://host/path  (intranet HTTP — common in self-hosted GitLab)
#   - ssh://git@host/path
#   - git@host:path     (the SCP-like SSH form)
#   - git+ssh://...     (occasionally produced by package metadata)
# The objective is "filter out obvious junk while not rejecting legitimate
# enterprise URLs". The shape check below is paired with the SSRF guard in
# core.url_guard.validate_git_url, which enforces scheme allow-list and
# rejects RFC1918 / loopback / cloud-metadata hostnames.
_GIT_URL_PATTERN = re.compile(
    r"^(?:https?://|ssh://|git\+ssh://|git://|[A-Za-z0-9_.\-]+@[A-Za-z0-9_.\-]+:).+",
)

# ---------------------------------------------------------------------------
# Scan metadata bounds (M-2 — security-reviewer finding from PR #7)
# ---------------------------------------------------------------------------
#
# `ScanCreate.metadata` is a JSONB blob. Without bounds the API would let a
# client store an unbounded payload (or crash structlog on logging). We cap:
#   - Serialized JSON byte size at 16 KiB (compact form).
#   - Nested depth at 4 (matches the deepest legitimate shape we see in
#     practice: { ort: { rules: { ignore: [...] } } }).
#
# Both checks run inside `ScanCreate._validate_metadata`. Failures surface
# as 422 problem+json via the FastAPI RequestValidationError handler.
_SCAN_METADATA_MAX_BYTES = 16 * 1024
_SCAN_METADATA_MAX_DEPTH = 4


def _measure_metadata_depth(value: Any, *, _level: int = 0) -> int:
    """Return the maximum nesting depth of `value` (scalars = 0)."""
    if _level > _SCAN_METADATA_MAX_DEPTH * 4:
        # Defensive guard against pathological recursion before pydantic
        # gets a chance to enforce the cap.
        return _level
    if isinstance(value, dict):
        if not value:
            return _level + 1
        return max(
            _measure_metadata_depth(v, _level=_level + 1) for v in value.values()
        )
    if isinstance(value, list):
        if not value:
            return _level + 1
        return max(
            _measure_metadata_depth(item, _level=_level + 1) for item in value
        )
    return _level

ProjectSlug = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, strip_whitespace=True),
]
ProjectName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=255, strip_whitespace=True),
]

# Visibility values mirror models.scan.PROJECT_VISIBILITY_VALUES. We keep the
# Literal here local — drift would surface immediately as a mypy error in the
# service layer when it casts to the model column.
ProjectVisibility = Literal["team", "organization"]
ScanKind = Literal["source", "container"]
ScanStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


# ---------------------------------------------------------------------------
# Project — request / response
# ---------------------------------------------------------------------------


class ProjectCreate(BaseModel):
    """Inbound payload for POST /v1/projects."""

    model_config = ConfigDict(extra="forbid")

    team_id: uuid.UUID
    name: ProjectName
    slug: ProjectSlug
    description: str | None = Field(default=None, max_length=4000)
    git_url: str | None = Field(default=None, max_length=2048)
    default_branch: str | None = Field(default=None, max_length=255)
    # PR #7 only stores 'team'; 'organization' visibility is reserved for
    # Phase 3+ org-wide projects. The validator below rejects 'organization'
    # at the schema layer so the rejection lives next to the contract.
    visibility: ProjectVisibility = "team"

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, value: str) -> str:
        if not _SLUG_PATTERN.match(value):
            raise ValueError(
                "slug must be lowercase alphanumerics and dashes,"
                " 1-64 chars, no leading/trailing dash",
            )
        return value

    @field_validator("git_url")
    @classmethod
    def _validate_git_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if not _GIT_URL_PATTERN.match(stripped):
            raise ValueError(
                "git_url must look like an https://, ssh://, git@host: or"
                " git+ssh:// repository URL",
            )
        # SSRF guard (M-4): scheme allow-list + DNS-resolved IP is not in
        # any non-routable / metadata range. Raises GitUrlValidationError
        # (a ValueError subclass) — Pydantic surfaces it as 422.
        try:
            return validate_git_url(stripped)
        except GitUrlValidationError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("visibility")
    @classmethod
    def _enforce_team_visibility(cls, value: str) -> str:
        # Phase 3+ TODO: relax once organization-wide projects are reachable
        # from the list endpoint (cf. project_service.list_projects).
        if value != "team":
            raise ValueError(
                "visibility='organization' is not enabled in this release;"
                " only 'team' is currently supported",
            )
        return value


class ProjectUpdate(BaseModel):
    """
    Inbound payload for PATCH /v1/projects/{project_id}.

    `team_id` and `slug` are intentionally NOT updatable: changing the team
    would require re-scoping every audit log, scan, and finding; changing the
    slug would invalidate webhook URLs and CLI bookmarks. If the product ever
    needs slug rename, model it as a separate `POST /v1/projects/{id}:rename`
    operation that does the rewrite in one transaction.
    """

    model_config = ConfigDict(extra="forbid")

    name: ProjectName | None = None
    description: str | None = Field(default=None, max_length=4000)
    git_url: str | None = Field(default=None, max_length=2048)
    default_branch: str | None = Field(default=None, max_length=255)
    visibility: ProjectVisibility | None = None

    @field_validator("git_url")
    @classmethod
    def _validate_git_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if not _GIT_URL_PATTERN.match(stripped):
            raise ValueError(
                "git_url must look like an https://, ssh://, git@host: or"
                " git+ssh:// repository URL",
            )
        try:
            return validate_git_url(stripped)
        except GitUrlValidationError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("visibility")
    @classmethod
    def _enforce_team_visibility(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value != "team":
            raise ValueError(
                "visibility='organization' is not enabled in this release;"
                " only 'team' is currently supported",
            )
        return value


class ProjectPublic(BaseModel):
    """Outbound shape for every project-bearing response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    team_id: uuid.UUID
    name: str
    slug: str
    description: str | None
    git_url: str | None
    default_branch: str | None
    visibility: ProjectVisibility
    archived_at: datetime | None
    created_by_user_id: uuid.UUID | None
    latest_scan_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    """Page of projects + total count for client-side paging UI."""

    items: list[ProjectPublic]
    total: int
    page: int
    size: int


# ---------------------------------------------------------------------------
# Scan — request / response
# ---------------------------------------------------------------------------


class ScanCreate(BaseModel):
    """
    Inbound payload for POST /v1/projects/{project_id}/scans.

    `kind` selects the scan pipeline (source = cdxgen + ORT + DT;
    container = Trivy). All scan inputs (git_ref, image_ref, ORT options)
    travel inside `metadata` so the schema does not have to grow a field
    every time the pipeline learns a new knob.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ScanKind = "source"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Bound the metadata blob in two dimensions (M-2).

        - Serialized JSON byte size must be <= 16 KiB. We measure with
          ``json.dumps(..., separators=(",", ":"))`` (compact form) so the
          number we cap on matches what gets stored on disk most closely.
        - Nested depth must be <= 4. Walk the tree once to compute the max
          level; cheap for any reasonable input.
        """
        # Depth check first — a shallow but huge dict still gets caught by
        # the size check, but a deeply nested attacker payload should fail
        # fast before we attempt to serialize it.
        depth = _measure_metadata_depth(value)
        if depth > _SCAN_METADATA_MAX_DEPTH:
            raise ValueError(
                f"metadata nests {depth} levels deep; the maximum allowed is"
                f" {_SCAN_METADATA_MAX_DEPTH}",
            )

        try:
            encoded = json.dumps(value, separators=(",", ":"), default=str)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"metadata is not JSON-serializable: {exc}",
            ) from exc

        size = len(encoded.encode("utf-8"))
        if size > _SCAN_METADATA_MAX_BYTES:
            raise ValueError(
                f"metadata is {size} bytes; the maximum allowed is"
                f" {_SCAN_METADATA_MAX_BYTES} bytes",
            )

        return value


class ScanPublic(BaseModel):
    """Outbound shape for every scan-bearing response."""

    # `from_attributes=True` lets us construct directly from a `Scan` ORM row;
    # the `metadata` alias below remaps the ORM attribute (`scan_metadata`,
    # renamed because `metadata` clashes with `DeclarativeBase.metadata`) onto
    # the API field `metadata`.
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    project_id: uuid.UUID
    kind: ScanKind
    status: ScanStatus
    progress_percent: int
    current_step: str | None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    requested_by_user_id: uuid.UUID | None
    celery_task_id: str | None
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        # Pull from the ORM attribute name (Scan.scan_metadata) which is
        # renamed off the DB column `metadata`.
        validation_alias="scan_metadata",
        serialization_alias="metadata",
    )
    created_at: datetime
    updated_at: datetime


class ScanListResponse(BaseModel):
    """Page of scans for a project."""

    items: list[ScanPublic]
    total: int
    page: int
    size: int


__all__ = [
    "ProjectCreate",
    "ProjectListResponse",
    "ProjectPublic",
    "ProjectUpdate",
    "ProjectVisibility",
    "ScanCreate",
    "ScanKind",
    "ScanListResponse",
    "ScanPublic",
    "ScanStatus",
]
