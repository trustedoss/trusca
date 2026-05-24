"""
Remediation dry-run schemas — v2.2 2.2-b2.

Request / response Pydantic v2 models for the npm manifest-remediation dry-run:

  POST /v1/projects/{project_id}/remediation/npm/dry-run
      → :class:`NpmDryRunRequest` (optional uploaded manifest)
      → :class:`NpmDryRunResponse` (proposed edited package.json + diff)

The dry-run NEVER opens a PR and NEVER persists — it returns the *proposed* edit
so the caller can review it before b3 (which will open the GitHub PR). Every
field carries an ``examples`` entry so the OpenAPI doc is self-describing.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ManifestSource = Literal["override", "preserved_source", "none"]


class NpmDryRunRequest(BaseModel):
    """Optional uploaded ``package.json`` body.

    When omitted, the endpoint best-effort fetches the manifest from the
    project's latest preserved scan source. Supplying ``manifest`` is the
    reliable path when the source was never preserved (or was swept).
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
            "Raw package.json text to edit. When omitted, the endpoint reads the "
            "manifest from the latest preserved scan source (best-effort)."
        ),
    )


class DependencyChangeOut(BaseModel):
    """One applied range edit in the proposed manifest."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "package": "lodash",
                    "section": "dependencies",
                    "before": "^4.17.20",
                    "after": "^4.17.21",
                    "changed": True,
                }
            ]
        }
    )

    package: str = Field(description="The npm package name (scoped names kept).")
    section: str = Field(
        description=(
            "Manifest block the entry lives in (dependencies / devDependencies / "
            "optionalDependencies / peerDependencies)."
        )
    )
    before: str = Field(description="The range string before the edit.")
    after: str = Field(description="The range string after the edit.")
    changed: bool = Field(description="Whether the range actually changed.")


class RemediationWarningOut(BaseModel):
    """A non-fatal note about the dry-run (skip reason / lockfile guidance)."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code": "lockfile_regeneration_required",
                    "package": None,
                    "detail": (
                        "package.json was edited; run `npm install` to regenerate "
                        "package-lock.json"
                    ),
                }
            ]
        }
    )

    code: str = Field(description="Machine-readable warning code.")
    package: str | None = Field(
        default=None, description="The package the warning concerns, if any."
    )
    detail: str = Field(description="Human-readable explanation.")


class DryRunRecommendationOut(BaseModel):
    """One npm component the dry-run proposes to bump (advisory)."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "package": "lodash",
                    "current_version": "4.17.20",
                    "recommended_version": "4.17.21",
                }
            ]
        }
    )

    package: str = Field(description="The npm package name (scoped names kept).")
    current_version: str = Field(description="The version the latest scan saw.")
    recommended_version: str = Field(
        description="The minimum-safe upgrade target (from the a3 engine)."
    )


class NpmDryRunResponse(BaseModel):
    """The computed npm remediation dry-run."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "project_id": "5b8f1c2e-0c2a-4a1e-9c3d-9c2b1a0f7e11",
                    "scan_id": "7a1d2c3b-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
                    "ecosystem": "npm",
                    "manifest_source": "preserved_source",
                    "manifest_found": True,
                    "changed": True,
                    "edited_manifest": (
                        '{\n  "name": "demo",\n  "dependencies": '
                        '{\n    "lodash": "^4.17.21"\n  }\n}\n'
                    ),
                    "recommendations": [
                        {
                            "package": "lodash",
                            "current_version": "4.17.20",
                            "recommended_version": "4.17.21",
                        }
                    ],
                    "changes": [
                        {
                            "package": "lodash",
                            "section": "dependencies",
                            "before": "^4.17.20",
                            "after": "^4.17.21",
                            "changed": True,
                        }
                    ],
                    "warnings": [
                        {
                            "code": "lockfile_regeneration_required",
                            "package": None,
                            "detail": "run `npm install` to regenerate package-lock.json",
                        }
                    ],
                    "notes": [],
                }
            ]
        }
    )

    project_id: uuid.UUID = Field(description="The project the dry-run is for.")
    scan_id: uuid.UUID | None = Field(
        default=None,
        description="The scan the recommendations were derived from (latest scan).",
    )
    ecosystem: str = Field(description="Always 'npm' for this endpoint.")
    manifest_source: ManifestSource = Field(
        description=(
            "Where the manifest came from: 'override' (request body), "
            "'preserved_source' (latest scan tarball), or 'none' (not available)."
        )
    )
    manifest_found: bool = Field(description="True iff a manifest was available to edit.")
    changed: bool = Field(description="True iff at least one dependency range was rewritten.")
    edited_manifest: str | None = Field(
        default=None,
        description=(
            "The proposed edited package.json text (only present when changed); "
            "the lockfile is NOT edited — regenerate it with `npm install`."
        ),
    )
    recommendations: list[DryRunRecommendationOut] = Field(
        default_factory=list,
        description="The npm upgrade recommendations considered for the edit.",
    )
    changes: list[DependencyChangeOut] = Field(
        default_factory=list, description="The applied range edits."
    )
    warnings: list[RemediationWarningOut] = Field(
        default_factory=list,
        description="Non-fatal notes (skipped packages, lockfile guidance, …).",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="High-level notes about the dry-run (e.g. 'no manifest found').",
    )


__all__ = [
    "DependencyChangeOut",
    "DryRunRecommendationOut",
    "ManifestSource",
    "NpmDryRunRequest",
    "NpmDryRunResponse",
    "RemediationWarningOut",
]
