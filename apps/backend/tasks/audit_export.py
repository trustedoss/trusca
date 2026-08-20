# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Celery beat task: ``trustedoss.export_audit_log`` (N17).

Runs on a schedule, takes a batch, posts it, and only then moves the position.
That order is the whole safety property: advancing first would turn one failed
request into a permanent hole, and the hole would be invisible, since the next
run starts past the rows that never arrived.

Off unless a destination is configured, and off means the task returns without
touching the database.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

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


def _run(self: Any) -> dict[str, Any]:
    """Body of the task, callable without Celery's self-injection."""
    url = audit_export_url()
    if url is None:
        return {"status": "skipped", "reason": "not_configured", "exported": 0}

    from core.db import sync_session_scope

    with sync_session_scope() as session:
        cursor = get_or_create_cursor(session, destination=url)
        batch = collect_batch(session, cursor=cursor)
        if batch.is_empty:
            advance_cursor(session, cursor=cursor, batch=batch)
            return {"status": "idle", "exported": 0, "pending": 0}

        status_code = asyncio.run(_post(url, build_body(batch, destination=url)))
        advance_cursor(session, cursor=cursor, batch=batch)
        behind = pending_count(session, cursor=cursor)

    log.info(
        "audit_export_batch_delivered",
        host=_safe_host(url),
        rows=len(batch.rows),
        http_status=status_code,
        pending=behind,
    )
    return {"status": "delivered", "exported": len(batch.rows), "pending": behind}


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
