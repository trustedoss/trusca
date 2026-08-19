# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Pydantic schemas for the About surface — ``GET /v1/about``.

Public shapes (frontend depends on these):

  - ``NoticeDocumentOut`` — one notice document's metadata, no body.
  - ``AboutResponse``     — product identity plus the document list.

The document BODIES are not in this contract. They are served as ``text/plain``
by ``GET /v1/about/notices/{document_id}`` so the license text is rendered as
what it is — plain text — rather than JSON-escaped into a string field. It also
keeps the About page's first paint small: the three documents together run past
20 KB, and a reader opens at most one at a time.

``size_bytes`` is ``null`` when the file is absent from the deployment. That is a
packaging fault (every image and the chart ship all three), so the UI shows it
instead of hiding the row.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class NoticeDocumentOut(BaseModel):
    """Metadata for one readable notice document."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "third-party-notices",
                "title": "Third-party notices",
                "filename": "THIRD_PARTY_NOTICES.md",
                "description": (
                    "Attribution for third-party material vendored into the "
                    "source tree and for the tools bundled in the container "
                    "images."
                ),
                "size_bytes": 7586,
            }
        }
    )

    id: str = Field(
        description=(
            "Stable identifier — the path segment for "
            "``GET /v1/about/notices/{document_id}``."
        )
    )
    title: str = Field(description="Human-readable title for the document tab.")
    filename: str = Field(
        description="The file's name as distributed (at ``/licenses/`` in images)."
    )
    description: str = Field(description="One line on what the document covers.")
    size_bytes: int | None = Field(
        default=None,
        description=(
            "Size on disk, or ``null`` when the file is missing from this "
            "deployment — a packaging fault the UI surfaces rather than hides."
        ),
    )


class AboutResponse(BaseModel):
    """Product identity and the list of readable notice documents."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "product": "TRUSCA",
                "version": "2.3.0-dev",
                "license_spdx_id": "Apache-2.0",
                "license_name": "Apache License, Version 2.0",
                "license_url": "https://www.apache.org/licenses/LICENSE-2.0",
                "copyright": "Copyright 2026 TRUSCA contributors",
                "source_url": "https://github.com/trustedoss/trusca",
                "documents": [],
            }
        }
    )

    product: str = Field(description="Product name.")
    version: str = Field(
        description=(
            "Released version, from ``TRUSTEDOSS_VERSION``; a development "
            "default when unset."
        )
    )
    license_spdx_id: str = Field(description="SPDX identifier of TRUSCA's license.")
    license_name: str = Field(description="Full license name.")
    license_url: str = Field(description="Canonical URL of the license text.")
    copyright: str = Field(description="TRUSCA's own copyright line.")
    source_url: str = Field(
        description="Where the corresponding source is published."
    )
    documents: list[NoticeDocumentOut] = Field(
        description="Notice documents, in display order."
    )
    features: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Optional surfaces this deployment has turned on. A key absent or "
            "false means the surface does not exist: its routes answer 404 "
            "and the SPA renders no entry point for it. Carried here because "
            "the shell already reads this response, and probing a route to "
            "decide whether to draw a menu entry makes the menu flicker."
        ),
    )
