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

from notifications import NotificationDeliveryError, NotificationDisabled, NotificationError
from notifications.email import EmailBatch, send_email


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


# ---------------------------------------------------------------------------
# EmailBatch: one connection, one message per recipient, per-recipient
# classification of failures.
# ---------------------------------------------------------------------------


class _FakeSMTP:
    """Stands in for ``aiosmtplib.SMTP``. ``script`` maps a recipient to the
    exceptions its successive sends raise (consumed in order)."""

    instances: list[_FakeSMTP] = []
    script: dict[str, list[BaseException]] = {}
    connect_errors: list[BaseException] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.sent: list[str] = []
        self.quit_called = False
        _FakeSMTP.instances.append(self)

    async def connect(self) -> None:
        if _FakeSMTP.connect_errors:
            raise _FakeSMTP.connect_errors.pop(0)

    async def send_message(self, msg: Any) -> None:
        to = msg["To"]
        queue = _FakeSMTP.script.get(to, [])
        if queue:
            raise queue.pop(0)
        self.sent.append(to)

    async def quit(self) -> None:
        self.quit_called = True

    def close(self) -> None:
        return None


@pytest.fixture
def fake_smtp(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    _FakeSMTP.instances = []
    _FakeSMTP.script = {}
    _FakeSMTP.connect_errors = []
    monkeypatch.setattr(aiosmtplib, "SMTP", _FakeSMTP)
    return _FakeSMTP


def _sent(fake: type[_FakeSMTP]) -> list[str]:
    return [to for client in fake.instances for to in client.sent]


async def test_email_batch_reuses_one_connection(fake_smtp) -> None:
    async with EmailBatch(subject="s", body_text="b") as batch:
        for to in ("a@example.com", "b@example.com", "c@example.com"):
            await batch.send_to(to)

    assert len(fake_smtp.instances) == 1
    assert fake_smtp.instances[0].sent == ["a@example.com", "b@example.com", "c@example.com"]
    assert fake_smtp.instances[0].quit_called


async def test_email_batch_never_connects_without_a_send(fake_smtp) -> None:
    async with EmailBatch(subject="s", body_text="b"):
        pass
    assert fake_smtp.instances == []


async def test_email_batch_disabled_without_smtp_host() -> None:
    with pytest.raises(NotificationDisabled):
        async with EmailBatch(subject="s", body_text="b"):
            pass


async def test_email_batch_5xx_recipient_is_permanent(fake_smtp) -> None:
    refused = aiosmtplib.SMTPRecipientRefused(550, "no such user", "gone@example.com")
    fake_smtp.script = {"gone@example.com": [aiosmtplib.SMTPRecipientsRefused([refused])]}

    async with EmailBatch(subject="s", body_text="b") as batch:
        with pytest.raises(NotificationError) as info:
            await batch.send_to("gone@example.com")
        await batch.send_to("a@example.com")

    assert not isinstance(info.value, NotificationDeliveryError)
    # The same connection carried on to the next recipient.
    assert len(fake_smtp.instances) == 1
    assert fake_smtp.instances[0].sent == ["a@example.com"]


async def test_email_batch_4xx_recipient_is_transient(fake_smtp) -> None:
    refused = aiosmtplib.SMTPRecipientRefused(451, "try later", "b@example.com")
    fake_smtp.script = {"b@example.com": [aiosmtplib.SMTPRecipientsRefused([refused])]}

    async with EmailBatch(subject="s", body_text="b") as batch:
        with pytest.raises(NotificationDeliveryError):
            await batch.send_to("b@example.com")
        await batch.send_to("a@example.com")

    assert _sent(fake_smtp) == ["a@example.com"]


async def test_email_batch_5xx_data_reply_is_permanent(fake_smtp) -> None:
    fake_smtp.script = {"x@example.com": [aiosmtplib.SMTPDataError(554, "rejected")]}
    async with EmailBatch(subject="s", body_text="b") as batch:
        with pytest.raises(NotificationError) as info:
            await batch.send_to("x@example.com")
    assert not isinstance(info.value, NotificationDeliveryError)


async def test_email_batch_reconnects_once_on_connection_loss(fake_smtp) -> None:
    fake_smtp.script = {"a@example.com": [aiosmtplib.SMTPServerDisconnected("gone")]}
    async with EmailBatch(subject="s", body_text="b") as batch:
        await batch.send_to("a@example.com")
        await batch.send_to("b@example.com")

    assert len(fake_smtp.instances) == 2
    assert _sent(fake_smtp) == ["a@example.com", "b@example.com"]


async def test_email_batch_gives_up_after_a_second_connection_loss(fake_smtp) -> None:
    lost = aiosmtplib.SMTPServerDisconnected
    fake_smtp.script = {"a@example.com": [lost("1"), lost("2")]}
    async with EmailBatch(subject="s", body_text="b") as batch:
        with pytest.raises(NotificationDeliveryError):
            await batch.send_to("a@example.com")
        with pytest.raises(NotificationDeliveryError):
            await batch.send_to("b@example.com")

    assert len(fake_smtp.instances) == 2
    assert _sent(fake_smtp) == []


async def test_email_batch_timeout_fails_the_rest_fast(fake_smtp) -> None:
    """A stalled server is waited for once per attempt, not once per recipient."""
    fake_smtp.script = {"a@example.com": [aiosmtplib.SMTPReadTimeoutError("stalled")]}
    async with EmailBatch(subject="s", body_text="b") as batch:
        for to in ("a@example.com", "b@example.com", "c@example.com"):
            with pytest.raises(NotificationDeliveryError):
                await batch.send_to(to)

    assert len(fake_smtp.instances) == 1
    assert _sent(fake_smtp) == []


async def test_email_batch_connect_failure_is_transient(fake_smtp) -> None:
    fake_smtp.connect_errors = [aiosmtplib.SMTPConnectError("refused")]
    async with EmailBatch(subject="s", body_text="b") as batch:
        with pytest.raises(NotificationDeliveryError):
            await batch.send_to("a@example.com")
        with pytest.raises(NotificationDeliveryError):
            await batch.send_to("b@example.com")
    assert len(fake_smtp.instances) == 1


async def test_email_batch_masks_rejected_address_in_logs(fake_smtp, capsys) -> None:
    fake_smtp.script = {"secretname@example.com": [aiosmtplib.SMTPDataError(550, "no")]}
    async with EmailBatch(subject="s", body_text="b") as batch:
        with pytest.raises(NotificationError):
            await batch.send_to("secretname@example.com")

    out = capsys.readouterr()
    logged = out.out + out.err
    assert "email_recipient_rejected" in logged
    assert "secretname" not in logged
