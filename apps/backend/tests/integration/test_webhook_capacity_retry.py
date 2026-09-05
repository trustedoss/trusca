# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Integration tests for the S7 webhook capacity retry
(concurrency-scaling-plan-2026-08-22.md §3.2/§4).

Two layers:

  - HTTP layer (``tests/integration/test_webhooks_github.py`` already pins
    the capacity SKIP itself - 200 + ``skipped_team_at_capacity``, delivery
    row written, no scan). This file adds the one thing that changed: a
    retry task gets scheduled (or does not, when the toggle is off).
  - Task layer: ``_process_capacity_retry`` against a REAL Postgres session,
    driven with plain values (no Celery, no broker) - same shape
    ``tests/integration/test_scan_scheduler.py`` already uses for
    ``services.scan_service``'s sync twins. Covers: capacity clears ->
    enqueue + outcome update; still blocked, attempts remain -> "still_blocked"
    (delivery untouched); still blocked, attempts exhausted -> outcome
    "capacity_retry_exhausted" + a notification dispatch attempted; delivery
    already resolved by something else -> no double-processing; missing
    target -> degrades without raising; toggle disabled mid-flight -> stops.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.db import sync_session_scope
from models import Project, WebhookDelivery
from tests._db_required import migrate_to_head
from tests._helpers import make_organization, make_project, make_scan, make_team, unique_suffix

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_delivery(
    session: AsyncSession,
    *,
    outcome: str,
    with_blocker: bool = True,
) -> tuple[WebhookDelivery, Project]:
    """A project + (optionally) a running scan holding its one capacity slot,
    plus a webhook_deliveries row already recorded with *outcome* - the shape
    every ``_process_capacity_retry`` case starts from."""
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    project = await make_project(session, team=team)
    if with_blocker:
        await make_scan(session, project=project, status="running", ref="release/1.x")

    delivery = WebhookDelivery(
        provider="github",
        delivery_id=f"delivery-{unique_suffix()}",
        event_type="push",
        payload_hash="0" * 64,
        project_id=project.id,
        outcome=outcome,
    )
    session.add(delivery)
    await session.commit()
    await session.refresh(delivery)
    return delivery, project


def _scan_metadata(delivery: WebhookDelivery) -> dict[str, object]:
    return {
        "trigger": "webhook",
        "source": "webhook-github",
        "provider": "github",
        "event_type": "push",
        "delivery_id": delivery.delivery_id,
        "ref": "refs/heads/main",
    }


# ---------------------------------------------------------------------------
# _process_capacity_retry - task-layer decision logic
# ---------------------------------------------------------------------------


async def _read_outcome(db_session: AsyncSession, delivery_id: uuid.UUID) -> WebhookDelivery:
    """Re-read *delivery_id* through db_session with a guaranteed-fresh row.

    ``_process_capacity_retry`` writes through a SEPARATE (sync) session.
    Without ``expire_all()``, ``db_session``'s identity map still holds the
    object ``_seed_delivery`` loaded before that write and
    (``expire_on_commit=False``) never refreshes it on its own, so a plain
    SELECT would return the stale in-memory copy rather than proving the
    write landed. ``expire_all()`` itself must run before anything touches an
    already-loaded ORM attribute (including building the WHERE clause off
    ``delivery.id``) - an expired attribute access outside an awaited context
    raises ``MissingGreenlet`` - so callers pass the plain uuid captured
    before expiry, never the ORM object.
    """
    db_session.expire_all()
    return (
        await db_session.execute(select(WebhookDelivery).where(WebhookDelivery.id == delivery_id))
    ).scalar_one()


