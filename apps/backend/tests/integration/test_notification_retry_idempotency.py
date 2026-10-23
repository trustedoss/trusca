# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
``trustedoss.send_notification`` retried and redelivered against the real
Postgres and Redis: one inbox row per event, and ledger keys that expire.

The unit tests in ``tests/unit/tasks/test_notify_retry_idempotency.py`` cover
the per-unit choreography with fakes. This one checks the two things a fake
cannot: that the in-app row really is committed once in ``notifications``,
and that the real Redis keys carry the TTL that lets them go away.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
import redis
from sqlalchemy import func, select

from core.config import redis_url
from core.db import sync_session_scope
from models import Notification
from notifications import NotificationDeliveryError
from notifications import dispatcher as disp
from notifications.delivery_ledger import LEDGER_TTL_SECONDS, email_unit
from tasks import notify as notify_mod
from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def worker_like(monkeypatch):
    """The worker's stack protection and a broker that only records retries.

    See the unit module's docstring for why both are needed.
    """
    from celery.app import trace
    from celery.app.task import Task as BaseTask
    from celery.exceptions import Retry

    already = getattr(BaseTask, "_stackprotected", False)
    trace._install_stack_protection()

    def _retry(*_args: Any, exc: BaseException | None = None, **_kwargs: Any) -> None:
        raise Retry(exc=exc)

    monkeypatch.setattr(notify_mod.send_notification_task, "retry", _retry)
    yield
    if not already:
        BaseTask.__call__ = trace._patched.pop("BaseTask.__call__")
        del BaseTask._stackprotected


def _make_user() -> uuid.UUID:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url
    from tests._helpers import make_user

    async def _build() -> uuid.UUID:
        engine = create_async_engine(database_url(), future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            user = await make_user(s)
            user_id = user.id
        await engine.dispose()
        return user_id

    return asyncio.run(_build())


def _deliver(task_id: str, user_id: uuid.UUID) -> Any:
    task = notify_mod.send_notification_task
    retries = 0
    while True:
        result = task.apply(
            args=[
                "scan_completed",
                {"project_name": "p", "scan_id": "s"},
                ["email"],
                ["owner@example.com"],
            ],
            kwargs={"user_id": str(user_id), "in_app_title": "CVE"},
            task_id=task_id,
            retries=retries,
        )
        if result.state != "RETRY":
            return result
        retries += 1
        assert retries <= task.max_retries


def test_retry_and_redelivery_write_one_inbox_row(worker_like, monkeypatch) -> None:  # noqa: ARG001
    user_id = _make_user()
    task_id = f"it-d3-{uuid.uuid4()}"
    client: Any = redis.Redis.from_url(redis_url(), decode_responses=True)

    # Email because it is on by default in a new user's preferences.
    email_calls: list[list[str]] = []

    async def _email(*, recipients, payload):  # noqa: ARG001
        email_calls.append(list(recipients))
        if len(email_calls) == 1:
            raise NotificationDeliveryError("SMTP delivery failed: timeout")

    monkeypatch.setattr(disp, "_send_email_channel", _email)

    try:
        _deliver(task_id, user_id)
        _deliver(task_id, user_id)  # redelivery of the same message

        assert email_calls == [["owner@example.com"], ["owner@example.com"]]
        with sync_session_scope() as session:
            rows = session.execute(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == user_id)
            ).scalar_one()
        assert rows == 1

        keys = sorted(client.keys(f"trusca:notify:delivery:{task_id}:*"))
        assert keys == [
            f"trusca:notify:delivery:{task_id}:{email_unit('owner@example.com')}",
            f"trusca:notify:delivery:{task_id}:in_app",
            f"trusca:notify:delivery:{task_id}:ticket",
        ]
        for key in keys:
            ttl = client.ttl(key)
            assert 0 < ttl <= LEDGER_TTL_SECONDS
    finally:
        leftover = client.keys(f"trusca:notify:delivery:{task_id}:*")
        if leftover:
            client.delete(*leftover)
        client.close()
