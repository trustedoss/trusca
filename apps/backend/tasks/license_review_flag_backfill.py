"""
One-shot backfill — populate ``licenses.review_flag`` for the whole catalog (Phase D1).

Background
----------
``licenses.review_flag`` (migration 0036) classifies a catalog row into an
AI-relevant restriction class a human must review — behavioral-use (RAIL /
Llama / Gemma / Falcon community licenses) or non-commercial (CC-BY-NC…). Going
forward, :func:`tasks.scan_source._get_or_create_license` sets and self-heals
the flag every time a scan touches a license row (create + reconcile-on-touch),
so the natural-traffic path keeps flags fresh.

That leaves rows created **before** the classifier existed carrying a NULL
``review_flag`` until the next scan happens to re-touch them. This task is the
operator-triggered floor: it sweeps every ``licenses`` row and reconciles the
flag against :func:`services.license_flags.classify_review_flag` computed from
the row's own ``spdx_id`` / ``name``.

What it does
------------
Walks ``licenses`` in id-ordered batches. For each row it recomputes the flag
and issues an UPDATE only when the stored value differs from the recomputed one
(NULL → flag, flag → NULL after a vocabulary change, or flag → different flag).
Rows already in agreement are skipped — the sweep is a no-op on a healthy
catalog.

Idempotency
-----------
Re-running is a no-op: the second sweep recomputes the same value and finds it
already stored, so no UPDATE is issued. A retry mid-batch resumes from the id
cursor; the reconciliation is row-local so partial progress is durable (each
batch commits independently).

Trigger (not on the Beat schedule — a backfill is a one-time event)::

    docker-compose exec backend celery -A tasks.celery_app call \\
        trustedoss.license_review_flag_backfill

Or from a Python REPL inside the worker container::

    from tasks.license_review_flag_backfill import backfill_license_review_flags
    backfill_license_review_flags.delay()

The result dict is logged (``license_review_flag_backfill_complete``) and
returned to the caller. No notifications are emitted — the operator triggered it.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import sync_session_scope
from models import License as LicenseModel
from services.license_flags import classify_review_flag
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.license_review_flag_backfill")

# Per-batch size — small so a single transaction holds row locks briefly and a
# retry resumes after at most this many rows. Each batch commits independently.
_BATCH_SIZE = 500

# Hard ceiling on per-task wall time. The catalog is small (one row per distinct
# license, low thousands even for large deployments), so this is a generous
# guard, not an expected limit. On exhaustion the task returns the partial
# summary with ``aborted=True`` and the operator re-runs to resume.
_MAX_DURATION_SECONDS = 1800


def _iter_candidate_ids(
    session: Session, *, after_id: uuid.UUID | None, limit: int
) -> list[uuid.UUID]:
    """Up to ``limit`` License ids strictly greater than ``after_id`` (id-ordered).

    The id slice is the cursor; the recompute + compare happens in Python so the
    classifier stays a single source of truth in ``services.license_flags``.
    """
    stmt = select(LicenseModel.id).order_by(LicenseModel.id).limit(limit)
    if after_id is not None:
        stmt = stmt.where(LicenseModel.id > after_id)
    return [row[0] for row in session.execute(stmt).all()]


def _new_summary(*, dry_run: bool) -> dict[str, Any]:
    return {
        "scanned": 0,
        "updated": 0,
        "set_from_null": 0,
        "cleared_to_null": 0,
        "reclassified": 0,
        "duration_seconds": 0.0,
        "aborted": False,
        "dry_run": dry_run,
    }


def _reconcile_row(
    row: LicenseModel, summary: dict[str, Any], *, dry_run: bool
) -> None:
    """Recompute + reconcile one row's ``review_flag`` in place, updating counters.

    Idempotent: a row already in agreement with the classifier is a no-op (no
    counter bump, no write). The single source of truth for the decision is
    :func:`services.license_flags.classify_review_flag` so the task, the
    scan-time upsert, and this backfill can never diverge.
    """
    computed = classify_review_flag(row.spdx_id, row.name)
    if row.review_flag == computed:
        return
    if row.review_flag is None:
        summary["set_from_null"] += 1
    elif computed is None:
        summary["cleared_to_null"] += 1
    else:
        summary["reclassified"] += 1
    summary["updated"] += 1
    if not dry_run:
        row.review_flag = computed


def _sweep_with_session(
    session: Session, summary: dict[str, Any], *, dry_run: bool
) -> None:
    """Single-transaction sweep over the whole catalog on a caller-owned session.

    Used when a ``session`` is injected (integration tests): the caller controls
    the transaction boundary, so we neither open our own connection nor commit —
    which lets the sweep SEE the caller's un-committed seed rows and lets the
    caller assert the mutation before rollback. Production uses the batched,
    independent-session, per-batch-commit path in the task body instead.
    """
    rows = list(session.execute(select(LicenseModel)).scalars().all())
    summary["scanned"] += len(rows)
    for row in rows:
        _reconcile_row(row, summary, dry_run=dry_run)
    if not dry_run:
        # Flush so a subsequent sweep on this same session reads the reconciled
        # values from the identity map (idempotence within one transaction).
        session.flush()


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.license_review_flag_backfill",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def backfill_license_review_flags(
    self: Any, dry_run: bool = False, session: Session | None = None
) -> dict[str, Any]:
    """One-shot backfill — reconcile ``review_flag`` on every ``licenses`` row.

    Args:
        dry_run: When True, walk the catalog and COUNT what would change but
            commit no UPDATE. Default False.
        session: Optional caller-owned session (integration tests). When given,
            the sweep runs in ONE transaction on that session and does not
            commit — so it sees the caller's un-committed seed rows and leaves
            the transaction boundary to the caller. Production omits it and gets
            the batched, independent-session, per-batch-commit path below.

    Returns a summary dict::

        {
            "scanned": int,          # total rows visited
            "updated": int,          # rows whose review_flag changed
            "set_from_null": int,    # NULL → a flag
            "cleared_to_null": int,  # a flag → NULL (vocabulary shrank)
            "reclassified": int,     # flag → different flag
            "duration_seconds": float,
            "aborted": bool,         # True if _MAX_DURATION_SECONDS exhausted
            "dry_run": bool,
        }
    """
    structlog.contextvars.bind_contextvars(
        task_name="license_review_flag_backfill",
        task_id=getattr(self.request, "id", None),
        dry_run=dry_run,
    )

    summary = _new_summary(dry_run=dry_run)
    started = time.monotonic()
    log.info("license_review_flag_backfill_started")

    if session is not None:
        # Test / caller-owned path: single transaction, no commit, sees
        # un-committed rows. No batching or time-limit — the injected session is
        # used for focused reconcile assertions, not a production-scale sweep.
        _sweep_with_session(session, summary, dry_run=dry_run)
        summary["duration_seconds"] = time.monotonic() - started
        log.info("license_review_flag_backfill_complete", **summary)
        return summary

    last_id: uuid.UUID | None = None
    while True:
        elapsed = time.monotonic() - started
        if elapsed > _MAX_DURATION_SECONDS:
            summary["aborted"] = True
            log.warning(
                "license_review_flag_backfill_aborted_time_limit",
                elapsed_seconds=elapsed,
                **{k: v for k, v in summary.items() if k != "duration_seconds"},
            )
            break

        with sync_session_scope() as scoped:
            ids = _iter_candidate_ids(scoped, after_id=last_id, limit=_BATCH_SIZE)
            if not ids:
                break

            if dry_run:
                stmt = select(LicenseModel).where(LicenseModel.id.in_(ids))
            else:
                # Row-lock the batch so a concurrent scan-time upsert cannot race
                # the reconcile. ``skip_locked`` leaves any row a scan is already
                # updating for the next sweep rather than blocking.
                stmt = (
                    select(LicenseModel)
                    .where(LicenseModel.id.in_(ids))
                    .with_for_update(skip_locked=True)
                )
            rows = list(scoped.execute(stmt).scalars().all())
            summary["scanned"] += len(rows)

            for row in rows:
                _reconcile_row(row, summary, dry_run=dry_run)

            if dry_run:
                scoped.rollback()

            last_id = ids[-1]

    summary["duration_seconds"] = time.monotonic() - started
    log.info("license_review_flag_backfill_complete", **summary)
    return summary


__all__ = [
    "backfill_license_review_flags",
    # Exposed for unit tests.
    "_iter_candidate_ids",
    "_reconcile_row",
    "_sweep_with_session",
]
