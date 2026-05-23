"""
Policy gate response schemas — Phase 5 PR #17.

The policy gate is the build-blocking decision the CI pipeline asks the
portal to make: based on the project's most recent successful scan, should
this build pass or fail?

Two HTTP shapes live here:

- :class:`GateResultResponse` — body of
  ``GET /v1/projects/{project_id}/gate-result``. Mirrors
  :class:`services.policy_gate.GateResult` but carries datetimes in ISO-8601
  rather than Python ``datetime`` objects so OpenAPI documents the wire
  format precisely.

- :class:`PostPRCommentRequest` / :class:`PostPRCommentResponse` — request +
  response for ``POST /v1/scans/{scan_id}/post-pr-comment``. The endpoint
  posts (or updates) a Markdown comment on a GitHub PR; the request carries
  the SCM context the CI runner already knows.

Closed-enum mirrors are kept in lock-step with
:data:`services.policy_gate.GateOutcome`.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

GateOutcome = Literal["pass", "fail"]


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/gate-result
# ---------------------------------------------------------------------------


class GateResultResponse(BaseModel):
    """Build-gate verdict for the project's latest successful scan."""

    model_config = ConfigDict(from_attributes=True)

    gate: GateOutcome = Field(
        description="Overall outcome. ``pass`` when no critical CVEs and no "
        "forbidden licenses are present, otherwise ``fail``.",
    )
    reason: str | None = Field(
        default=None,
        description="Human-readable explanation when ``gate == 'fail'``. ``null`` "
        "for passing builds.",
    )
    critical_cve_count: int = Field(
        ge=0,
        description="Number of open critical-severity findings on the evaluated "
        "scan. Open = status not in (not_affected, fixed, false_positive).",
    )
    forbidden_license_count: int = Field(
        ge=0,
        description="Distinct component_versions on the evaluated scan that carry "
        "at least one forbidden-classification license.",
    )
    epss_gate_count: int = Field(
        default=0,
        ge=0,
        description="Number of open findings on the evaluated scan whose CVE has an "
        "EPSS score at or above ``epss_threshold``. Always 0 when the EPSS gate is "
        "disabled (``epss_threshold == null``).",
    )
    epss_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="The active EPSS gate threshold in [0, 1], read from the "
        "``GATE_EPSS_THRESHOLD`` environment variable at evaluation time. ``null`` "
        "when the EPSS gate is disabled (unset/unparseable env), in which case the "
        "gate behaves exactly as the critical-CVE + forbidden-license gate.",
    )
    project_id: uuid.UUID
    scan_id: uuid.UUID | None = Field(
        default=None,
        description="ID of the scan the verdict was computed against. ``null`` "
        "when the project has never had a successful scan, in which case "
        "``gate == 'pass'`` is returned by convention (no signal = no block).",
    )
    evaluated_at: datetime = Field(
        description="Server timestamp at which the verdict was computed (UTC, "
        "ISO-8601).",
    )


# ---------------------------------------------------------------------------
# POST /v1/scans/{scan_id}/post-pr-comment
# ---------------------------------------------------------------------------


# GitHub repository slugs are "owner/repo" with each segment composed of
# letters, digits, hyphen, underscore, dot. We pin a defensive pattern so
# attackers cannot inject path traversal or encoded URLs into the call we
# make to api.github.com.
_REPO_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


class PostPRCommentRequest(BaseModel):
    """CI-side input for ``POST /v1/scans/{scan_id}/post-pr-comment``."""

    repo_full_name: str = Field(
        min_length=3,
        max_length=140,
        description="GitHub ``owner/repo`` slug. Validated against the GitHub "
        "naming rules so we never call api.github.com with attacker-controlled "
        "path segments.",
    )
    pr_number: int = Field(
        ge=1,
        le=10_000_000,
        description="GitHub PR number.",
    )
    dry_run: bool = Field(
        default=False,
        description="When ``true`` the endpoint builds the Markdown comment but "
        "does not call GitHub. Useful for local CI rehearsals and used by the "
        "default integration tests so they do not require network access.",
    )

    @field_validator("repo_full_name")
    @classmethod
    def _validate_repo_full_name(cls, value: str) -> str:
        if not _REPO_SLUG_PATTERN.match(value):
            raise ValueError(
                "repo_full_name must look like 'owner/repo' with [A-Za-z0-9._-] segments",
            )
        return value


class PostPRCommentResponse(BaseModel):
    """Outcome of a PR-comment post."""

    model_config = ConfigDict(from_attributes=True)

    status: Literal["posted", "updated", "dry_run"]
    comment_id: int | None = Field(
        default=None,
        description="GitHub issue-comment id. ``null`` for ``dry_run`` and on "
        "transport errors that we choose not to surface to the caller.",
    )
    comment_url: str | None = Field(
        default=None,
        description="``html_url`` of the comment on github.com.",
    )
    body_preview: str = Field(
        description="The first 280 characters of the rendered comment body. The "
        "full body is never returned because it can grow large; the preview "
        "is enough for the CI runner to log a sanity check.",
    )
    gate: GateOutcome = Field(
        description="Echo of the gate verdict the comment reports. Lets the CI "
        "runner branch on the build-blocking decision in a single round-trip.",
    )


__all__ = [
    "GateOutcome",
    "GateResultResponse",
    "PostPRCommentRequest",
    "PostPRCommentResponse",
]
