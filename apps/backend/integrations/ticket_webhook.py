# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Posting an event worth a ticket to whatever the organisation runs (N11).

Generic on purpose. The portal posts a structured event; the organisation's
own adapter turns it into a ticket. The mapping is where organisations differ
most, and it differs in ways no setting captures: which project the ticket
lands in, which issue type, which custom fields are mandatory, who it is
assigned to, whether an existing ticket should be updated instead. An adapter
for one tracker would serve one organisation and mislead every other one into
thinking the feature was for them.

The payload is versioned and its shape is pinned by a test. Whoever writes the
receiving adapter reads a JSON document once and then depends on it; changing
a field name later without saying so breaks code we cannot see and cannot fix.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from core.config import (
    notification_http_timeout_seconds,
    ticket_webhook_token,
    ticket_webhook_url,
)

log = structlog.get_logger("integrations.ticket_webhook")

#: Bumped when a field changes meaning or disappears. Adding a field does not
#: bump it: a receiver that ignores unknown keys keeps working, which is the
#: contract every well-behaved consumer of a webhook already follows.
PAYLOAD_VERSION = 1


class TicketWebhookDisabled(Exception):
    """No URL configured. Not an error, and not retried."""


class TicketWebhookDeliveryError(Exception):
    """Transient: a timeout, a network failure, or a 5xx. Worth retrying."""


class TicketWebhookRejected(Exception):
    """Permanent: the receiver answered 4xx. Retrying would repeat it."""


def build_payload(
    *,
    event: str,
    context: dict[str, Any],
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """The document the receiving adapter reads.

    Deliberately flat and small. Everything here is already visible to anybody
    holding a portal account, and the context is passed through as the
    producer wrote it rather than reshaped per event, so a new event kind
    needs no change here and no change on the receiver that ignores fields it
    does not know.
    """
    return {
        "version": PAYLOAD_VERSION,
        "event": event,
        "occurred_at": (occurred_at or datetime.now(tz=UTC)).isoformat(),
        "source": "trusca",
        "context": dict(context),
    }


def _safe_host(url: str) -> str:
    """The host, for logs. The path often carries the secret."""
    try:
        return httpx.URL(url).host
    except Exception:  # noqa: BLE001
        return "<unparseable>"


async def post_event(*, event: str, context: dict[str, Any]) -> dict[str, Any]:
    """Post one event. Raises rather than reporting, so the task can retry.

    Nothing here reaches the database or the flow that produced the event. The
    caller is a Celery task precisely so a slow or dead receiver costs a
    worker slot rather than a scan.
    """
    url = ticket_webhook_url()
    if url is None:
        raise TicketWebhookDisabled("no ticket webhook configured")

    payload = build_payload(event=event, context=context)
    headers = {"Content-Type": "application/json"}
    token = ticket_webhook_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout = notification_http_timeout_seconds()
    host = _safe_host(url)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        log.warning(
            "ticket_webhook_network_failure",
            host=host,
            event_kind=event,
            error_type=type(exc).__name__,
        )
        raise TicketWebhookDeliveryError(
            f"ticket webhook network failure: {type(exc).__name__}"
        ) from exc

    if 500 <= response.status_code < 600:
        log.warning("ticket_webhook_5xx", host=host, event_kind=event, status=response.status_code)
        raise TicketWebhookDeliveryError(f"ticket webhook {response.status_code}")

    if 400 <= response.status_code < 500:
        # Permanent. A receiver that rejects the document will reject it again
        # in ten minutes, and the retries would be the only thing in its log.
        log.warning(
            "ticket_webhook_4xx",
            host=host,
            event_kind=event,
            status=response.status_code,
            body=response.text[:200],
        )
        raise TicketWebhookRejected(
            f"ticket webhook rejected the event: {response.status_code}"
        )

    log.info("ticket_webhook_ok", host=host, event_kind=event, status=response.status_code)
    return {"status": "delivered", "http_status": response.status_code}
