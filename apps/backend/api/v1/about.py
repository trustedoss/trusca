# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
About surface — product identity and license notices readable from the portal.

  * ``GET /v1/about``                        — identity + document list (JSON)
  * ``GET /v1/about/notices/{document_id}``  — one document's text (text/plain)

Both are JWT-gated (CLAUDE.md core rule #12). No role check beyond
authentication: a license notice is something every user of the deployment is
entitled to read, so gating it to super-admins would defeat the purpose. It is
not public either — the unauthenticated surface stays limited to the health
probes, and the notices are already published with the source and inside every
image at ``/licenses/``.

Why serve these at all when the files ship in the image: §4(d) is satisfied for
someone with shell access to a container. It does nothing for the person looking
at the portal in a browser, and in an air-gapped deployment "read it on GitHub"
is not an option. See ``services.about_service`` for how the files are located.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import PlainTextResponse

from core.config import intake_requests_enabled
from core.errors import problem_response
from core.security import CurrentUser, get_current_user
from schemas.about import AboutResponse, NoticeDocumentOut
from services.about_service import (
    NoticeNotFoundError,
    NoticeTooLargeError,
    available_documents,
    product_identity,
    read_document,
)

log = structlog.get_logger("api.about")

router = APIRouter(prefix="/v1/about", tags=["about"])


@router.get(
    "",
    response_model=AboutResponse,
    summary="Product identity and the list of license notices",
)
async def get_about(
    _current_user: CurrentUser = Depends(get_current_user),
) -> AboutResponse:
    """Return what this deployment is, and which notices it can show."""
    identity = product_identity()
    documents = [
        NoticeDocumentOut(
            id=doc.id,
            title=doc.title,
            filename=doc.filename,
            description=doc.description,
            size_bytes=size,
        )
        for doc, size in available_documents()
    ]
    return AboutResponse(
        **identity,
        documents=documents,
        # What this deployment has turned on. Read at request time rather than
        # captured at import, so flipping the setting takes effect on the next
        # page load instead of the next restart.
        features={"intake_requests": intake_requests_enabled()},
    )


@router.get(
    "/notices/{document_id}",
    response_class=PlainTextResponse,
    summary="Read one license-notice document",
    responses={
        200: {
            "content": {"text/plain": {}},
            "description": "The document's text, verbatim.",
        },
        404: {"description": "Unknown document id, or absent from this deployment."},
    },
)
async def get_notice(
    document_id: str,
    request: Request,
    _current_user: CurrentUser = Depends(get_current_user),
) -> PlainTextResponse:
    """Return one notice document as ``text/plain``.

    Verbatim: a license text that has been reflowed, truncated or
    markdown-rendered is no longer the notice it is standing in for.
    """
    try:
        _doc, text = read_document(document_id)
    except NoticeNotFoundError:
        # Same 404 for "no such id" and "file missing from this deployment".
        # The distinction is an operator concern (it is logged below and shows
        # as a null size in GET /v1/about), not something to branch the caller's
        # error handling on.
        log.info("about_notice_not_found", document_id=document_id)
        return problem_response(  # type: ignore[return-value]
            status_code=status.HTTP_404_NOT_FOUND,
            title="Notice not found",
            detail=(
                f"No license notice with id {document_id!r} is available from "
                "this deployment."
            ),
            instance=str(request.url.path),
        )
    except NoticeTooLargeError as exc:
        log.warning("about_notice_too_large", document_id=document_id, error=str(exc))
        return problem_response(  # type: ignore[return-value]
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            title="Notice too large to serve",
            detail=(
                "The notice file on disk exceeds the size this endpoint will "
                "return. Read it from the source distribution instead."
            ),
            instance=str(request.url.path),
        )

    # charset is explicit: the Apache-2.0 text is ASCII but NOTICE and
    # THIRD_PARTY_NOTICES.md carry em dashes and "Co., Ltd." punctuation.
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")
