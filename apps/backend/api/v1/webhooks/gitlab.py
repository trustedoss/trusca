"""
GitLab webhook receiver — Phase 5 PR #16.

Endpoint:
  POST /v1/webhooks/gitlab

Required headers:
  X-Gitlab-Token:        <secret>          # constant-time compared to project secret
  X-Gitlab-Webhook-UUID: <uuid>            # idempotency key (newer GitLab)
  X-Gitlab-Event:        "Push Hook" | "Merge Request Hook" | ...

Public endpoint — no JWT. Authentication is the X-Gitlab-Token header
compared against ``projects.webhook_secret`` in constant time.

Notes:
  - Older GitLab versions did not send X-Gitlab-Webhook-UUID. For those we
    fall back to ``payload.object_attributes.id`` (merge requests) or
    ``payload.checkout_sha`` (pushes) as the delivery id. The lookup still
    runs through the same uniqueness gate.
  - The body is consumed raw and JSON-parsed exactly once — GitLab does not
    HMAC-sign the body, so the byte-exact preservation that GitHub needs
    does not apply, but we still avoid Pydantic body parsing here so a
    malformed JSON results in a clean 400 + Problem Details.
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
    process_gitlab_webhook,
)

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])
log = structlog.get_logger("webhooks.gitlab")


def _problem_for_webhook_error(request: Request, exc: WebhookError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


def _extract_delivery_id(
    explicit: str | None,
    payload: dict[str, Any],
) -> str | None:
    """Resolve the delivery id from the header, falling back to payload fields.

    Order of preference:
      1. X-Gitlab-Webhook-UUID header (newer GitLab installs).
      2. ``object_attributes.id`` (merge request hooks).
      3. ``checkout_sha`` (push hooks — at least unique per push).

    Returns None if none of these are usable.
    """
    if explicit:
        return explicit
    obj = payload.get("object_attributes")
    if isinstance(obj, dict):
        oid = obj.get("id")
        if oid is not None:
            return f"obj:{oid}"
    sha = payload.get("checkout_sha")
    if isinstance(sha, str) and sha:
        return f"sha:{sha}"
    return None


@router.post(
    "/gitlab",
    summary="Receive a GitLab webhook delivery",
    responses={
        200: {
            "description": (
                "Delivery accepted. Body shape: "
                "``{\"status\": \"enqueued\"|\"duplicate\"|\"ignored\", "
                "\"delivery_id\": str, \"scan_id\": uuid?}``"
            )
        },
        400: {"description": "Required webhook headers are missing or malformed JSON body."},
        401: {"description": "X-Gitlab-Token mismatch."},
        404: {"description": "No project configured for the payload's repository URL."},
    },
)
async def gitlab_webhook_endpoint(
    request: Request,
    x_gitlab_token: str | None = Header(default=None, alias="X-Gitlab-Token"),
    x_gitlab_event: str | None = Header(default=None, alias="X-Gitlab-Event"),
    x_gitlab_webhook_uuid: str | None = Header(default=None, alias="X-Gitlab-Webhook-UUID"),
    session: AsyncSession = Depends(get_db),
) -> Response:
    body = await request.body()
    if not body:
        return problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Empty Body",
            detail="GitLab webhook body must be a non-empty JSON document.",
            instance=request.url.path,
        )

    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        return problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Invalid JSON",
            detail="GitLab webhook body did not parse as JSON.",
            instance=request.url.path,
        )
    if not isinstance(payload, dict):
        return problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Invalid Payload Shape",
            detail="GitLab webhook payload must be a JSON object.",
            instance=request.url.path,
        )

    delivery_id = _extract_delivery_id(x_gitlab_webhook_uuid, payload)

    try:
        result = await process_gitlab_webhook(
            session,
            body=body,
            token_header=x_gitlab_token,
            delivery_id=delivery_id,
            event_header=x_gitlab_event,
            payload=payload,
        )
    except WebhookError as exc:
        return _problem_for_webhook_error(request, exc)

    response_body = {
        "status": result.status,
        "delivery_id": delivery_id,
        "scan_id": str(result.scan_id) if result.scan_id is not None else None,
    }
    return Response(
        content=json.dumps(response_body),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


__all__ = ["router"]
