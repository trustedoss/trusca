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
    model_validator,
)

from core.url_guard import GitUrlValidationError, validate_git_url

# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------

# Project slug: lowercase letters, digits, dashes. 1-64 chars. No leading/
# trailing dash. The DB column already enforces 64 char max via String(64).
_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")

# Reserved slugs that the schema rejects even though they match the pattern
# (BUG-011). ``organization`` is reserved for the Phase 3+ org-wide projects
# feature: an org-wide route/segment would collide with a project that owns the
# slug. Rejecting it at the schema layer keeps the guarantee next to the
# contract (the visibility validator below references the same reservation).
_RESERVED_SLUGS = frozenset({"organization"})

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

# default_branch is forwarded to the GitHub remediation-PR service (b3), where it
# is interpolated into GitHub API URL paths/queries and the PR `base` field. We
# restrict it to a git-ref-safe charset here at the API boundary (defence in
# depth — b3 re-validates too). The charset is letters/digits and the ref-safe
# punctuation `._/-`; we additionally reject `..` (path traversal) and a leading
# `/` (empty path segment) in the validator. This rejects control chars, spaces,
# `&`, `?`, `#` by construction (they are not in the charset).
_DEFAULT_BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")


def _validate_default_branch(value: str | None) -> str | None:
    """Shared default_branch validator for ProjectCreate / ProjectUpdate.

    Returns ``None`` for an empty/blank value (the project falls back to the
    server default ``main``); otherwise enforces the git-ref-safe shape and
    rejects traversal / leading-slash. Raises ``ValueError`` (Pydantic → 422).
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if (
        not _DEFAULT_BRANCH_PATTERN.match(stripped)
        or ".." in stripped
        or stripped.startswith("/")
    ):
        raise ValueError(
            "default_branch must be a git-ref-safe name (letters, digits, and"
            " '._/-' only; no spaces, control chars, '&', '?', '#', '..', or a"
            " leading '/')",
        )
    return stripped

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

# feat/zip-upload: how the worker materialises the source tree.
#   - "git"    (default / backward-compatible): clone project.git_url
#   - "upload": extract a previously-uploaded zip identified by archive_id
_SCAN_SOURCE_TYPES = frozenset({"git", "upload"})

# Feature #18 Part A — release/version label. An optional ``metadata.release``
# (e.g. "v1.2.3") lets a user map a release tag → its scan findings. We restrict
# it to the SAME git-ref-safe charset/length approach used for ``default_branch``
# (letters, digits, and the ref-safe punctuation ``._/-``), bounded at 100 chars
# (a generous cap for a version/ref label — well under the 16 KiB metadata cap).
# The label is descriptive-only metadata: it is never interpolated into a shell,
# a URL path, or a git ref, but we still validate it at the boundary so the
# stored value is predictable and the UI never has to defend against control
# chars / spaces / shell metacharacters. Empty / missing is allowed.
_SCAN_RELEASE_MAX_LEN = 100
_SCAN_RELEASE_PATTERN = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")


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
        if value in _RESERVED_SLUGS:
            raise ValueError(f"slug {value!r} is reserved and cannot be used")
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

    @field_validator("default_branch")
    @classmethod
    def _validate_default_branch(cls, value: str | None) -> str | None:
        return _validate_default_branch(value)

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
    # Feature #18 Part B — private-repo git credential (WRITE-ONLY).
    #
    # `git_credential` is a plaintext PAT / deploy token. When provided and
    # non-empty the service encrypts it (core.crypto.encrypt_secret) and stores
    # the Fernet ciphertext in `projects.git_credential_encrypted`. It is NEVER
    # echoed back in any response (there is deliberately NO matching field on
    # ProjectPublic) — the read side only exposes the boolean `has_git_credential`.
    # An 8 KiB cap is a generous ceiling for a PAT / deploy token (GitHub PATs are
    # ~40-93 chars; an SSH key is a future step) while bounding the encrypt input.
    #
    # `clear_git_credential` is the explicit way to remove a stored credential
    # (set the column back to NULL). We use a dedicated boolean flag rather than
    # overloading `git_credential=""`/`null`, because the field is write-only and
    # "unset vs explicit-null" under PATCH semantics is ambiguous for a secret —
    # a boolean flag makes the caller's intent unambiguous and is self-documenting
    # in the OpenAPI schema. Setting both `git_credential` (non-empty) AND
    # `clear_git_credential=true` in one request is rejected (422) by the
    # validator below as a contradictory payload.
    git_credential: str | None = Field(
        default=None,
        max_length=8192,
        description=(
            "Write-only plaintext git credential (PAT / deploy token) for cloning "
            "a private repo. Encrypted at rest; NEVER returned in any response. "
            "Provide a non-empty value to set/rotate it. Omit to leave it "
            "unchanged. Use `clear_git_credential: true` to remove it."
        ),
    )
    clear_git_credential: bool = Field(
        default=False,
        description=(
            "Set true to remove a stored git credential (column → NULL). "
            "Cannot be combined with a non-empty `git_credential`."
        ),
    )

    @model_validator(mode="after")
    def _validate_credential_intent(self) -> ProjectUpdate:
        """Reject a contradictory credential payload (set AND clear in one request).

        A non-empty `git_credential` means "set/rotate"; `clear_git_credential`
        means "remove". Asking for both at once is ambiguous, so we 422 rather
        than silently picking one.
        """
        if (
            self.clear_git_credential
            and self.git_credential is not None
            and self.git_credential.strip() != ""
        ):
            raise ValueError(
                "git_credential and clear_git_credential are mutually exclusive:"
                " provide a non-empty git_credential to set it, OR"
                " clear_git_credential=true to remove it, not both",
            )
        return self

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

    @field_validator("default_branch")
    @classmethod
    def _validate_default_branch(cls, value: str | None) -> str | None:
        return _validate_default_branch(value)

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


class SeveritySummary(BaseModel):
    """Per-project vulnerability-severity counts for the project-list risk badge.

    Counts are the number of *components* (component_versions) landing in each
    severity bucket — the worst CVE finding per component — within the project's
    latest **succeeded** scan (resolved via
    ``services.scan_resolution.latest_succeeded_scan_id``, the same anchor the
    overview / build-gate use). Only the four risk-bearing buckets are surfaced
    (``info`` / ``none`` are not actionable on a list-row indicator). A project
    with a succeeded scan but no CVE findings yields all-zero counts; a project
    that has never succeeded a scan surfaces ``severity_summary = null`` instead
    (see ``ProjectPublic.severity_summary``).
    """

    model_config = ConfigDict(from_attributes=True)

    critical: int = Field(default=0, ge=0)
    high: int = Field(default=0, ge=0)
    medium: int = Field(default=0, ge=0)
    low: int = Field(default=0, ge=0)


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
    # Feature #18 Part B — read-only "is a private-repo credential configured?"
    # flag. This is the ONLY thing the read side exposes about the credential:
    # the plaintext and the ciphertext are NEVER returned. Derived from the
    # `Project.has_git_credential` computed property (true iff
    # `git_credential_encrypted` is non-null) so `from_attributes` picks it up
    # without the schema ever touching the ciphertext column.
    has_git_credential: bool = False
    # #25 — project-list status badge + risk indicator. These two fields are
    # populated ONLY on the list endpoint (GET /v1/projects), which enriches each
    # page row in TWO batched queries (no N+1). On single-project responses
    # (GET/POST/PATCH /v1/projects/{id}) they default to null — the detail UI
    # reads status / severity from the richer overview endpoint instead.
    latest_scan_status: ScanStatus | None = Field(
        default=None,
        description=(
            "Status of the project's latest scan *attempt* (the scan pointed at "
            "by `latest_scan_id`): queued|running|succeeded|failed|cancelled. "
            "`null` when the project has never been scanned — the UI renders an "
            "'Idle' badge. This tracks the attempt timeline, NOT the current SCA "
            "posture (a failed latest attempt still shows `failed` here while "
            "`severity_summary` reflects the last succeeded scan)."
        ),
    )
    severity_summary: SeveritySummary | None = Field(
        default=None,
        description=(
            "Vulnerability-severity component counts from the project's latest "
            "*succeeded* scan (the same anchor the overview / build-gate use). "
            "`null` when the project has no succeeded scan. Drives the per-row "
            "risk indicator. Note this can be non-null even when "
            "`latest_scan_status` is 'failed' — the last *attempt* failed but an "
            "earlier scan succeeded and its findings remain the current posture."
        ),
    )
    # W3 #30 — list-row discoverability aggregates. Populated ONLY on the list
    # endpoint (GET /v1/projects), which fills them via one batched GROUP BY
    # query alongside the status / severity maps. On single-project responses
    # (GET/POST/PATCH /v1/projects/{id}) they default to 0 / 0 / null — those
    # surfaces read the richer overview endpoint instead.
    scan_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Total scan attempts for the project (any status). Populated only "
            "on the list endpoint; defaults to 0 on single-project responses."
        ),
    )
    release_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Count of succeeded scans (the 'release' model: every succeeded "
            "scan IS a release snapshot). Populated only on the list endpoint; "
            "defaults to 0 on single-project responses."
        ),
    )
    last_scan_at: datetime | None = Field(
        default=None,
        description=(
            "Timestamp of the most recent scan *attempt* (any status). `null` "
            "when the project has never been scanned. Populated only on the "
            "list endpoint; defaults to null on single-project responses."
        ),
    )
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
        """Bound the metadata blob in two dimensions (M-2) + validate source_type.

        - Serialized JSON byte size must be <= 16 KiB. We measure with
          ``json.dumps(..., separators=(",", ":"))`` (compact form) so the
          number we cap on matches what gets stored on disk most closely.
        - Nested depth must be <= 4. Walk the tree once to compute the max
          level; cheap for any reasonable input.
        - ``source_type`` (feat/zip-upload) selects how the worker fetches the
          source. Defaults to ``"git"`` for backward compatibility (existing
          callers omit the key entirely). When ``"upload"`` is given, an
          ``archive_id`` string is required so the worker knows which uploaded
          zip to extract.
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

        # source_type / archive_id contract. Absent key == "git" so legacy
        # payloads keep working without change.
        source_type = value.get("source_type", "git")
        if source_type not in _SCAN_SOURCE_TYPES:
            raise ValueError(
                "metadata.source_type must be one of"
                f" {sorted(_SCAN_SOURCE_TYPES)}; got {source_type!r}",
            )
        if source_type == "upload":
            archive_id = value.get("archive_id")
            if not isinstance(archive_id, str) or not archive_id.strip():
                raise ValueError(
                    "metadata.archive_id (str) is required when"
                    " source_type == 'upload'",
                )

        # Feature #18 Part A — optional release/version label. Absent / blank is
        # allowed (the scan simply carries no release). When present it must be a
        # short, ref-safe label (same charset as default_branch). This rejects
        # spaces, control chars, and shell metacharacters (`;`, `&`, `|`, ...)
        # by construction — they are not in the charset.
        release = value.get("release")
        if release is not None:
            if not isinstance(release, str):
                raise ValueError("metadata.release must be a string when present")
            stripped_release = release.strip()
            if stripped_release and (
                len(stripped_release) > _SCAN_RELEASE_MAX_LEN
                or not _SCAN_RELEASE_PATTERN.match(stripped_release)
            ):
                raise ValueError(
                    "metadata.release must be a short version/ref-safe label"
                    " (letters, digits, and '._/-' only; no spaces, control"
                    f" chars, or shell metacharacters; max {_SCAN_RELEASE_MAX_LEN}"
                    " chars)",
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
    # Feature #18 Part A — convenience surfacing of the release/version label that
    # rides inside `metadata` (the canonical store). It is derived from
    # `metadata.get("release")` in the `_derive_release` validator below so a
    # client never has to dig into the metadata blob to map a release → findings.
    # `null` when the scan carried no release. The value is still present in
    # `metadata` too; this is a read-only mirror, not a second source of truth.
    release: str | None = None
    # P1 #5 — denormalized project name/slug surfaced on every scan row so the
    # cross-project Scans queue and project-deeplinking UIs don't have to issue
    # a second round-trip per row. Both are nullable: the listing endpoints
    # eager-load `Scan.project` (selectinload) but a defensive `None` is the
    # safe fallback for any code path that constructs a ScanPublic directly
    # from a Scan without the relationship loaded.
    project_name: str | None = None
    project_slug: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_scan(cls, scan: Any) -> ScanPublic:
        """Build a ScanPublic from a Scan ORM row, populating project_name /
        project_slug from the loaded ``Scan.project`` relationship.

        Callers MUST have eager-loaded the relationship (selectinload) — this
        helper does not lazy-load (a lazy load would trip the async greenlet
        guard). When the relationship is not loaded the two fields default to
        None so the response stays well-formed.
        """
        pub = cls.model_validate(scan)
        # `scan.project` may not be loaded; access via __dict__ so we never
        # trigger a lazy-load that would crash under asyncpg.
        project = scan.__dict__.get("project")
        if project is not None:
            pub.project_name = project.name
            pub.project_slug = project.slug
        return pub

    @model_validator(mode="after")
    def _derive_release(self) -> ScanPublic:
        """Populate `release` from the metadata blob (the canonical store).

        Runs after field population so `self.metadata` is already remapped off the
        ORM `scan_metadata` attribute. A non-string / blank stored value collapses
        to `None` (defensive — the inbound validator already enforces the shape,
        but a row written before this feature, or by a future code path, must not
        surface a non-string here).
        """
        raw = self.metadata.get("release") if isinstance(self.metadata, dict) else None
        if isinstance(raw, str) and raw.strip():
            self.release = raw.strip()
        else:
            self.release = None
        return self


class SourceArchiveUploadResponse(BaseModel):
    """Outbound shape for POST /v1/projects/{project_id}/source-archive.

    The opaque ``archive_id`` is later echoed into ``ScanCreate.metadata`` as
    ``{"source_type": "upload", "archive_id": "<id>"}`` to scan the uploaded
    source instead of cloning a git URL.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"archive_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479"},
        },
    )

    archive_id: str


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
    "SeveritySummary",
]
