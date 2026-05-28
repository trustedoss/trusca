"""
SMTP email channel — Phase 6 PR #18.

We use :mod:`aiosmtplib` (asyncio-native) so the FastAPI event loop is not
blocked while a remote SMTP server thinks. Production deployments configure
``SMTP_HOST`` / ``SMTP_PORT`` / ``SMTP_USER`` / ``SMTP_PASSWORD`` /
``SMTP_USE_STARTTLS`` via env. When ``SMTP_HOST`` is unset we raise
:class:`NotificationDisabled` so the dispatcher records the channel as
``status='skipped'`` and does NOT trigger Celery retry.

Security:
  - Recipient addresses are not logged. Subject / body are not logged.
  - Authentication credentials come from env at call time (CLAUDE.md core
    rule #11) — never module-level constants.
  - The ``Message-ID`` header is generated at send time. The ``From:`` header
    is the configured ``SMTP_FROM`` so MTAs that enforce envelope-from
    matching accept the mail.
"""

from __future__ import annotations

import email.utils
import socket
import ssl
import uuid
from email.message import EmailMessage

import aiosmtplib
import structlog

from core.config import (
    smtp_from_address,
    smtp_host,
    smtp_password,
    smtp_port,
    smtp_request_timeout_seconds,
    smtp_use_starttls,
    smtp_user,
)

from . import NotificationDeliveryError, NotificationDisabled

log = structlog.get_logger("notifications.email")


def _build_message(
    *,
    to: list[str],
    subject: str,
    body_text: str,
    body_html: str | None,
    from_address: str,
) -> EmailMessage:
    """Assemble an :class:`EmailMessage` with text + optional HTML alternative."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_address
    msg["To"] = ", ".join(to)
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain="trustedoss")
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    return msg


async def send_email(
    *,
    to: list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> None:
    """Send a single email via SMTP.

    Raises:
        NotificationDisabled: ``SMTP_HOST`` is unset.
        NotificationDeliveryError: any transient delivery failure (timeout,
            5xx, network). Permanent 5xx failures are surfaced as
            :class:`NotificationDeliveryError` too — the dispatcher decides
            whether the Celery retry envelope retries them.

    The function returns ``None`` on success.
    """
    if not to:
        raise ValueError("send_email requires at least one recipient")

    host = smtp_host()
    if not host:
        raise NotificationDisabled("SMTP not configured (SMTP_HOST is unset)")

    port = smtp_port()
    user = smtp_user()
    password = smtp_password()
    use_starttls = smtp_use_starttls()
    timeout = smtp_request_timeout_seconds()
    from_address = smtp_from_address()

    msg = _build_message(
        to=to,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        from_address=from_address,
    )

    # Bind a per-call correlation id so the operator can match log lines
    # across the SMTP handshake / send / failure pathway. Do NOT log
    # recipients / subject / body — they may contain PII.
    correlation_id = uuid.uuid4().hex[:12]
    log.info(
        "email_send_start",
        correlation_id=correlation_id,
        host=host,
        port=port,
        recipients=len(to),
        starttls=use_starttls,
    )

    try:
        await aiosmtplib.send(
            msg,
            hostname=host,
            port=port,
            username=user,
            password=password,
            start_tls=use_starttls,
            tls_context=ssl.create_default_context() if use_starttls else None,
            timeout=timeout,
        )
    except (
        aiosmtplib.SMTPException,
        TimeoutError,
        ConnectionError,
        OSError,
        socket.gaierror,
    ) as exc:
        log.warning(
            "email_send_failed",
            correlation_id=correlation_id,
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )
        raise NotificationDeliveryError(
            f"SMTP delivery failed: {type(exc).__name__}"
        ) from exc

    log.info("email_send_ok", correlation_id=correlation_id)


__all__ = ["send_email"]
