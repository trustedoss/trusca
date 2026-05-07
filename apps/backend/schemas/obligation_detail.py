"""
Obligation detail schemas — Phase 3 PR #13 (Obligations tab + NOTICE generator).

Read-only response models for three new endpoints:

- GET /v1/projects/{project_id}/obligations                    → ObligationListResponse
- GET /v1/projects/{project_id}/obligations/{obligation_id}    → ObligationDetailResponse
- GET /v1/projects/{project_id}/notice                         → text/plain (raw)
                                                               → also wraps NoticeMetadata
                                                                 in JSON inspection mode

Why "read-only"
---------------
Obligations are a policy catalog, not an analyst workflow. ORT (and curated
license metadata) declare which obligations a license carries — there is no
fulfillment / accept / reject state on the row itself, no transition matrix,
no audit log. This module therefore exposes only GET shapes and there is no
PATCH counterpart, mirroring the Licenses tab (PR #12).

Open-ended ``kind``
-------------------
Unlike ``license.category`` (a closed Postgres ENUM), ``obligations.kind`` is a
free-form ``String(64)`` so the catalog can grow as new license families are
ingested without a schema migration. We therefore expose ``kind`` as a plain
``str`` and *advertise* a ranked allow-list (``KNOWN_OBLIGATION_KINDS``) for
clients that want predictable ordering without hard-coding the wire format.

License category mirror
-----------------------
``license_category`` reuses :data:`schemas.license_detail.LicenseCategory`
verbatim — a single source of truth across both tabs.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.license_detail import LicenseCategory

# Ranked allow-list of obligation kinds we know about today. The list is
# advisory: the database column is open, so unknown kinds round-trip
# transparently — we just place them after the known ones in the
# distribution payload to keep the chart's primary axis stable.
KNOWN_OBLIGATION_KINDS: tuple[str, ...] = (
    "attribution",
    "notice",
    "source-disclosure",
    "copyleft",
    "modifications",
    "dynamic-linking",
    "no-endorsement",
)

ObligationSortKey = Literal["category", "license_name", "kind", "affected_count"]
NoticeFormat = Literal["text", "markdown"]


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


class ObligationListItem(BaseModel):
    """One row in the Obligations tab table.

    A row is a single ``(license, obligation_kind)`` pair observed in the
    project's latest scan. ``affected_count`` counts distinct
    component_versions that carry the parent license — not the obligation
    itself, which is a per-license policy attribute. The drawer dereferences
    by ``id`` (the obligation row) within the project scope.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="obligations.id (catalog row).")
    license_id: uuid.UUID = Field(description="licenses.id this obligation belongs to.")
    license_spdx_id: str | None = Field(
        default=None,
        description=(
            "SPDX short identifier of the parent license. "
            "Null for ORT custom licenses (LicenseRef-*)."
        ),
    )
    license_name: str = Field(description="Full name of the parent license.")
    license_category: LicenseCategory
    kind: str = Field(
        description=(
            "Obligation kind — free-form catalog string (e.g. attribution, "
            "source-disclosure, copyleft). See KNOWN_OBLIGATION_KINDS for the "
            "ranked allow-list rendered first in the distribution."
        ),
        max_length=64,
    )
    text: str = Field(description="Human-readable obligation text.")
    link: str | None = Field(
        default=None,
        description=(
            "Optional URL with further explanation of the obligation. "
            "Frontends MUST scheme-filter this before rendering as a link."
        ),
    )
    affected_count: int = Field(
        ge=0,
        description=(
            "Distinct component_versions in the latest scan that carry the "
            "parent license."
        ),
    )
    updated_at: datetime


class ObligationListResponse(BaseModel):
    """Page of obligations for the project's latest scan.

    ``distribution`` is the per-kind count across all obligations visible in
    the latest scan, regardless of the active filter. Known kinds appear
    first in their canonical order; unknown kinds (catalog growth) are
    appended alphabetically. Always populated even on empty result sets.
    """

    items: list[ObligationListItem]
    distribution: dict[str, int] = Field(
        description=(
            "kind → count of distinct (license, kind) obligation rows in the "
            "latest scan. Known kinds appear first in canonical order, "
            "followed by unknown kinds alphabetically."
        ),
    )
    total: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Detail / drawer endpoint
# ---------------------------------------------------------------------------


class AffectedComponentByObligation(BaseModel):
    """A component_version in the latest scan that carries the parent license.

    Mirrors :class:`schemas.license_detail.AffectedComponentByLicense` but
    keyed via the obligation's parent license rather than a license_finding
    row directly.
    """

    model_config = ConfigDict(from_attributes=True)

    component_version_id: uuid.UUID
    component_name: str
    version: str


class ObligationDetailResponse(BaseModel):
    """Full drawer payload for a single obligation, scoped to a project."""

    id: uuid.UUID
    license_id: uuid.UUID
    license_spdx_id: str | None = None
    license_name: str
    license_category: LicenseCategory
    license_reference_url: str | None = Field(
        default=None,
        description="licenses.reference_url for further reading.",
    )
    kind: str = Field(max_length=64)
    text: str = Field(
        description=(
            "Human-readable obligation text. Capped at 65 536 bytes — see "
            "``text_truncated``."
        ),
    )
    text_truncated: bool = Field(
        default=False,
        description=(
            "True when the server truncated ``text`` to its 65 536-byte cap. "
            "Clients should display a notice and offer a link to the source "
            "catalog if available."
        ),
    )
    link: str | None = Field(
        default=None,
        description=(
            "Optional URL provided by the catalog. Frontends MUST scheme-"
            "filter to http/https before rendering as a clickable link."
        ),
    )
    affected_components: list[AffectedComponentByObligation] = Field(
        default_factory=list,
        description=(
            "All component_versions in the project's latest scan that carry "
            "the parent license. Capped at 500 rows — see "
            "``affected_components_truncated``."
        ),
    )
    affected_components_truncated: bool = Field(
        default=False,
        description=(
            "True when the server truncated ``affected_components`` to its "
            "500-row cap. Clients should display a notice and optionally "
            "fall back to the components tab for the full list."
        ),
    )
    affected_components_total: int = Field(
        default=0,
        ge=0,
        description=(
            "Total number of distinct component_versions associated with "
            "the parent license in the scan, before the response cap is "
            "applied."
        ),
    )
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# NOTICE file endpoint (JSON wrapper — text/plain stream uses raw body)
# ---------------------------------------------------------------------------


class NoticeMetadata(BaseModel):
    """Metadata block associated with a generated NOTICE body.

    The /notice endpoint defaults to ``text/plain`` for direct download UX.
    Tools that want machine-readable provenance can still POST the same
    inputs to a JSON variant in the future; for now this model is also used
    by the service layer to seal the values it returns, and is exposed as a
    response header set so clients see the same provenance via headers
    (X-Notice-License-Count, X-Notice-Obligation-Count, X-Notice-Generated-At).
    """

    project_id: uuid.UUID
    project_name: str
    generated_at: datetime
    format: NoticeFormat
    license_count: int = Field(ge=0)
    obligation_count: int = Field(ge=0)


__all__ = [
    "AffectedComponentByObligation",
    "KNOWN_OBLIGATION_KINDS",
    "NoticeFormat",
    "NoticeMetadata",
    "ObligationDetailResponse",
    "ObligationListItem",
    "ObligationListResponse",
    "ObligationSortKey",
]
