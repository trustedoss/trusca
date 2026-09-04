"""
Posting an event worth a ticket (N11).

Three things carry the contract and the tests are mostly about them.

Nothing is called when nothing is configured. Not a request, not a queued
task, not a log line about a skipped delivery. A deployment that has written
no URL should be indistinguishable from one built before this existed.

The call is never in the flow that produced the event. That is the failure
this kind of integration usually has: a tracker with a slow morning becomes a
scan taking eleven minutes, and a tracker that is down becomes a scan that
failed.

And the payload is a document somebody else's adapter reads. Its shape is
pinned here, because changing a field name later breaks code we cannot see.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Off, which is the default
# ---------------------------------------------------------------------------


def test_nothing_is_queued_when_no_url_is_configured(monkeypatch) -> None:
    from tasks import ticket_webhook

    monkeypatch.delenv("TICKET_WEBHOOK_URL", raising=False)
    queued: list[tuple] = []
    monkeypatch.setattr(
        ticket_webhook.post_ticket_event_task,
        "delay",
        lambda *args: queued.append(args),
    )

    posted = ticket_webhook.enqueue_ticket_event(
        event="new_critical_cve", context={"cve_id": "CVE-2026-1"}
    )

    assert posted is False
    assert queued == []


def test_a_notification_queues_nothing_when_the_webhook_is_off(monkeypatch) -> None:
    """Through the path a real event takes, not just the helper."""
    from tasks import notify, ticket_webhook

    monkeypatch.delenv("TICKET_WEBHOOK_URL", raising=False)
    queued: list[tuple] = []
    monkeypatch.setattr(
        ticket_webhook.post_ticket_event_task,
        "delay",
        lambda *args: queued.append(args),
    )

    notify._maybe_raise_a_ticket(kind="new_critical_cve", context={"cve_id": "X"})

    assert queued == []


# ---------------------------------------------------------------------------
# On
# ---------------------------------------------------------------------------


def test_an_event_is_queued_when_a_url_is_configured(monkeypatch) -> None:
    from tasks import ticket_webhook

    monkeypatch.setenv("TICKET_WEBHOOK_URL", "https://tracker.example/hook")
    monkeypatch.delenv("TICKET_WEBHOOK_EVENTS", raising=False)
    queued: list[tuple] = []
    monkeypatch.setattr(
        ticket_webhook.post_ticket_event_task,
        "delay",
        lambda *args: queued.append(args),
    )

    posted = ticket_webhook.enqueue_ticket_event(
        event="new_critical_cve", context={"cve_id": "CVE-2026-1"}
    )

    assert posted is True
    assert queued == [("new_critical_cve", {"cve_id": "CVE-2026-1"})]


def test_the_event_list_narrows_what_is_worth_a_ticket(monkeypatch) -> None:
    """A finished scan is worth a line in a channel and is not worth a ticket
    anybody will close."""
    from tasks import ticket_webhook

    monkeypatch.setenv("TICKET_WEBHOOK_URL", "https://tracker.example/hook")
    monkeypatch.setenv("TICKET_WEBHOOK_EVENTS", "new_critical_cve")

    assert ticket_webhook.should_post("new_critical_cve") is True
    assert ticket_webhook.should_post("scan_completed") is False


def test_an_empty_event_list_means_every_kind(monkeypatch) -> None:
    """The same reading as an absent condition on a routing rule. One
    convention across the settings that filter by kind."""
    from tasks import ticket_webhook

    monkeypatch.setenv("TICKET_WEBHOOK_URL", "https://tracker.example/hook")
    monkeypatch.setenv("TICKET_WEBHOOK_EVENTS", "")

    assert ticket_webhook.should_post("anything_at_all") is True


# ---------------------------------------------------------------------------
# The payload somebody else's adapter reads
# ---------------------------------------------------------------------------


def test_the_payload_shape_is_the_one_an_adapter_was_written_against() -> None:
    from integrations.ticket_webhook import PAYLOAD_VERSION, build_payload

    payload = build_payload(
        event="new_critical_cve",
        context={"cve_id": "CVE-2026-1", "project_name": "demo"},
    )

    assert set(payload) == {"version", "event", "occurred_at", "source", "context"}
    assert payload["version"] == PAYLOAD_VERSION
    assert payload["event"] == "new_critical_cve"
    assert payload["source"] == "trusca"
    assert payload["context"] == {"cve_id": "CVE-2026-1", "project_name": "demo"}


def test_the_context_is_passed_through_rather_than_reshaped() -> None:
    """A new event kind needs no change here, and no change on a receiver that
    ignores fields it does not know."""
    from integrations.ticket_webhook import build_payload

    payload = build_payload(event="something_new", context={"a": 1, "b": {"c": 2}})

    assert payload["context"] == {"a": 1, "b": {"c": 2}}


def test_the_payload_carries_no_copy_of_the_caller_dict() -> None:
    """Mutating the context after building must not change what was sent."""
    from integrations.ticket_webhook import build_payload

    context = {"cve_id": "CVE-2026-1"}
    payload = build_payload(event="e", context=context)
    context["cve_id"] = "CVE-2026-2"

    assert payload["context"]["cve_id"] == "CVE-2026-1"


# ---------------------------------------------------------------------------
# Failure is isolated
# ---------------------------------------------------------------------------


async def test_a_dead_receiver_is_a_retryable_failure(monkeypatch) -> None:
    from integrations import ticket_webhook

    monkeypatch.setenv("TICKET_WEBHOOK_URL", "https://tracker.example/hook")

    async def _boom(*_args, **_kwargs):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx.AsyncClient, "post", _boom)

    with pytest.raises(ticket_webhook.TicketWebhookDeliveryError):
        await ticket_webhook.post_event(event="e", context={})


async def test_a_rejected_document_is_not_retried(monkeypatch) -> None:
    """A receiver that rejects the document rejects it again in ten minutes,
    and the retries would be the only thing in its log."""
    from integrations import ticket_webhook

    monkeypatch.setenv("TICKET_WEBHOOK_URL", "https://tracker.example/hook")

    async def _rejected(*_args, **_kwargs):
        return httpx.Response(422, text="unknown issue type")

    monkeypatch.setattr(httpx.AsyncClient, "post", _rejected)

    with pytest.raises(ticket_webhook.TicketWebhookRejected):
        await ticket_webhook.post_event(event="e", context={})


def test_the_task_swallows_a_rejection_rather_than_failing(monkeypatch) -> None:
    from integrations import ticket_webhook as adapter
    from tasks import ticket_webhook

    async def _rejected(**_kwargs):
        raise adapter.TicketWebhookRejected("422")

    monkeypatch.setattr(ticket_webhook, "post_event", _rejected)

    result = ticket_webhook._run(None, "e", {})

    assert result["status"] == "rejected"


def test_a_broker_that_will_not_take_the_message_is_not_the_callers_problem(
    monkeypatch,
) -> None:
    """A ticket is lost. A scan is not."""
    from tasks import ticket_webhook

    monkeypatch.setenv("TICKET_WEBHOOK_URL", "https://tracker.example/hook")

    def _broker_down(*_args):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ticket_webhook.post_ticket_event_task, "delay", _broker_down)

    assert ticket_webhook.enqueue_ticket_event(event="e", context={}) is False


def test_the_notification_survives_a_ticket_hook_that_explodes(monkeypatch) -> None:
    """The named silent break, from the other side.

    Wiring the integration into the flow means its failure becomes the flow's
    failure. The hook is called for its effect and its exceptions stop here.
    """
    from tasks import notify

    def _explode(**_kwargs):
        raise RuntimeError("integration is on fire")

    monkeypatch.setattr(
        "tasks.ticket_webhook.enqueue_ticket_event", _explode, raising=False
    )

    notify._maybe_raise_a_ticket(kind="new_critical_cve", context={})


async def test_the_token_travels_as_a_bearer_header(monkeypatch) -> None:
    from integrations import ticket_webhook

    monkeypatch.setenv("TICKET_WEBHOOK_URL", "https://tracker.example/hook")
    monkeypatch.setenv("TICKET_WEBHOOK_TOKEN", "s3cret")
    seen: dict[str, object] = {}

    async def _capture(self, url, **kwargs):  # noqa: ANN001
        seen["headers"] = dict(kwargs.get("headers") or {})
        seen["json"] = kwargs.get("json")
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncClient, "post", _capture)

    await ticket_webhook.post_event(event="e", context={"k": "v"})

    assert seen["headers"]["Authorization"] == "Bearer s3cret"  # type: ignore[index]


async def test_no_token_means_no_authorization_header(monkeypatch) -> None:
    """A URL that already carries a secret in its path, which is the shape
    most trackers hand out, needs no second one."""
    from integrations import ticket_webhook

    monkeypatch.setenv("TICKET_WEBHOOK_URL", "https://tracker.example/hook/abc123")
    monkeypatch.delenv("TICKET_WEBHOOK_TOKEN", raising=False)
    seen: dict[str, object] = {}

    async def _capture(self, url, **kwargs):  # noqa: ANN001
        seen["headers"] = dict(kwargs.get("headers") or {})
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncClient, "post", _capture)

    await ticket_webhook.post_event(event="e", context={})

    assert "Authorization" not in seen["headers"]  # type: ignore[operator]


def test_the_notification_path_queues_the_event_and_does_not_post_it(
    monkeypatch,
) -> None:
    """The named silent break for this unit.

    Wiring the post into the producer looks identical from every other test:
    the event still reaches the receiver, the payload is still right, failures
    are still logged. What changes is that a tracker having a slow morning
    becomes a notification that took eleven minutes, and one that is down
    becomes a notification that failed. So this asserts the mechanism rather
    than the outcome: something was queued, and no request left the process.
    """
    from tasks import notify, ticket_webhook

    monkeypatch.setenv("TICKET_WEBHOOK_URL", "https://tracker.example/hook")
    monkeypatch.delenv("TICKET_WEBHOOK_EVENTS", raising=False)
    queued: list[tuple] = []
    monkeypatch.setattr(
        ticket_webhook.post_ticket_event_task,
        "delay",
        lambda *args: queued.append(args),
    )

    async def _must_not_be_called(*_args, **_kwargs):
        raise AssertionError(
            "the producer posted to the tracker inline; it must enqueue instead"
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _must_not_be_called)

    notify._maybe_raise_a_ticket(
        kind="new_critical_cve", context={"cve_id": "CVE-2026-1"}
    )

    assert queued == [("new_critical_cve", {"cve_id": "CVE-2026-1"})]
