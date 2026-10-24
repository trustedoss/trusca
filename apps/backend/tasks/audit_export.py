# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Celery beat task: ``trustedoss.export_audit_log`` (N17).

Runs on a schedule, takes a batch, posts it, and only then moves the position.
That order is the whole safety property: advancing first would turn one failed
request into a permanent hole, and the hole would be invisible, since the next
run starts past the rows that never arrived.

One run keeps taking batches until it is caught up or its time budget is spent,
so a large existing trail is handed over in a few runs rather than over weeks.

Only one run works a destination at a time. A retry of a failed run can land
on top of the next beat, and both would otherwise read the same position and
post the same batch. The run takes a session-level advisory lock keyed on the
destination, on a connection of its own in autocommit mode; a second run that
cannot take it returns without reading anything. The lock is not a row lock on
the cursor because a row lock lives inside a transaction, and the post must not:
no transaction is open while the collector is being waited on. Each read and
each move of the position is its own short transaction.

Off unless a destination is configured, and off means the task returns without
touching the database.
"""

from __future__ import annotations

import asyncio
import time
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from core.config import (
    audit_export_token,
    audit_export_url,
    notification_http_timeout_seconds,
)
from services.audit_export_service import (
    advance_cursor,
    build_body,
    collect_batch,
    get_or_create_cursor,
    pending_count,
)
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.audit_export")

#: First half of the advisory lock key, the second being a hash of the
#: destination. The two-integer key space is separate from the single-bigint
#: one the migration lock uses, so the two cannot collide. "AEXP" in ASCII.
_LOCK_CLASS_ID = 0x41455850

#: How long one run keeps taking batches. Below the five-minute beat so a run
#: normally finishes before the next one starts; a run that overlaps anyway
#: is turned away by the lock rather than doubling up.
_RUN_BUDGET_SECONDS = 240.0


class AuditExportDeliveryError(Exception):
    """The collector could not be reached, or refused with a 5xx."""


def _safe_host(url: str) -> str:
    try:
        return httpx.URL(url).host
    except Exception:  # noqa: BLE001
        return "<unparseable>"


async def _post(url: str, body: dict[str, Any]) -> int:
    headers = {"Content-Type": "application/json"}
    token = audit_export_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=notification_http_timeout_seconds()) as client:
            response = await client.post(url, json=body, headers=headers)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise AuditExportDeliveryError(
            f"audit export network failure: {type(exc).__name__}"
        ) from exc

    if response.status_code >= 400:
        # Including 4xx. A collector that rejects a batch is a collector that
        # will keep rejecting it, and the alternative to retrying is skipping
        # the rows, which is the one outcome this task exists to prevent. The
        # export stalls, visibly, until somebody fixes the receiver.
        raise AuditExportDeliveryError(f"audit export rejected: {response.status_code}")
    return int(response.status_code)


def _lock_key(destination: str) -> tuple[int, int]:
    """The advisory lock key for one destination, as two signed int4 values."""
    digest = zlib.crc32(destination.encode("utf-8"))
    signed = digest - (1 << 32) if digest >= (1 << 31) else digest
    return _LOCK_CLASS_ID, signed


def _run_budget_seconds() -> float:
    return _RUN_BUDGET_SECONDS


@contextmanager
def _destination_lock(engine: Engine, destination: str) -> Iterator[bool]:
    """Hold the destination's advisory lock for the block, if it is free.

    Yields False without waiting when another run holds it. The connection is
    in autocommit mode, so holding the lock keeps no transaction open; if the
    worker dies, the server ends the backend and the lock goes with it.
    """
    class_id, object_id = _lock_key(destination)
    with engine.connect() as raw:
        conn = raw.execution_options(isolation_level="AUTOCOMMIT")
        got = bool(
            conn.execute(select(func.pg_try_advisory_lock(class_id, object_id))).scalar()
        )
        try:
            yield got
        finally:
            if got:
                try:
                    conn.execute(select(func.pg_advisory_unlock(class_id, object_id)))
                except Exception:  # noqa: BLE001
                    # A connection that broke has already lost the lock on
                    # the server side, and raising here would replace the
                    # run's own result or error with this one.
                    log.warning("audit_export_unlock_failed", exc_info=True)


def _run(self: Any) -> dict[str, Any]:
    """Body of the task, callable without Celery's self-injection."""
    url = audit_export_url()
    if url is None:
        return {"status": "skipped", "reason": "not_configured", "exported": 0}

    from core.db import get_sync_session_factory, sync_session_scope

    engine = get_sync_session_factory().kw["bind"]
    with _destination_lock(engine, url) as locked:
        if not locked:
            log.info("audit_export_run_already_in_progress", host=_safe_host(url))
            return {"status": "busy", "exported": 0}
        with sync_session_scope() as session:
            return _drain(session, url)


def _drain(session: Session, url: str) -> dict[str, Any]:
    """Deliver batches until caught up or out of time. Caller holds the lock."""
    started = time.monotonic()
    budget = _run_budget_seconds()
    cursor = get_or_create_cursor(session, destination=url)
    exported = 0
    batches = 0
    status = "idle"
    while True:
        batch = collect_batch(session, cursor=cursor)
        # End the read before waiting on the collector. Nothing is written
        # here, and the session keeps its loaded objects across the commit.
        session.commit()
        if batch.is_empty:
            if batches == 0:
                advance_cursor(session, cursor=cursor, batch=batch)
                return {"status": "idle", "exported": 0, "pending": 0}
            break

        try:
            status_code = asyncio.run(_post(url, build_body(batch, destination=url)))
        except AuditExportDeliveryError:
            if exported:
                log.warning(
                    "audit_export_run_interrupted",
                    host=_safe_host(url),
                    exported=exported,
                    batches=batches,
                )
            raise

        if not advance_cursor(session, cursor=cursor, batch=batch):
            # The position moved while this batch was in flight, which only
            # happens if the lock was lost. Stop rather than guess; the run
            # that moved it owns the destination now.
            status = "superseded"
            break
        exported += len(batch.rows)
        batches += 1
        status = "delivered"
        log.info(
            "audit_export_batch_delivered",
            host=_safe_host(url),
            rows=len(batch.rows),
            http_status=status_code,
        )
        if time.monotonic() - started >= budget:
            break

    behind = pending_count(session, cursor=cursor)
    session.commit()
    log.info(
        "audit_export_run_finished",
        host=_safe_host(url),
        status=status,
        exported=exported,
        batches=batches,
        pending=behind,
    )
    return {"status": status, "exported": exported, "batches": batches, "pending": behind}


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.export_audit_log",
    bind=True,
    autoretry_for=(AuditExportDeliveryError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def export_audit_log_task(self: Any) -> dict[str, Any]:
    """Hand the next batch of audit rows to the configured collector."""
    return _run(self)


__all__ = ["AuditExportDeliveryError", "_run", "export_audit_log_task"]
