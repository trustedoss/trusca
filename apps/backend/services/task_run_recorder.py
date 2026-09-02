# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Write a ``task_runs`` row for each background task execution.

Called from the Celery signal handlers in ``tasks.log_context``. Kept out of
that module because the handlers are about log context and this is about
persistence; mixing them would mean a database failure could take the logging
context down with it.

Every function here swallows its own errors. Recording that a sweep ran is
worth less than the sweep running, so a database hiccup degrades the history
rather than failing the work. The failure is logged, which is the one place
that cannot itself be conditional on the database.

How a task reports its outcome
------------------------------

Nothing is required of a task. If it returns nothing useful the row still
carries name, timing, attempt and success or failure, which answers most of
the questions on its own.

A task that wants to say more returns a dict, and two keys are read from it:

``skipped_reason``
    Turns the outcome into ``skipped``. The task ran but did no work, which
    is neither success nor failure and reads wrongly as either. The
    vocabulary is ``models.sync_state.SKIPPED_REASON_VALUES``.

``detail_code`` / ``detail_params``
    What happened, as a code and its arguments rather than a sentence, so
    counts aggregate and the UI renders EN and KO without prose in the
    database. Same shape ``admin_health_service`` already uses.

Anything else in the returned dict is ignored. The row is a summary, not a
copy of the task's return value: some of those carry snapshots measured in
megabytes.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import update

from core.db import sync_session_scope
from models.task_run import TaskRun
from services.admin_disk_service import _strip_credentials

log = structlog.get_logger("services.task_run_recorder")

#: Longest error summary stored. The full traceback is in the logs; this
#: column is read in a list view where a wall of text is worse than a clue.
_ERROR_MAX_CHARS = 500


def task_run_retention_days() -> int:
    """Age (days) past which a task-run row is reclaimed (default 90).

    Ninety matches the audit log's window, which is the closest existing
    precedent for "operational history an admin might look back through".
    Read at call time rather than cached (CLAUDE.md rule 11).
    """
    return max(int(os.getenv("TASK_RUN_RETENTION_DAYS", "90")), 0)


def record_start(
    *,
    task_name: str,
    celery_task_id: str | None,
    request_id: str | None,
    attempt: int,
) -> None:
    """Insert the row for a run that has just begun.

    The row is deliberately written before the work rather than after it. A
    task killed mid-flight leaves a row with no ``finished_at``, and that gap
    is the only evidence such a death leaves behind.
    """
    try:
        with sync_session_scope() as session:
            session.add(
                TaskRun(
                    task_name=task_name,
                    celery_task_id=celery_task_id,
                    request_id=request_id,
                    attempt=max(attempt, 1),
                    started_at=datetime.now(UTC),
                )
            )
            # sync_session_scope does not commit on exit: the scan pipeline
            # mixes intermediate and terminal commits, so the helper leaves
            # the decision to the caller. Forgetting it here writes nothing
            # and raises nothing.
            session.commit()
    except Exception as exc:  # noqa: BLE001 - history must not fail the task
        log.warning(
            "task_run.record_start_failed", task_name=task_name, error=str(exc)
        )


def record_finish(
    *,
    celery_task_id: str | None,
    attempt: int,
    outcome: str,
    retval: Any = None,
    error: BaseException | None = None,
) -> None:
    """Close the row this execution opened.

    Matched on ``(celery_task_id, attempt)`` rather than on an id held in
    memory: the handler that opened the row and the one closing it are
    separate signal callbacks, and a task that retried has several rows under
    the same Celery id.
    """
    try:
        fields: dict[str, Any] = {
            "finished_at": datetime.now(UTC),
            "outcome": outcome,
        }
        if error is not None:
            fields["error"] = _summarise_error(error)

        summary = _summary_fields(retval)
        fields.update(summary)
        if summary.get("skipped_reason"):
            fields["outcome"] = "skipped"

        with sync_session_scope() as session:
            session.execute(
                update(TaskRun)
                .where(
                    TaskRun.celery_task_id == celery_task_id,
                    TaskRun.attempt == attempt,
                    TaskRun.finished_at.is_(None),
                )
                .values(**fields)
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001 - history must not fail the task
        log.warning("task_run.record_finish_failed", error=str(exc))


def _summarise_error(error: BaseException) -> str:
    """Class name plus message, credential-stripped and length-capped.

    The message is kept because the class alone rarely identifies the cause,
    but an upstream error string can carry a URL with a password in it, so it
    goes through the same stripping the disk service uses.
    """
    text = f"{type(error).__name__}: {error}"
    return _strip_credentials(text)[:_ERROR_MAX_CHARS]


def _summary_fields(retval: Any) -> dict[str, Any]:
    """Pull the two recognised keys out of a task's return value.

    Returns an empty mapping for anything that is not a dict, which covers
    every task that returns None or a bare count.
    """
    if not isinstance(retval, dict):
        return {}

    fields: dict[str, Any] = {}

    reason = retval.get("skipped_reason")
    if isinstance(reason, str) and reason:
        fields["skipped_reason"] = reason[:64]

    detail_code = retval.get("detail_code")
    if isinstance(detail_code, str) and detail_code:
        result: dict[str, Any] = {"detail_code": detail_code}
        params = retval.get("detail_params")
        if isinstance(params, dict):
            result["detail_params"] = params
        fields["result"] = result

    return fields