async def test_capacity_clearing_enqueues_the_scan_and_updates_outcome(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery, project = await _seed_delivery(
        db_session, outcome="skipped_team_at_capacity", with_blocker=False
    )
    delivery_id, project_id = delivery.id, project.id

    fake_enqueue = MagicMock(return_value="celery-task-fake")
    monkeypatch.setattr("services.scan_service.enqueue_scan", fake_enqueue, raising=False)
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "1")

    from tasks.webhook_capacity_retry import _process_capacity_retry

    with sync_session_scope() as sync_session:
        result = _process_capacity_retry(
            sync_session,
            delivery_id=delivery_id,
            project_id=project_id,
            metadata=_scan_metadata(delivery),
            attempt=0,
        )

    assert result["outcome"] == "enqueued"
    assert result["scan_id"] is not None
    fake_enqueue.assert_called_once()

    refreshed = await _read_outcome(db_session, delivery_id)
    assert refreshed.outcome == "enqueued"
    assert refreshed.enqueued_scan_id is not None


async def test_still_blocked_with_attempts_remaining_leaves_delivery_untouched(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery, project = await _seed_delivery(db_session, outcome="skipped_team_at_capacity")
    delivery_id, project_id = delivery.id, project.id
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "1")

    from tasks.webhook_capacity_retry import _process_capacity_retry

    with sync_session_scope() as sync_session:
        result = _process_capacity_retry(
            sync_session,
            delivery_id=delivery_id,
            project_id=project_id,
            metadata=_scan_metadata(delivery),
            attempt=2,
        )

    assert result == {"outcome": "still_blocked", "reason": "skipped_team_at_capacity"}

    refreshed = await _read_outcome(db_session, delivery_id)
    assert refreshed.outcome == "skipped_team_at_capacity"
    assert refreshed.enqueued_scan_id is None


async def test_still_blocked_at_max_attempts_marks_exhausted_and_notifies(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery, project = await _seed_delivery(db_session, outcome="skipped_team_at_capacity")
    delivery_id, project_id = delivery.id, project.id
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "1")

    fake_send = MagicMock()
    monkeypatch.setattr("tasks.notify.send_notification_task", fake_send)

    from tasks.webhook_capacity_retry import _MAX_RETRY_ATTEMPTS, _process_capacity_retry

    with sync_session_scope() as sync_session:
        result = _process_capacity_retry(
            sync_session,
            delivery_id=delivery_id,
            project_id=project_id,
            metadata=_scan_metadata(delivery),
            attempt=_MAX_RETRY_ATTEMPTS,
        )

    assert result["outcome"] == "capacity_retry_exhausted"

    refreshed = await _read_outcome(db_session, delivery_id)
    assert refreshed.outcome == "capacity_retry_exhausted"

    fake_send.delay.assert_called_once()
    args = fake_send.delay.call_args.args
    assert args[0] == "webhook_capacity_retry_exhausted"
    assert args[1]["delivery_id"] == str(delivery_id)


async def test_a_notification_dispatch_failure_does_not_break_the_exhaustion_write(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery, project = await _seed_delivery(db_session, outcome="skipped_team_at_capacity")
    delivery_id, project_id = delivery.id, project.id
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "1")

    def _boom(*_a, **_k):
        raise RuntimeError("broker unreachable")

    fake_send = MagicMock()
    fake_send.delay.side_effect = _boom
    monkeypatch.setattr("tasks.notify.send_notification_task", fake_send)

    from tasks.webhook_capacity_retry import _MAX_RETRY_ATTEMPTS, _process_capacity_retry

    with sync_session_scope() as sync_session:
        result = _process_capacity_retry(
            sync_session,
            delivery_id=delivery_id,
            project_id=project_id,
            metadata=_scan_metadata(delivery),
            attempt=_MAX_RETRY_ATTEMPTS,
        )

    assert result["outcome"] == "capacity_retry_exhausted"
    refreshed = await _read_outcome(db_session, delivery_id)
    assert refreshed.outcome == "capacity_retry_exhausted"


