"""
GitHub webhook receiver — Phase 5 PR #16.

Endpoint:
  POST /v1/webhooks/github

Required headers:
  X-Hub-Signature-256: sha256=<hex>     # HMAC over raw body using project secret
  X-GitHub-Delivery:   <uuid>           # idempotency key
  X-GitHub-Event:      <event>          # 'push', 'pull_request', 'ping', ...

Public endpoint — no JWT. Authentication is the HMAC over the request body.
The body is read RAW (not via Pydantic JSON parsing) because HMAC is
calculated over the exact bytes the SCM sent; re-serialising via
``request.json()`` would change whitespace and invalidate the signature.

All 4xx / 5xx responses use ``application/problem+json`` (RFC 7807).

Logging:
  Signatures, secrets, and the request body are NEVER logged. Only the
  delivery_id, event_type, project_id, and high-level outcome are emitted
  via structlog.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from services.webhook_service import (
    WebhookError,
    process_github_webhook,
)

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])
log = structlog.get_logger("webhooks.github")


def _problem_for_webhook_error(request: Request, exc: WebhookError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


@router.post(
    "/github",
    summary="Receive a GitHub webhook delivery",
    responses={
        200: {
            "description": (
                "Delivery accepted. Body shape: "
                "``{\"status\": \"enqueued\"|\"duplicate\"|\"ignored\", "
                "\"delivery_id\": str, \"scan_id\": uuid?}``"
            )
        },
        400: {"description": "Required webhook headers are missing or malformed JSON body."},
        401: {"description": "HMAC verification failed."},
        404: {"description": "No project configured for the payload's repository URL."},
    },
)
async def github_webhook_endpoint(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
    x_github_delivery: str | None = Header(default=None, alias="X-GitHub-Delivery"),
    session: AsyncSession = Depends(get_db),
) -> Response:
    body = await request.body()
    if not body:
        return problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Empty Body",
            detail="GitHub webhook body must be a non-empty JSON document.",
            instance=request.url.path,
        )

    # Parse JSON only after the HMAC step in the service has already checked
    # signature integrity over the raw bytes. We need the parsed body to
    # extract the repo URL, so JSON-parsing failure is a 400 — the SCM
    # literally sent bad data.
    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        return problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Invalid JSON",
            detail="GitHub webhook body did not parse as JSON.",
            instance=request.url.path,
        )
    if not isinstance(payload, dict):
        return problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Invalid Payload Shape",
            detail="GitHub webhook payload must be a JSON object.",
            instance=request.url.path,
        )

    try:
        result = await process_github_webhook(
            session,
            body=body,
            signature_header=x_hub_signature_256,
            delivery_id=x_github_delivery,
            event_type=x_github_event,
            payload=payload,
        )
    except WebhookError as exc:
        return _problem_for_webhook_error(request, exc)

    response_body = {
        "status": result.status,
        "delivery_id": x_github_delivery,
        "scan_id": str(result.scan_id) if result.scan_id is not None else None,
    }
    return Response(
        content=json.dumps(response_body),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


__all__ = ["router"]
