"""
Microsoft Teams incoming-webhook channel — Phase 6 PR #18.

Same shape as :mod:`notifications.slack` — we POST a JSON payload to the
configured webhook URL. Teams accepts the legacy "MessageCard" envelope
out-of-the-box, which avoids the Adaptive Card schema complications. The
dispatcher hands us a ``title`` + ``text`` pair and we wrap them.

Classification mirrors the Slack channel exactly:
  - 2xx                   → return ``None``.
  - 4xx                   → :class:`NotificationError` (no retry).
  - 5xx / network / timeout → :class:`NotificationDeliveryError` (retry).
  - URL unset             → :class:`NotificationDisabled` (skip).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog

from core.config import notification_http_timeout_seconds, teams_webhook_url

from . import NotificationDeliveryError, NotificationDisabled, NotificationError

log = structlog.get_logger("notifications.teams")


def _safe_host(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "<invalid>"
    return parsed.hostname or "<no-host>"


def _build_message_card(*, title: str, text: str) -> dict[str, Any]:
    """Return a minimal MessageCard payload Teams accepts."""
    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        # ThemeColor is a 6-digit hex without the leading '#'. We use a
        # neutral blue; severity-coloured variants are a follow-up.
        "themeColor": "0078D7",
        "summary": title,
        "title": title,
        "text": text,
    }


async def send_teams(
    *,
    title: str,
    text: str,
    webhook_url: str | None = None,
) -> None:
    """POST an MS Teams MessageCard webhook payload.

    Raises:
        NotificationDisabled: webhook URL unset.
        NotificationDeliveryError: 5xx / network / timeout.
        NotificationError: 4xx (permanent).
    """
    if not title or not text:
        raise ValueError("send_teams requires both title and text")

    url = webhook_url or teams_webhook_url()
    if not url:
        raise NotificationDisabled("Teams not configured (TEAMS_WEBHOOK_URL is unset)")

    payload = _build_message_card(title=title, text=text)
    timeout = notification_http_timeout_seconds()
    safe_host = _safe_host(url)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        log.warning(
            "teams_send_network_failure",
            host=safe_host,
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )
        raise NotificationDeliveryError(
            f"Teams webhook network failure: {type(exc).__name__}"
        ) from exc

    if 500 <= response.status_code < 600:
        log.warning(
            "teams_send_5xx",
            host=safe_host,
            status=response.status_code,
        )
        raise NotificationDeliveryError(
            f"Teams webhook {response.status_code}",
        )

    if 400 <= response.status_code < 500:
        log.warning(
            "teams_send_4xx",
            host=safe_host,
            status=response.status_code,
            body=response.text[:200],
        )
        raise NotificationError(
            f"Teams webhook rejected payload: {response.status_code}"
        )

    log.info("teams_send_ok", host=safe_host, status=response.status_code)


__all__ = ["send_teams"]
