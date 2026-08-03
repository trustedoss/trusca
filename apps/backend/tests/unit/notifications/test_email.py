"""
Unit tests for :mod:`notifications.email`.

We do NOT spin up a real SMTP server. Instead we monkeypatch
:func:`aiosmtplib.send` so the channel exercise is deterministic and the
test surface stays at the contract boundary:

  - SMTP_HOST unset                -> NotificationDisabled
  - empty recipients               -> ValueError (programmer error)
  - aiosmtplib raises SMTPException -> NotificationDeliveryError
  - aiosmtplib raises TimeoutError  -> NotificationDeliveryError
  - happy path                     -> returns None + EmailMessage built
                                       with the right To: / Subject: headers
"""

from __future__ import annotations

from typing import Any

import aiosmtplib
import pytest

from notifications import NotificationDeliveryError, NotificationDisabled
from notifications.email import send_email


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """Start every test with a clean SMTP env."""
    for key in (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "SMTP_USE_STARTTLS",
        "SMTP_FROM",
        "SMTP_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)


async def test_send_email_raises_disabled_when_smtp_host_unset() -> None:
    with pytest.raises(NotificationDisabled):
        await send_email(
            to=["alice@example.com"],
            subject="hi",
            body_text="body",
        )


async def test_send_email_raises_value_error_on_empty_recipients(monkeypatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    with pytest.raises(ValueError):
        await send_email(to=[], subject="hi", body_text="body")


async def test_send_email_happy_path_calls_aiosmtplib_with_built_message(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_FROM", "no-reply@trustedoss.io")

    captured: dict[str, object] = {}

    async def _fake_send(message, **kwargs):
        captured["message"] = message
        captured["kwargs"] = kwargs

    monkeypatch.setattr(aiosmtplib, "send", _fake_send)

    await send_email(
        to=["alice@example.com", "bob@example.com"],
        subject="Reset your password",
        body_text="Click the link",
        body_html="<p>Click the link</p>",
    )

    msg: Any = captured["message"]
    assert msg["Subject"] == "Reset your password"
    assert msg["From"] == "no-reply@trustedoss.io"
    assert "alice@example.com" in msg["To"]
    assert "bob@example.com" in msg["To"]

    # The message has both a text body and an HTML alternative.
    parts = list(msg.iter_parts())
    assert any("Click the link" in (p.get_content() or "") for p in parts)

    # Connection params propagated.
    kwargs: Any = captured["kwargs"]
    assert kwargs["hostname"] == "smtp.example.com"
    assert kwargs["port"] == 587


async def test_send_email_translates_smtp_exception_into_delivery_error(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")

    async def _boom(*_args, **_kwargs):
        raise aiosmtplib.SMTPException("upstream busy")

    monkeypatch.setattr(aiosmtplib, "send", _boom)

    with pytest.raises(NotificationDeliveryError):
        await send_email(
            to=["alice@example.com"],
            subject="x",
            body_text="x",
        )


async def test_send_email_translates_timeout_into_delivery_error(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")

    async def _timeout(*_args, **_kwargs):
        raise TimeoutError("slow MTA")

    monkeypatch.setattr(aiosmtplib, "send", _timeout)

    with pytest.raises(NotificationDeliveryError):
        await send_email(
            to=["alice@example.com"],
            subject="x",
            body_text="x",
        )


async def test_send_email_translates_oserror_into_delivery_error(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")

    async def _net_down(*_args, **_kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr(aiosmtplib, "send", _net_down)

    with pytest.raises(NotificationDeliveryError):
        await send_email(
            to=["alice@example.com"],
            subject="x",
            body_text="x",
        )


async def test_send_email_does_not_log_recipients(
    monkeypatch, caplog
) -> None:
    """Belt-and-braces: addresses must never appear in structlog output."""
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")

    async def _ok(*_args, **_kwargs):
        return None

    monkeypatch.setattr(aiosmtplib, "send", _ok)

    secret_address = "vip-leak-canary@example.com"
    await send_email(
        to=[secret_address],
        subject="hi",
        body_text="body",
    )

    captured_text = " ".join(record.getMessage() for record in caplog.records)
    assert secret_address not in captured_text
