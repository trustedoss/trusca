# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
External package lookup schemas.

Powers ``GET /v1/external-packages`` and ``GET /v1/external-advisories/{id}``.
The wire shape is a contract the frontend depends on; keep field names /
nesting stable, changes ripple to the FE mirror.

``ExternalPackageLookupOut.purl`` is deliberately versionless (matches
``Component.purl``'s own identity, "a package identity without version") so
the pre-adoption request flow's purl and the post-scan approval purl are the
same string and the automatic decision-carryover between them keeps working.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class InternalProjectUsage(BaseModel):
    """One project (within the caller's team scope) already using this purl."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "project_id": "6f9619ff-8b86-d011-b42d-00cf4fc964ff",
                "project_name": "Payments API",
                "project_slug": "payments-api",
                "version": "4.17.19",
            }
        },
    )

    project_id: uuid.UUID
    project_name: str
    project_slug: str
    version: str


class ExternalPackageLookupOut(BaseModel):
    """One deps.dev package lookup result."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "ecosystem": "npm",
                "name": "lodash",
                "found": True,
                "version": "4.18.1",
                "purl": "pkg:npm/lodash",
                "licenses": ["MIT"],
                "advisory_count": 0,
                "advisory_ids": [],
                "homepage_url": "https://lodash.com/",
                "source_repo_url": "git+https://github.com/lodash/lodash.git",
                "internal_projects": [],
            }
        }
    )

    ecosystem: str
    name: str
    found: bool
    version: str | None = None
    purl: str | None = None
    licenses: list[str] = Field(default_factory=list)
    advisory_count: int = 0
    advisory_ids: list[str] = Field(default_factory=list)
    homepage_url: str | None = None
    source_repo_url: str | None = None
    internal_projects: list[InternalProjectUsage] = Field(default_factory=list)


class ExternalAdvisoryOut(BaseModel):
    """One deps.dev advisory lookup result, keyed by CVE or GHSA id."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "advisory_id": "GHSA-f23m-r3pf-42rh",
                "found": True,
                "title": "lodash vulnerable to Prototype Pollution",
                "cvss3_score": 6.5,
                "cvss3_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:L/A:L",
                "aliases": ["CVE-2025-13465"],
            }
        }
    )

    advisory_id: str
    found: bool
    title: str | None = None
    cvss3_score: float | None = None
    cvss3_vector: str | None = None
    aliases: list[str] = Field(default_factory=list)
