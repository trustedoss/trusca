"""
Slack incoming-webhook channel — Phase 6 PR #18.

We POST a JSON document of the form ``{"text": str, "blocks": [...]}`` to the
configured webhook URL. Slack returns 200 + ``ok`` body on success and
4xx with a textual error code (``invalid_payload``, ``channel_not_found``,
``no_text``) on permanent failure; 5xx is treated as transient.

Classification:
  - 2xx                   → return ``None``.
  - 4xx                   → :class:`NotificationError` (no retry).
  - 5xx / network / timeout → :class:`NotificationDeliveryError` (retry).
  - URL unset             → :class:`NotificationDisabled` (skip).

The webhook URL itself is treated as a secret. We never log the URL — the
log lines emit the host component only so debugging "did the worker reach
Slack" is still possible without leaking the token segment.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog

from core.config import notification_http_timeout_seconds, slack_webhook_url

from . import NotificationDeliveryError, NotificationDisabled, NotificationError

log = structlog.get_logger("notifications.slack")


def _safe_host(url: str) -> str:
    """Return only the host component for logging — never the full URL."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "<invalid>"
    return parsed.hostname or "<no-host>"


async def send_slack(
    *,
    text: str,
    blocks: list[dict[str, Any]] | None = None,
    webhook_url: str | None = None,
) -> None:
    """POST a Slack incoming-webhook payload.

    Args:
        text: Fallback text used by Slack's notification surface (push
            notifications, screen readers, channel previews). Required even
            when ``blocks`` is supplied.
        blocks: Optional Slack Block Kit payload.
        webhook_url: Override for the env-derived URL. The dispatcher passes
            the per-recipient URL when teams have their own integrations.

    Raises:
        NotificationDisabled: webhook URL unset.
        NotificationDeliveryError: 5xx / network / timeout.
        NotificationError: 4xx (permanent — invalid payload / dead webhook).
    """
    if not text:
        raise ValueError("send_slack requires a non-empty text fallback")

    url = webhook_url or slack_webhook_url()
    if not url:
        raise NotificationDisabled("Slack not configured (SLACK_WEBHOOK_URL is unset)")

    payload: dict[str, Any] = {"text": text}
    if blocks:
        payload["blocks"] = blocks

    timeout = notification_http_timeout_seconds()
    safe_host = _safe_host(url)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        log.warning(
            "slack_send_network_failure",
            host=safe_host,
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )
        raise NotificationDeliveryError(
            f"Slack webhook network failure: {type(exc).__name__}"
        ) from exc

    if 500 <= response.status_code < 600:
        log.warning(
            "slack_send_5xx",
            host=safe_host,
            status=response.status_code,
        )
        raise NotificationDeliveryError(
            f"Slack webhook {response.status_code}",
        )

    if 400 <= response.status_code < 500:
        # 4xx is permanent — caller misconfigured the webhook or sent bad
        # JSON. We surface it as the parent class so the dispatcher records
        # ``status='failed'`` but does NOT retry through Celery.
        log.warning(
            "slack_send_4xx",
            host=safe_host,
            status=response.status_code,
            body=response.text[:200],
        )
        raise NotificationError(
            f"Slack webhook rejected payload: {response.status_code}"
        )

    log.info("slack_send_ok", host=safe_host, status=response.status_code)


__all__ = ["send_slack"]