async def test_already_resolved_delivery_is_left_alone(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manual redelivery (or a racing earlier attempt) already resolved the
    delivery before this attempt ran - re-processing would risk a second
    scan for the same push."""
    delivery, project = await _seed_delivery(
        db_session, outcome="enqueued", with_blocker=False
    )
    delivery_id, project_id = delivery.id, project.id
    fake_enqueue = MagicMock(return_value="celery-task-fake")
    monkeypatch.setattr("services.scan_service.enqueue_scan", fake_enqueue, raising=False)

    from tasks.webhook_capacity_retry import _process_capacity_retry

    with sync_session_scope() as sync_session:
        result = _process_capacity_retry(
            sync_session,
            delivery_id=delivery_id,
            project_id=project_id,
            metadata=_scan_metadata(delivery),
            attempt=0,
        )

    assert result == {"outcome": "already_resolved", "current_outcome": "enqueued"}
    fake_enqueue.assert_not_called()


async def test_missing_delivery_or_project_degrades_without_raising(
    db_session: AsyncSession,
) -> None:
    from tasks.webhook_capacity_retry import _process_capacity_retry

    with sync_session_scope() as sync_session:
        result = _process_capacity_retry(
            sync_session,
            delivery_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            metadata={},
            attempt=0,
        )

    assert result == {"outcome": "target_missing"}


async def test_toggle_disabled_mid_flight_stops_without_touching_the_delivery(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery, project = await _seed_delivery(db_session, outcome="skipped_team_at_capacity")
    delivery_id, project_id = delivery.id, project.id
    monkeypatch.setenv("WEBHOOK_CAPACITY_RETRY_ENABLED", "false")

    from tasks.webhook_capacity_retry import _process_capacity_retry

    with sync_session_scope() as sync_session:
        result = _process_capacity_retry(
            sync_session,
            delivery_id=delivery_id,
            project_id=project_id,
            metadata=_scan_metadata(delivery),
            attempt=0,
        )

    assert result == {"outcome": "disabled"}
    refreshed = await _read_outcome(db_session, delivery_id)
    assert refreshed.outcome == "skipped_team_at_capacity"


# ---------------------------------------------------------------------------
# _translate_result - the Celery wrapper's still_blocked -> retry translation
# ---------------------------------------------------------------------------


def test_translate_result_raises_for_still_blocked() -> None:
    from tasks.webhook_capacity_retry import _translate_result, _WebhookStillAtCapacity

    with pytest.raises(_WebhookStillAtCapacity):
        _translate_result({"outcome": "still_blocked", "reason": "skipped_team_at_capacity"})


@pytest.mark.parametrize(
    "result",
    [
        {"outcome": "enqueued", "scan_id": uuid.uuid4()},
        {"outcome": "skipped_active_scan", "scan_id": None},
        {"outcome": "capacity_retry_exhausted", "reason": "skipped_team_at_capacity"},
        {"outcome": "already_resolved", "current_outcome": "enqueued"},
        {"outcome": "target_missing"},
        {"outcome": "disabled"},
    ],
)
def test_translate_result_passes_through_every_other_outcome(result: dict[str, object]) -> None:
    from tasks.webhook_capacity_retry import _translate_result

    assert _translate_result(result) is result


# ---------------------------------------------------------------------------
# Decorator smoke check - pins the retry envelope itself
# ---------------------------------------------------------------------------


def test_task_is_registered_with_the_expected_retry_envelope() -> None:
    from tasks.webhook_capacity_retry import (
        _MAX_RETRY_ATTEMPTS,
        _RETRY_BACKOFF_MAX_SECONDS,
        RETRY_BACKOFF_BASE_SECONDS,
        _WebhookStillAtCapacity,
        webhook_capacity_retry_task,
    )

    assert webhook_capacity_retry_task.name == "trustedoss.webhook_capacity_retry"
    assert _WebhookStillAtCapacity in tuple(webhook_capacity_retry_task.autoretry_for)
    assert webhook_capacity_retry_task.retry_backoff == RETRY_BACKOFF_BASE_SECONDS
    assert webhook_capacity_retry_task.retry_backoff_max == _RETRY_BACKOFF_MAX_SECONDS
    assert webhook_capacity_retry_task.retry_jitter is True
    assert webhook_capacity_retry_task.max_retries == _MAX_RETRY_ATTEMPTS


# ---------------------------------------------------------------------------
# HTTP layer - the capacity-skip endpoint actually schedules the retry task
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _make_github_project(client: AsyncClient) -> tuple[Project, str]:
    import secrets

    from core.crypto import encrypt_secret

    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        project = await make_project(session, team=team)
        project.git_url = f"https://github.com/acme/widgets-{unique_suffix()}"
        raw = secrets.token_urlsafe(32)
        project.webhook_secret_encrypted = encrypt_secret(raw)
        project.webhook_provider = "github"
        await session.commit()
        await session.refresh(project)
        return project, raw


def _sign(body: bytes, secret: str) -> str:
    import hashlib
    import hmac as hmac_lib

    digest = hmac_lib.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def test_capacity_skip_schedules_a_retry_task(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "1")
    monkeypatch.setenv("WEBHOOK_CAPACITY_RETRY_ENABLED", "true")

    project, secret = await _make_github_project(client)
    factory = await _factory(client)
    async with factory() as session:
        stored = (
            await session.execute(select(Project).where(Project.id == project.id))
        ).scalar_one()
        await make_scan(session, project=stored, status="running", ref="release/1.x")

    captured: dict[str, Any] = {}

    def _fake_apply_async(*, kwargs, countdown):
        captured.update(kwargs=kwargs, countdown=countdown)
        return MagicMock(id="retry-task-id")

    monkeypatch.setattr(
        "tasks.webhook_capacity_retry.webhook_capacity_retry_task.apply_async",
        _fake_apply_async,
    )

    body = json.dumps(
        {
            "ref": "refs/heads/main",
            "repository": {"clone_url": project.git_url, "html_url": project.git_url},
            "pusher": {"name": "octocat"},
        }
    ).encode()
    response = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, secret),
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "skipped_team_at_capacity"

    assert captured["kwargs"]["project_id"] == str(project.id)
    from tasks.webhook_capacity_retry import RETRY_BACKOFF_BASE_SECONDS

    assert captured["countdown"] == RETRY_BACKOFF_BASE_SECONDS


async def test_capacity_skip_does_not_schedule_a_retry_when_the_toggle_is_off(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "1")
    monkeypatch.setenv("WEBHOOK_CAPACITY_RETRY_ENABLED", "false")

    project, secret = await _make_github_project(client)
    factory = await _factory(client)
    async with factory() as session:
        stored = (
            await session.execute(select(Project).where(Project.id == project.id))
        ).scalar_one()
        await make_scan(session, project=stored, status="running", ref="release/1.x")

    fake_apply_async = MagicMock()
    monkeypatch.setattr(
        "tasks.webhook_capacity_retry.webhook_capacity_retry_task.apply_async",
        fake_apply_async,
    )

    body = json.dumps(
        {
            "ref": "refs/heads/main",
            "repository": {"clone_url": project.git_url, "html_url": project.git_url},
            "pusher": {"name": "octocat"},
        }
    ).encode()
    response = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, secret),
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "skipped_team_at_capacity"
    fake_apply_async.assert_not_called()


async def test_a_dispatch_failure_does_not_turn_the_skip_response_into_an_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broker hiccup while scheduling the retry must not cost the Git host
    its 200 - that would turn a capacity skip into an SCM-visible failure the
    host might itself retry, on top of the one this task already schedules."""
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "1")
    monkeypatch.setenv("WEBHOOK_CAPACITY_RETRY_ENABLED", "true")

    project, secret = await _make_github_project(client)
    factory = await _factory(client)
    async with factory() as session:
        stored = (
            await session.execute(select(Project).where(Project.id == project.id))
        ).scalar_one()
        await make_scan(session, project=stored, status="running", ref="release/1.x")

    def _boom(**_k):
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr(
        "tasks.webhook_capacity_retry.webhook_capacity_retry_task.apply_async", _boom
    )

    body = json.dumps(
        {
            "ref": "refs/heads/main",
            "repository": {"clone_url": project.git_url, "html_url": project.git_url},
            "pusher": {"name": "octocat"},
        }
    ).encode()
    response = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, secret),
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "skipped_team_at_capacity"
