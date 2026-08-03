"""
Unit tests for :mod:`notifications.teams`.

Same pattern as the Slack tests — ``httpx.MockTransport`` for sealed I/O.
"""

from __future__ import annotations

import httpx
import pytest

from notifications import (
    NotificationDeliveryError,
    NotificationDisabled,
    NotificationError,
)
from notifications import teams as teams_mod


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    monkeypatch.delenv("TEAMS_WEBHOOK_URL", raising=False)


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    real_init = httpx.AsyncClient.__init__

    def _patched_init(self: object, *args: object, **kwargs: object) -> None:
        kwargs.pop("transport", None)
        kwargs["transport"] = httpx.MockTransport(handler)  # type: ignore[arg-type]
        real_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)


async def test_send_teams_raises_disabled_when_url_unset() -> None:
    with pytest.raises(NotificationDisabled):
        await teams_mod.send_teams(title="hi", text="body")


async def test_send_teams_requires_title_and_text() -> None:
    with pytest.raises(ValueError):
        await teams_mod.send_teams(
            title="",
            text="x",
            webhook_url="https://outlook.office.com/webhook/abc",
        )


async def test_send_teams_happy_path_builds_message_card(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, text="ok")

    _patch_async_client(monkeypatch, handler)

    await teams_mod.send_teams(
        title="Critical CVE",
        text="CVE-2026-12345 in project foo",
        webhook_url="https://outlook.office.com/webhook/abc",
    )

    body = captured["body"]
    assert isinstance(body, bytes)
    assert b"MessageCard" in body
    assert b"Critical CVE" in body
    assert b"CVE-2026-12345" in body


async def test_send_teams_5xx_raises_delivery_error(monkeypatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    _patch_async_client(monkeypatch, handler)

    with pytest.raises(NotificationDeliveryError):
        await teams_mod.send_teams(
            title="x",
            text="y",
            webhook_url="https://outlook.office.com/webhook/abc",
        )


async def test_send_teams_4xx_raises_permanent(monkeypatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    _patch_async_client(monkeypatch, handler)

    with pytest.raises(NotificationError) as ei:
        await teams_mod.send_teams(
            title="x",
            text="y",
            webhook_url="https://outlook.office.com/webhook/abc",
        )
    assert not isinstance(ei.value, NotificationDeliveryError)


async def test_send_teams_network_error_raises_delivery(monkeypatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns down")

    _patch_async_client(monkeypatch, handler)

    with pytest.raises(NotificationDeliveryError):
        await teams_mod.send_teams(
            title="x",
            text="y",
            webhook_url="https://outlook.office.com/webhook/abc",
        )
