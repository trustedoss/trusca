---
id: ticket-integration
title: Ticket integration
description: Post events worth a ticket to your own tracker with a generic outbound webhook, off by default.
sidebar_label: Ticket integration
sidebar_position: 10
---

# Ticket integration

The portal can post an event to a URL you own when something happens that is
worth a ticket. Your adapter turns that into a ticket in whatever tracker you
run.

Off by default. With no URL configured nothing is called: no request, no
queued task, no log line about a skipped delivery.

## Why it is generic

There is no built-in adapter for any particular tracker, and that is a
decision rather than a gap. The mapping from an event to a ticket is where
organisations differ most, and they differ in ways no setting captures: which
project the ticket lands in, which issue type, which custom fields are
mandatory, who it is assigned to, whether an existing ticket should be updated
instead of a new one raised. An adapter for one tracker would serve one
organisation and mislead every other one into thinking the feature was for
them.

What you write is small: an endpoint that accepts a JSON POST and calls your
tracker's own API.

## Configuration

| Setting | Meaning |
|---|---|
| `TICKET_WEBHOOK_URL` | Where to post. Empty means off. |
| `TICKET_WEBHOOK_TOKEN` | Optional bearer token. Empty means none, which is right when the URL already carries a secret in its path. |
| `TICKET_WEBHOOK_EVENTS` | Which event kinds are worth a ticket, comma separated. Empty means all of them. |

Most deployments name a few kinds. A finished scan is worth a line in a chat
channel and is usually not worth a ticket anybody will close; a new critical
CVE or a component waiting on review usually is.

## The payload

```json
{
  "version": 1,
  "event": "new_critical_cve",
  "occurred_at": "2026-08-20T09:12:44.128301+00:00",
  "source": "trusca",
  "context": {
    "cve_id": "CVE-2026-1234",
    "project_id": "3f1c…",
    "project_name": "payments-api",
    "severity": "CRITICAL"
  }
}
```

`version` is bumped when a field changes meaning or disappears. Adding a field
does not bump it, so write your adapter to ignore keys it does not know.

`context` is passed through as the portal built it and its keys depend on the
event. Read the ones you need and ignore the rest; that way a new event kind
needs no change on your side.

## What happens when your tracker is unavailable

Nothing that matters. The post is made by a background task, never by the flow
that produced the event, so a tracker having a slow morning cannot become a
scan that takes eleven minutes and one that is down cannot become a scan that
failed.

| Your response | What the portal does |
|---|---|
| 2xx | Done. |
| 5xx, timeout, connection refused | Retries with backoff, up to five attempts. |
| 4xx | Gives up at once. A receiver that rejects the document will reject it again in ten minutes, and the retries would be the only thing in your log. |

An event that cannot be delivered is logged and dropped. The work it described
is still in the portal, where somebody will find it on the screen that is
about that work; the ticket is a convenience on top of that, not the record.

## Verify it worked

<!-- docs-uat: id=ticket-webhook-off-by-default kind=manual tier=manual -->
1. With no `TICKET_WEBHOOK_URL`, run a scan that produces a critical finding
   and confirm your receiver logs nothing at all.
<!-- docs-uat: id=ticket-webhook-delivers kind=manual tier=manual -->
2. Point `TICKET_WEBHOOK_URL` at a request bin, repeat, and confirm one POST
   arrives with the envelope above.

## See also

- [Notifications](../user-guide/notifications.md): who hears about an event
  is a separate question from what gets a ticket.
