# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""One row per background task execution.

Seventeen periodic tasks run in this deployment. Five of them record anything
at all, and those five write to singleton sync-state rows that hold only the
most recent tick. So the questions an operator actually asks after an
incident have had no answer: how many times has this failed, how long has it
been failing, what did it delete last night, why did it skip.

The columns come from those questions rather than from what Celery happens to
expose. Attempt count and outcome answer "how often"; the two timestamps
answer "since when" and "how long"; ``result`` answers "how much"; and
``skipped_reason`` answers "why nothing happened".

Names are inherited, not invented. ``celery_task_id`` matches the column
``models.scan.Scan`` already carries for the same value, ``skipped_reason``
matches the sync-state tables, and ``result`` follows the
``detail_code`` / ``detail_params`` pairing that ``admin_health_service``
uses in six places.

Boundaries, so this table does not become a second version of something that
already exists:

Scans are not recorded here. ``scans`` already carries status, timings and
``celery_task_id``, and a scan is user-facing domain data while this table is
operator-facing background bookkeeping. Two homes for one fact means neither
is authoritative.

The sync-state tables stay. They answer "what is the state right now" from a
single row, which is cheaper than scanning history for a max. This table
answers "what happened over time". Both are written in the same transaction so
they cannot disagree.

Scheduled-but-not-yet-run work is not recorded here either. ``task_prerun``
fires when a task starts, so a row exists only for work that actually began.
Pre-writing rows for beat's schedule would make "beat never fired" and "the
task started and died" look identical, and those need different responses.
The admin API composes the schedule with these rows instead.

Unlike ``audit_logs``, this is not evidence about people. It is diagnostic
data with a retention window, and the operational retention sweep deletes
rows past ``TASK_RUN_RETENTION_DAYS``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

# Defined locally rather than imported from a sibling model: the two that
# export these (auth, scan) both import from this package, and taking them
# from either one puts this module inside that cycle. scan.py declares its own
# copies for the same reason.
UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")

#: Terminal outcomes. A row with ``NULL`` here is still running, which is a
#: state rather than a value: the worker was killed before ``task_postrun``
#: could fire, or the task is in flight right now. The admin view shows those
#: as running, and a stale one is itself a finding.
TASK_RUN_OUTCOME_VALUES = ("succeeded", "failed", "skipped")


class TaskRun(Base):
    """A single execution of a background task."""

    __tablename__ = "task_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=GEN_UUID
    )

    #: Registered Celery name, e.g. ``trustedoss.kev_catalog_refresh``.
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)

    #: Celery's id for this execution. Retries keep the same id, which is what
    #: groups the attempts of one dispatch together.
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: The request that dispatched this, when there was one. Beat has none.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: 1 for a first run, 2 for the first retry, and so on. A count column
    #: instead of a row per attempt would lose why the third try failed when
    #: the first two failed differently.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)

    #: Why a run that succeeded technically did no work. Vocabulary lives in
    #: ``models.sync_state``; see ``SKIPPED_REASON_VALUES`` there.
    skipped_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: ``{"detail_code": ..., "detail_params": {...}}``. A code plus arguments
    #: rather than a sentence, so counts can be aggregated and the UI can
    #: render EN and KO without storing prose in the database.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    #: Failure summary, credential-stripped. Never the full traceback: that is
    #: in the logs, and this column is read in a list view.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('succeeded', 'failed', 'skipped')",
            name="ck_task_runs_outcome",
        ),
        CheckConstraint("attempt >= 1", name="ck_task_runs_attempt_positive"),
        # The admin list is "this task, most recent first".
        Index("ix_task_runs_name_started", "task_name", text("started_at DESC")),
        # The retention sweep deletes by age across all tasks.
        Index("ix_task_runs_started_at", "started_at"),
        # Grouping the attempts of one dispatch for the drawer.
        Index("ix_task_runs_celery_task_id", "celery_task_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<TaskRun {self.task_name} attempt={self.attempt} "
            f"outcome={self.outcome}>"
        )
