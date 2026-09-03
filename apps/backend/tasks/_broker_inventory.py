# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Which task ids the broker still knows about (ER11).

The stale-scan reaper needs to tell a scan that is legitimately waiting from
one whose broker message no longer exists. Age cannot answer that: a queue
list has no bound, so a genuinely backlogged scan can sit ``queued`` for as
long as the backlog lasts, and killing it would be worse than the leak. The
only honest answer is to ask the broker whether the message is still there.

Two Redis structures hold everything Celery has not finished with, and a task
in either one must not be touched:

* ``LRANGE <queue>`` - messages published and not yet delivered. Each list
  item is a JSON envelope whose ``headers.id`` is the task id (``properties.
  correlation_id`` repeats it).
* ``HGETALL unacked`` - messages delivered to a worker and not yet acked.
  With ``task_acks_late=True`` (celery_app.py) that covers both a task
  reserved behind a busy slot and one actually running. Each value is a JSON
  ``[envelope, exchange, routing_key]`` triple, so the id sits at the same
  ``headers.id`` path one level in.

Both shapes were confirmed against a real broker rather than read off the
documentation, by publishing through Celery and taking delivery through kombu
without acking.

Anything unreadable returns ``None``, never a partial set. A caller that
reaps on absence must treat "I could not look" as "do not reap": a truncated
inventory would mark live scans failed, which is the one outcome worse than
the leak this exists to close.
"""

from __future__ import annotations

import json
from typing import Any

import redis as _redis
import structlog

from core.config import redis_url

log = structlog.get_logger("tasks.broker_inventory")

#: Refuse to enumerate a queue longer than this. A backlog this deep means the
#: broker is busy, not that messages were lost, so the reaper has nothing to do
#: and paying for a huge LRANGE would only slow the pass down.
MAX_QUEUE_SCAN = 10_000


def _task_id_from_envelope(raw: Any) -> str | None:
    """Pull the task id out of one queue item or one ``unacked`` value."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    # `unacked` stores [envelope, exchange, routing_key]; a queue list stores
    # the envelope on its own.
    envelope = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(envelope, dict):
        return None
    headers = envelope.get("headers")
    if isinstance(headers, dict):
        task_id = headers.get("id")
        if isinstance(task_id, str) and task_id:
            return task_id
    properties = envelope.get("properties")
    if isinstance(properties, dict):
        task_id = properties.get("correlation_id")
        if isinstance(task_id, str) and task_id:
            return task_id
    return None


def broker_known_task_ids() -> set[str] | None:
    """Every task id the broker is still holding, or ``None`` if unreadable.

    ``None`` means "could not determine", and is deliberately not the same
    value as an empty set: an empty set says the broker is holding nothing,
    which is exactly the state that licenses a reap.
    """
    from tasks.celery_app import _SCAN_QUEUE, celery_app

    queues = [_SCAN_QUEUE, str(celery_app.conf.task_default_queue)]
    known: set[str] = set()

    try:
        client = _redis.Redis.from_url(redis_url(), decode_responses=True)
        try:
            for queue in queues:
                depth = int(client.llen(queue))  # type: ignore[arg-type]
                if depth > MAX_QUEUE_SCAN:
                    log.info(
                        "broker_inventory_queue_too_deep",
                        queue=queue,
                        depth=depth,
                        limit=MAX_QUEUE_SCAN,
                    )
                    return None
                for raw in client.lrange(queue, 0, -1):  # type: ignore[union-attr]
                    task_id = _task_id_from_envelope(raw)
                    if task_id:
                        known.add(task_id)

            # One hash for the whole broker, not per queue.
            for raw in client.hvals("unacked"):  # type: ignore[union-attr]
                task_id = _task_id_from_envelope(raw)
                if task_id:
                    known.add(task_id)
        finally:
            client.close()  # type: ignore[no-untyped-call]
    except Exception as exc:  # noqa: BLE001 - any broker fault means "unknown"
        log.warning("broker_inventory_unavailable", error=str(exc)[:200])
        return None

    return known


__all__ = ["MAX_QUEUE_SCAN", "broker_known_task_ids"]
