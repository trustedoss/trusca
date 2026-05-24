"""
Remediation pull-request schemas — v2.2 2.2-b3.

Request / response Pydantic v2 models for the opt-in automated npm remediation
PR endpoint:

  POST /v1/projects/{project_id}/remediation/npm/pull-request
      → :class:`NpmPullRequestCreate` (optional uploaded manifest, same shape as
        the b2 dry-run request)
      → :class:`RemediationPullRequestOut` (the persisted PR record)

  GET  /v1/projects/{project_id}/remediation/pull-requests
      → :class:`RemediationPullRequestList`

Unlike the b2 dry-run (which never persists), b3 ACTUALLY opens a PR on the
project's opted-in GitHub repo and records the attempt. The target repository is
NEVER caller-supplied — it is derived from the project's opted-in installation
(see ``services.remediation_pr_service``). Every field carries an ``examples``
entry so the OpenAPI doc is self-describing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RemediationPRStatus = Literal["creating", "open", "failed", "superseded"]


class NpmPullRequestCreate(BaseModel):
    """Optional uploaded ``package.json`` body (mirrors the b2 dry-run request).

    When omitted, the service best-effort fetches the manifest from the project's
    latest preserved scan source. NOTE: the target repository is NOT part of this
    body — it is derived from the project's opted-in GitHub App installation, so a
    caller can never point the PR at an arbitrary repo.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "manifest": (
                        '{\n  "name": "demo",\n  "dependencies": '
                        '{\n    "lodash": "^4.17.20"\n  }\n}\n'
                    )
                }
            ]
        }
    )

    manifest: str | None = Field(
        default=None,
        description=(
            "Raw package.json text to edit. When omitted, the service reads the "
            "manifest from the latest preserved scan source (best-effort)."
        ),
    )


class RemediationPackageChangeOut(BaseModel):
    """One package bump recorded on the PR (for audit / human review).

    ``populate_by_name`` lets the service build this from keyword args
    (``from_version`` / ``to_version``) while the wire / JSONB shape uses the
    natural ``from`` / ``to`` keys via the field aliases (``from`` is a Python
    keyword, hence the suffix on the attribute name).
    """

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [{"package": "lodash", "from": "4.17.20", "to": "4.17.21"}]
        },
    )

    package: str = Field(description="The npm package name (scoped names kept).")
    from_version: str | None = Field(
        default=None,
        alias="from",
        description="The version the scan saw (advisory; may be null).",
    )
    to_version: str = Field(
        alias="to", description="The minimum-safe upgrade target the PR applies."
    )


class RemediationPullRequestOut(BaseModel):
    """The persisted remediation-PR record returned to the UI."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "9c2b1a0f-7e11-4a1e-9c3d-5b8f1c2e0c2a",
                    "project_id": "5b8f1c2e-0c2a-4a1e-9c3d-9c2b1a0f7e11",
                    "ecosystem": "npm",
                    "repository_full_name": "acme/widget",
                    "head_branch": "trustedoss/remediation-1a2b3c4d",
                    "base_branch": "main",
                    "pr_number": 42,
                    "pr_url": "https://github.com/acme/widget/pull/42",
                    "status": "open",
                    "package_changes": [
                        {"package": "lodash", "from": "4.17.20", "to": "4.17.21"}
                    ],
                    "created_at": "2026-05-25T12:00:00Z",
                    "updated_at": "2026-05-25T12:00:01Z",
                }
            ]
        },
    )

    id: uuid.UUID = Field(description="The remediation-PR record id.")
    project_id: uuid.UUID = Field(description="The project the PR remediates.")
    ecosystem: str = Field(description="Always 'npm' for this endpoint.")
    repository_full_name: str = Field(
        description="The 'owner/repo' the PR targets (derived from the opt-in link)."
    )
    head_branch: str = Field(description="The branch the portal created for the bump.")
    base_branch: str = Field(description="The repo default branch the PR targets.")
    pr_number: int | None = Field(
        default=None, description="The GitHub PR number (null until opened)."
    )
    pr_url: str | None = Field(
        default=None, description="The GitHub PR URL (null until opened)."
    )
    status: RemediationPRStatus = Field(
        description="creating | open | failed | superseded."
    )
    package_changes: list[RemediationPackageChangeOut] = Field(
        default_factory=list, description="The package bumps the PR applies."
    )
    created_at: datetime = Field(description="When the record was created.")
    updated_at: datetime = Field(description="When the record was last updated.")


class RemediationPullRequestList(BaseModel):
    """A page of remediation-PR records for a project (newest first)."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "items": [
                        {
                            "id": "9c2b1a0f-7e11-4a1e-9c3d-5b8f1c2e0c2a",
                            "project_id": "5b8f1c2e-0c2a-4a1e-9c3d-9c2b1a0f7e11",
                            "ecosystem": "npm",
                            "repository_full_name": "acme/widget",
                            "head_branch": "trustedoss/remediation-1a2b3c4d",
                            "base_branch": "main",
                            "pr_number": 42,
                            "pr_url": "https://github.com/acme/widget/pull/42",
                            "status": "open",
                            "package_changes": [
                                {"package": "lodash", "from": "4.17.20", "to": "4.17.21"}
                            ],
                            "created_at": "2026-05-25T12:00:00Z",
                            "updated_at": "2026-05-25T12:00:01Z",
                        }
                    ],
                    "total": 1,
                }
            ]
        }
    )

    items: list[RemediationPullRequestOut] = Field(
        default_factory=list, description="The remediation-PR records."
    )
    total: int = Field(description="Total records for the project.")


__all__ = [
    "NpmPullRequestCreate",
    "RemediationPRStatus",
    "RemediationPackageChangeOut",
    "RemediationPullRequestList",
    "RemediationPullRequestOut",
]
