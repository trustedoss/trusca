# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Paged search-result shapes for the full search page (S3).

Separate from ``schemas/search.py`` on purpose. That module shapes the ⌘K
palette: a fixed handful of hits per category, no counts, no paging, and a
contract the frontend palette and its tests already depend on. This one shapes
the full search page: ONE kind at a time, paged, counted, with the facet tallies
the filter bar renders.

Trying to serve both from one envelope means a response whose shape depends on
which parameters were sent, which is where search APIs become impossible to type
against. Two envelopes, two jobs.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class SearchFacetBucket(BaseModel):
    """One facet value and how many results carry it.

    Counts are computed over the whole matching set, not the current page —
    a facet that only counted the visible rows would tell the user nothing
    about what clicking it would do. Whole here means "up to
    ``services.search_results_service.RESULT_COUNT_CAP``" (Q3,
    2026-08-24): when the page's ``counts_capped`` is true, every bucket's
    ``count`` is a floor rather than the true count too, since it was tallied
    from the same bounded window ``total`` was.
    """

    value: str
    count: int


class ProjectResult(BaseModel):
    """A project whose name or slug matched."""

    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    project_name: str
    project_slug: str
    git_url: str | None = None
    archived: bool = False


class ComponentResult(BaseModel):
    """A component match, scoped to the project it was observed in."""

    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    project_name: str
    project_slug: str
    component_id: uuid.UUID
    component_name: str
    version: str
    purl: str
    package_type: str


class VulnerabilityResult(BaseModel):
    """A CVE match, scoped to a project whose current scan surfaced it."""

    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    project_name: str
    project_slug: str
    finding_id: uuid.UUID
    cve_id: str
    severity: str
    status: str
    component_name: str
    version: str


class LicenseResult(BaseModel):
    """A license match, scoped to a project whose current scan carries it."""

    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    project_name: str
    project_slug: str
    license_id: uuid.UUID
    spdx_id: str | None = None
    license_name: str
    category: str
    component_name: str
    version: str


class SearchResultsPage(BaseModel):
    """One page of results for one kind.

    Exactly one of the four item lists is populated — the one named by ``kind``.
    Modelling them as four typed lists rather than a single heterogeneous list
    keeps the wire shape checkable on both sides; the client reads the list its
    active tab owns and ignores the rest.
    """

    kind: str
    query: str
    items_projects: list[ProjectResult] = Field(default_factory=list)
    items_components: list[ComponentResult] = Field(default_factory=list)
    items_vulnerabilities: list[VulnerabilityResult] = Field(default_factory=list)
    items_licenses: list[LicenseResult] = Field(default_factory=list)
    total: int = 0
    #: True when the match set is larger than
    #: ``services.search_results_service.RESULT_COUNT_CAP`` (Q3, 2026-08-24):
    #: ``total`` and every facet bucket's ``count`` are then a floor, not the
    #: true count, so the query matches AT LEAST this many, not exactly this
    #: many. False (the default) means every count on this page is exact.
    counts_capped: bool = False
    page: int = 1
    size: int = 25
    #: Facet name → buckets, e.g. ``{"severity": [{"value": "high", "count": 3}]}``.
    #: Which facets appear depends on the kind; an empty dict is valid.
    facets: dict[str, list[SearchFacetBucket]] = Field(default_factory=dict)


__all__ = [
    "ComponentResult",
    "LicenseResult",
    "ProjectResult",
    "SearchFacetBucket",
    "SearchResultsPage",
    "VulnerabilityResult",
]
