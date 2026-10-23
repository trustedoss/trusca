# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Per-unit completion record for one notification event.

``trustedoss.send_notification`` retries the whole task when any channel
reports a transient failure, and ``task_acks_late`` redelivers the same
message when a worker dies mid-run. Without a record of what already
happened, every attempt wrote another in-app row, enqueued another ticket
and resent every channel that had already succeeded: one Slack 5xx could
turn into six emails, six inbox rows and six tickets.

The ledger records completion per delivery unit of one event:

  - ``in_app`` and ``ticket``: claimed BEFORE the write (:meth:`claim`) with
    a short TTL, then marked done (full TTL) once the write committed. A
    retry or redelivery inside the claim window skips them; a worker killed
    between the claim and the commit leaves a claim that expires after
    :data:`CLAIM_TTL_SECONDS`, so the acks_late redelivery (which comes
    after the broker visibility timeout, about an hour) writes it then. The
    one remaining duplicate window is a kill after the commit and before the
    mark, which is a few milliseconds.
  - ``slack``, ``teams`` and ``email:<recipient>``: marked AFTER the send
    succeeds (:meth:`mark_done`), so a unit that failed is retried and a
    unit that succeeded is not.

The event is identified by the Celery task id, which stays the same across
``self.retry`` and acks_late redelivery (``request.retries`` does not: a
redelivered message starts again at 0, so it cannot tell a first attempt).

Keys live in Redis with :data:`LEDGER_TTL_SECONDS`, far longer than the
retry window (five retries, backoff capped at 10 minutes) plus the broker
visibility timeout. No schema change is involved.

A Redis error never stops a delivery. The broker is the same Redis, so an
outage long enough to matter also stops retries; reading "not done" and
sending once more is preferable to losing the notification.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol

import structlog

log = structlog.get_logger("notifications.delivery_ledger")

#: Seven days. The longest legitimate gap between two attempts of the same
#: message is the broker visibility timeout (about an hour) plus the retry
#: backoff (minutes), so this leaves a wide margin while still letting the
#: keys expire on their own.
LEDGER_TTL_SECONDS = 7 * 24 * 60 * 60

#: How long a claim holds before the write it guards has committed. Longer
#: than any single write takes, shorter than the visibility timeout after
#: which a killed worker's message is redelivered.
CLAIM_TTL_SECONDS = 5 * 60

_KEY_PREFIX = "trusca:notify:delivery"

UNIT_IN_APP = "in_app"
UNIT_TICKET = "ticket"


def email_unit(recipient: str) -> str:
    """Unit name for one email recipient.

    Case-folded so the same address in different case is one unit, and
    hashed so the Redis keyspace never holds an address in plain text.
    """
    folded = recipient.strip().lower()
    return "email:" + hashlib.sha256(folded.encode("utf-8")).hexdigest()[:32]


class DeliveryLedger(Protocol):
    """What the task and the dispatcher need from a completion record."""

    def is_done(self, unit: str) -> bool: ...

    def mark_done(self, unit: str) -> None: ...

    def claim(self, unit: str, *, ttl_seconds: int = CLAIM_TTL_SECONDS) -> bool: ...

    def release(self, unit: str) -> None: ...


class NullLedger:
    """No record at all: every unit runs. Used when there is no task id
    (direct calls outside Celery), which is the behaviour before the ledger."""

    def is_done(self, unit: str) -> bool:  # noqa: ARG002
        return False

    def mark_done(self, unit: str) -> None:  # noqa: ARG002
        return None

    def claim(self, unit: str, *, ttl_seconds: int = CLAIM_TTL_SECONDS) -> bool:  # noqa: ARG002
        return True

    def release(self, unit: str) -> None:  # noqa: ARG002
        return None


class RedisDeliveryLedger:
    """Ledger for one event, backed by one Redis key per unit."""

    def __init__(self, *, event_id: str, client: Any) -> None:
        self._event_id = event_id
        self._client = client

    def _key(self, unit: str) -> str:
        return f"{_KEY_PREFIX}:{self._event_id}:{unit}"

    def is_done(self, unit: str) -> bool:
        try:
            return bool(self._client.exists(self._key(unit)))
        except Exception as exc:  # noqa: BLE001 (deliver rather than lose it)
            log.warning("notification_ledger_read_failed", unit=unit, error=str(exc)[:200])
            return False

    def mark_done(self, unit: str) -> None:
        try:
            self._client.set(self._key(unit), "1", ex=LEDGER_TTL_SECONDS)
        except Exception as exc:  # noqa: BLE001 (the send already happened)
            log.warning("notification_ledger_write_failed", unit=unit, error=str(exc)[:200])

    def claim(self, unit: str, *, ttl_seconds: int = CLAIM_TTL_SECONDS) -> bool:
        try:
            return bool(self._client.set(self._key(unit), "1", nx=True, ex=ttl_seconds))
        except Exception as exc:  # noqa: BLE001 (deliver rather than lose it)
            log.warning("notification_ledger_claim_failed", unit=unit, error=str(exc)[:200])
            return True

    def release(self, unit: str) -> None:
        try:
            self._client.delete(self._key(unit))
        except Exception as exc:  # noqa: BLE001
            log.warning("notification_ledger_release_failed", unit=unit, error=str(exc)[:200])


def redis_client() -> Any:
    """Client for the ledger. Separate function so tests can substitute one."""
    import redis

    from core.config import redis_url

    return redis.Redis.from_url(
        redis_url(),
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


def ledger_for_event(event_id: str | None) -> DeliveryLedger:
    """Ledger for ``event_id``, or a :class:`NullLedger` when there is none."""
    if not event_id:
        return NullLedger()
    try:
        client = redis_client()
    except Exception as exc:  # noqa: BLE001 (deliver rather than lose it)
        log.warning("notification_ledger_unavailable", error=str(exc)[:200])
        return NullLedger()
    return RedisDeliveryLedger(event_id=event_id, client=client)


__all__ = [
    "CLAIM_TTL_SECONDS",
    "LEDGER_TTL_SECONDS",
    "UNIT_IN_APP",
    "UNIT_TICKET",
    "DeliveryLedger",
    "NullLedger",
    "RedisDeliveryLedger",
    "email_unit",
    "ledger_for_event",
]
