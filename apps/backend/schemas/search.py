"""
Global cross-project search schemas — BomLens parity backlog H-2.

Powers ``GET /v1/search``. The wire shape is a contract the frontend depends on
(the global search palette renders these rows and deep-links each hit back to
its owning project via ``project_id`` / ``project_slug`` / ``project_name``).
Keep field names / nesting stable; changes ripple to the FE mirror.

Every result carries its owning project so a hit is self-describing (no second
round-trip to resolve the project). Team isolation is enforced in the service
via :func:`core.authz.team_scope_filter` — these schemas only shape output.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class ComponentSearchHit(BaseModel):
    """One component match, scoped to the project it was observed in."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "project_id": "6f9619ff-8b86-d011-b42d-00cf4fc964ff",
                "project_name": "Payments API",
                "project_slug": "payments-api",
                "component_name": "lodash",
                "version": "4.17.19",
                "purl": "pkg:npm/lodash",
            }
        },
    )

    project_id: uuid.UUID
    project_name: str
    project_slug: str
    component_name: str
    version: str
    purl: str


class VulnerabilitySearchHit(BaseModel):
    """One CVE match, scoped to a project whose scans surfaced it."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "project_id": "6f9619ff-8b86-d011-b42d-00cf4fc964ff",
                "project_name": "Payments API",
                "project_slug": "payments-api",
                "cve_id": "CVE-2021-23337",
                "severity": "high",
            }
        },
    )

    project_id: uuid.UUID
    project_name: str
    project_slug: str
    cve_id: str
    severity: str


class GlobalSearchResults(BaseModel):
    """Top-level envelope for ``GET /v1/search``.

    ``query`` echoes back the trimmed search term the server actually ran (so
    the client can reconcile debounced input). Each category is independently
    capped at 20 hits by the service.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "lodash",
                "components": [
                    {
                        "project_id": "6f9619ff-8b86-d011-b42d-00cf4fc964ff",
                        "project_name": "Payments API",
                        "project_slug": "payments-api",
                        "component_name": "lodash",
                        "version": "4.17.19",
                        "purl": "pkg:npm/lodash",
                    }
                ],
                "vulnerabilities": [
                    {
                        "project_id": "6f9619ff-8b86-d011-b42d-00cf4fc964ff",
                        "project_name": "Payments API",
                        "project_slug": "payments-api",
                        "cve_id": "CVE-2021-23337",
                        "severity": "high",
                    }
                ],
            }
        }
    )

    query: str
    components: list[ComponentSearchHit] = Field(default_factory=list)
    vulnerabilities: list[VulnerabilitySearchHit] = Field(default_factory=list)


__all__ = [
    "ComponentSearchHit",
    "GlobalSearchResults",
    "VulnerabilitySearchHit",
]
