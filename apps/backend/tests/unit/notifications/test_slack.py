"""
Unit tests for :mod:`notifications.slack`.

We use ``httpx.MockTransport`` to seal the test inside the process — no real
HTTP. The pattern mirrors :file:`tests/unit/integrations/dt/test_client.py`.
"""

from __future__ import annotations

import httpx
import pytest

from notifications import (
    NotificationDeliveryError,
    NotificationDisabled,
    NotificationError,
)
from notifications import slack as slack_mod


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    """Replace httpx.AsyncClient's transport so POST hits ``handler``."""
    real_init = httpx.AsyncClient.__init__

    def _patched_init(self: object, *args: object, **kwargs: object) -> None:
        kwargs.pop("transport", None)
        kwargs["transport"] = httpx.MockTransport(handler)  # type: ignore[arg-type]
        real_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)


async def test_send_slack_raises_disabled_when_url_unset() -> None:
    with pytest.raises(NotificationDisabled):
        await slack_mod.send_slack(text="hello")


async def test_send_slack_requires_text() -> None:
    with pytest.raises(ValueError):
        await slack_mod.send_slack(text="", webhook_url="https://hooks.example.com/T/B/C")


async def test_send_slack_happy_path_returns_none(monkeypatch) -> None:
    received: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received["url"] = str(request.url)
        received["body"] = request.read()
        return httpx.Response(200, text="ok")

    _patch_async_client(monkeypatch, handler)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/C")

    await slack_mod.send_slack(text="critical CVE", blocks=[{"type": "section"}])

    url_value = received["url"]
    body_value = received["body"]
    assert isinstance(url_value, str) and "hooks.slack.com" in url_value
    assert isinstance(body_value, bytes) and b"critical CVE" in body_value


async def test_send_slack_5xx_raises_delivery_error(monkeypatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    _patch_async_client(monkeypatch, handler)

    with pytest.raises(NotificationDeliveryError):
        await slack_mod.send_slack(
            text="x",
            webhook_url="https://hooks.example.com/T/B/C",
        )


async def test_send_slack_4xx_raises_permanent_error_not_delivery(monkeypatch) -> None:
    """4xx is permanent — must not retry through Celery."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="invalid_payload")

    _patch_async_client(monkeypatch, handler)

    with pytest.raises(NotificationError) as ei:
        await slack_mod.send_slack(
            text="x",
            webhook_url="https://hooks.example.com/T/B/C",
        )
    # Important: 4xx must NOT be classified as a retryable delivery error.
    assert not isinstance(ei.value, NotificationDeliveryError)


async def test_send_slack_timeout_raises_delivery_error(monkeypatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    _patch_async_client(monkeypatch, handler)

    with pytest.raises(NotificationDeliveryError):
        await slack_mod.send_slack(
            text="x",
            webhook_url="https://hooks.example.com/T/B/C",
        )


async def test_send_slack_does_not_log_full_webhook_url(
    monkeypatch, caplog
) -> None:
    import logging as _logging

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    _patch_async_client(monkeypatch, handler)

    secret_path = "T012XYZ/B345SECRET/SeCrEtToKeN"
    url = f"https://hooks.slack.com/services/{secret_path}"

    # Silence httpx's own request logging (which echoes the URL) — we only
    # care that *our* slack module does not leak the secret path.
    _logging.getLogger("httpx").setLevel(_logging.WARNING)
    _logging.getLogger("httpcore").setLevel(_logging.WARNING)

    await slack_mod.send_slack(text="x", webhook_url=url)

    own_records = [r for r in caplog.records if r.name.startswith("notifications")]
    captured = " ".join(record.getMessage() for record in own_records)
    assert secret_path not in captured
