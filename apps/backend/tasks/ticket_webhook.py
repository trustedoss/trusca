# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Celery task: ``trustedoss.post_ticket_event`` (N11).

Asynchronous on purpose, and that is the whole design. The named risk for this
feature is an outbound call wired into the flow that produced the event, so a
tracker having a slow morning becomes a scan taking eleven minutes and a
tracker being down becomes a scan that failed. Nothing in the scan, gate or
notification path waits on this; they enqueue and carry on.

Failure here is also not failure there. A rejected or undeliverable event is
logged and dropped after its retries, because the work the event describes
still exists in the portal and the ticket is a convenience on top of it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from core.config import ticket_webhook_events, ticket_webhook_url
from integrations.ticket_webhook import (
    TicketWebhookDeliveryError,
    TicketWebhookDisabled,
    TicketWebhookRejected,
    post_event,
)
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.ticket_webhook")


def should_post(event: str) -> bool:
    """Whether this deployment wants a ticket for this kind of event.

    Two questions in order, and the first one is the cheap one: a deployment
    with no URL is asked nothing else. That matters because this runs at every
    producer, and the answer for almost every installation is no.
    """
    if ticket_webhook_url() is None:
        return False
    wanted = ticket_webhook_events()
    if not wanted:
        return True
    return event in wanted


def enqueue_ticket_event(*, event: str, context: dict[str, Any]) -> bool:
    """Queue one event if the deployment wants it. Never raises.

    Returns whether anything was queued, which the callers log rather than
    act on. A broker that will not accept the message is a reason to lose the
    ticket, never a reason to fail the scan that produced it.
    """
    if not should_post(event):
        return False
    try:
        post_ticket_event_task.delay(event, context)
    except Exception as exc:  # noqa: BLE001 (a broker failure is not the caller's problem)
        log.warning("ticket_event_enqueue_failed", event_kind=event, error=str(exc)[:300])
        return False
    return True


def _run(self: Any, event: str, context: dict[str, Any]) -> dict[str, Any]:
    """Body of the task, callable without Celery's self-injection."""
    try:
        return asyncio.run(post_event(event=event, context=context))
    except TicketWebhookDisabled:
        # The URL was removed between the enqueue and the run. Not an error.
        log.info("ticket_event_skipped_not_configured", event_kind=event)
        return {"status": "skipped", "reason": "not_configured"}
    except TicketWebhookRejected as exc:
        # Permanent. Retrying would put the same rejection in the receiver's
        # log every ten minutes until the attempts run out.
        log.warning("ticket_event_rejected", event_kind=event, error=str(exc)[:300])
        return {"status": "rejected", "reason": str(exc)[:300]}


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.post_ticket_event",
    bind=True,
    autoretry_for=(TicketWebhookDeliveryError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def post_ticket_event_task(
    self: Any, event: str, context: dict[str, Any]
) -> dict[str, Any]:
    """Post one event to the deployment's ticket webhook."""
    return _run(self, event, context)


__all__ = [
    "_run",
    "enqueue_ticket_event",
    "post_ticket_event_task",
    "should_post",
]
