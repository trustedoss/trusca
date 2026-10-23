# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
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
from types import TracebackType
from typing import Any

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
from core.logging import mask_pii

from . import NotificationDeliveryError, NotificationDisabled, NotificationError

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


def _is_permanent(exc: BaseException) -> bool:
    """A 5xx reply about this message or recipient: resending will not help.

    Connection loss, timeouts and 4xx replies (greylisting, mailbox busy) are
    transient. ``SMTPRecipientsRefused`` carries one reply per refused
    recipient; it is permanent only when every one of them is 5xx.
    """
    if isinstance(exc, aiosmtplib.SMTPRecipientsRefused):
        codes = [refused.code for refused in exc.recipients]
        return bool(codes) and all(code >= 500 for code in codes)
    if isinstance(exc, aiosmtplib.SMTPResponseException) and not isinstance(
        exc, aiosmtplib.SMTPConnectError | aiosmtplib.SMTPAuthenticationError
    ):
        return exc.code >= 500
    return False


# Errors that mean the connection itself is gone or unusable, as opposed to
# a reply about one message.
_CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    aiosmtplib.SMTPServerDisconnected,
    aiosmtplib.SMTPConnectError,
    aiosmtplib.SMTPTimeoutError,
    aiosmtplib.SMTPAuthenticationError,
    TimeoutError,
    ConnectionError,
    OSError,
    socket.gaierror,
)
_SMTP_ERRORS: tuple[type[BaseException], ...] = (aiosmtplib.SMTPException, *_CONNECTION_ERRORS)


class EmailBatch:
    """One SMTP connection, one message per recipient.

    Used as ``async with EmailBatch(...) as batch: await batch.send_to(addr)``.
    The connection is opened on the first :meth:`send_to`, so a batch whose
    recipients were all delivered by an earlier attempt never connects.

    :meth:`send_to` raises:
      - :class:`NotificationDisabled` when SMTP is not configured;
      - :class:`NotificationError` for a permanent (5xx) reply about this
        recipient, which a retry would only repeat;
      - :class:`NotificationDeliveryError` for anything transient.

    A lost connection is reopened once per batch. After a second loss, or a
    timeout, the batch stops talking to the server: every later
    :meth:`send_to` fails at once as transient instead of waiting out another
    timeout, so a stalled server costs a couple of timeouts per attempt rather
    than one per recipient.
    """

    def __init__(self, *, subject: str, body_text: str, body_html: str | None = None) -> None:
        self._subject = subject
        self._body_text = body_text
        self._body_html = body_html
        self._client: Any = None
        self._reconnects_left = 1
        self._broken: str | None = None
        self._from_address = ""
        self._correlation_id = uuid.uuid4().hex[:12]

    async def __aenter__(self) -> EmailBatch:
        if not smtp_host():
            raise NotificationDisabled("SMTP not configured (SMTP_HOST is unset)")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._close()

    async def _close(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.quit()
        except Exception:  # noqa: BLE001 (closing a connection we are done with)
            client.close()

    async def _connect(self) -> None:
        host = smtp_host()
        if not host:
            raise NotificationDisabled("SMTP not configured (SMTP_HOST is unset)")
        use_starttls = smtp_use_starttls()
        self._from_address = smtp_from_address()
        client = aiosmtplib.SMTP(
            hostname=host,
            port=smtp_port(),
            username=smtp_user(),
            password=smtp_password(),
            start_tls=use_starttls,
            tls_context=ssl.create_default_context() if use_starttls else None,
            timeout=smtp_request_timeout_seconds(),
        )
        log.info(
            "email_batch_connect",
            correlation_id=self._correlation_id,
            host=host,
            starttls=use_starttls,
        )
        await client.connect()
        self._client = client

    async def send_to(self, recipient: str) -> None:
        if self._broken is not None:
            raise NotificationDeliveryError(f"SMTP delivery failed: {self._broken}")
        while True:
            try:
                if self._client is None:
                    await self._connect()
                msg = _build_message(
                    to=[recipient],
                    subject=self._subject,
                    body_text=self._body_text,
                    body_html=self._body_html,
                    from_address=self._from_address,
                )
                client: Any = self._client
                await client.send_message(msg)
            except NotificationDisabled:
                raise
            except aiosmtplib.SMTPServerDisconnected as exc:
                await self._close()
                if self._reconnects_left > 0:
                    self._reconnects_left -= 1
                    log.info("email_batch_reconnect", correlation_id=self._correlation_id)
                    continue
                self._broken = type(exc).__name__
                raise NotificationDeliveryError(
                    f"SMTP delivery failed: {type(exc).__name__}"
                ) from exc
            except _SMTP_ERRORS as exc:
                if _is_permanent(exc):
                    log.warning(
                        "email_recipient_rejected",
                        correlation_id=self._correlation_id,
                        recipient=mask_pii(recipient),
                        error_type=type(exc).__name__,
                        code=getattr(exc, "code", None),
                    )
                    raise NotificationError(
                        f"SMTP rejected recipient: {type(exc).__name__}"
                    ) from exc
                if isinstance(exc, _CONNECTION_ERRORS):
                    # The server is unreachable or stalled; do not wait for it
                    # again for every remaining recipient.
                    await self._close()
                    self._broken = type(exc).__name__
                log.warning(
                    "email_send_failed",
                    correlation_id=self._correlation_id,
                    error_type=type(exc).__name__,
                    error=str(exc)[:200],
                )
                raise NotificationDeliveryError(
                    f"SMTP delivery failed: {type(exc).__name__}"
                ) from exc
            return


__all__ = ["EmailBatch", "send_email"]
