# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Batch request and result shapes: register many projects, trigger many scans.

Onboarding an organization means creating a project per repository. Doing that
one HTTP call at a time is what the bench tooling already works around, and an
organization with three hundred repositories will not fill a form three hundred
times.

The interesting part of a batch is not the happy path, it is what a partial
failure says. Three properties, each chosen against a defect this codebase has
already shipped once:

**A partly-failed batch does not report success.** ``all_succeeded`` sits at the
top level and the endpoint answers 207 rather than 201 when anything failed, so
a caller learns the outcome from the status line and one boolean instead of by
walking the rows. Scans that found nothing, backfills that wrote nothing and
gates that passed on absent data have all shipped here reporting success; a
batch whose failures are visible only inside the body would be the same shape
again.

**Every row says why.** ``forbidden``, ``already_exists``, ``rate_limited`` and
``invalid`` ask different things of the caller: get access, do nothing, retry
later, fix the input. Twelve failures out of three hundred is not actionable
without that split.

**An existing row is not a failure.** Re-running a batch is the normal way to
finish an interrupted onboarding, and on the second run most rows already
exist. Counting those as failures would make every re-run report failure and
render ``all_succeeded`` meaningless, so ``already_exists`` counts as success
and is reported separately from ``created`` (a caller re-running needs to see
that nothing changed).
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.scan import ProjectCreate

#: How a single row of a batch turned out.
#:
#: ``created`` and ``already_exists`` are both successes: the requested state
#: holds afterwards, which is what an idempotent caller asked for. The other
#: three are failures, split by what the caller has to do about them.
BatchRowStatus = Literal[
    "created",
    "already_exists",
    "forbidden",
    "invalid",
    "rate_limited",
]

#: The subset that counts toward ``succeeded``. Kept as data rather than an
#: ``in`` test written at each call site so the classification lives in one
#: place; ``test_batch_contracts.py`` asserts the two sets partition the
#: vocabulary.
BATCH_SUCCESS_STATUSES: frozenset[str] = frozenset({"created", "already_exists"})

BATCH_FAILURE_STATUSES: frozenset[str] = frozenset({"forbidden", "invalid", "rate_limited"})

#: Largest batch accepted in one request.
#:
#: Each row is its own SAVEPOINT inside one transaction, so the cost is bounded
#: by how long that transaction may hold its locks, not by the row count alone.
#: Two hundred keeps a full batch well inside the request timeout while still
#: covering the common case in one or two calls.
MAX_BATCH_SIZE = 200


class ProjectBatchCreate(BaseModel):
    """Inbound payload for ``POST /v1/projects:batch``."""

    model_config = ConfigDict(extra="forbid")

    projects: list[ProjectCreate] = Field(
        min_length=1,
        max_length=MAX_BATCH_SIZE,
        description=(
            f"Projects to create, at most {MAX_BATCH_SIZE}. Each entry is the "
            "same body `POST /v1/projects` takes. Rows are processed in order "
            "and independently: a row that fails does not undo the rows before "
            "it."
        ),
    )


class ScanBatchCreate(BaseModel):
    """Inbound payload for ``POST /v1/scans:batch``."""

    model_config = ConfigDict(extra="forbid")

    project_ids: list[uuid.UUID] = Field(
        min_length=1,
        max_length=MAX_BATCH_SIZE,
        description=(
            f"Projects to scan, at most {MAX_BATCH_SIZE}. The team's concurrent-"
            "scan cap still applies and is re-counted per row, so a batch "
            "starts scans up to the cap and reports the rest as "
            "`rate_limited` rather than queueing past it."
        ),
    )
    ref: str | None = Field(
        default=None,
        max_length=255,
        description="Git ref to scan, applied to every row. Omit for the default branch.",
    )


class BatchRowResult(BaseModel):
    """What happened to one row, in the order it was sent."""

    model_config = ConfigDict(from_attributes=True)

    index: int = Field(
        ge=0,
        description=(
            "0-based position in the request array. The only stable way to "
            "match a result back to what was sent, since a rejected row may "
            "have no id."
        ),
    )
    status: BatchRowStatus
    project_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "The project, when one exists. Populated for `already_exists` too, "
            "so a re-run can pick up ids it did not record the first time."
        ),
    )
    scan_id: uuid.UUID | None = Field(
        default=None,
        description="The scan started for this row. `null` outside scan batches.",
    )
    detail: str | None = Field(
        default=None,
        description=(
            "Why, for a row that is not `created`. Written for a person "
            "reading a CI log; branch on `status`, not on this text."
        ),
    )
    retry_after_seconds: int | None = Field(
        default=None,
        ge=0,
        description=(
            "For `rate_limited` rows: roughly how long until a slot frees. "
            "Best-effort, from the same estimate the single-scan 429 carries."
        ),
    )


class BatchResult(BaseModel):
    """The envelope both batch endpoints return.

    Read ``all_succeeded`` first. It is the whole point of the shape: a caller
    that only checks the status code sees 207 and stops, and a caller that
    reads one field learns the same thing without walking ``rows``.
    """

    model_config = ConfigDict(from_attributes=True)

    all_succeeded: bool = Field(
        description=(
            "True when every row succeeded (`created` or `already_exists`). "
            "The endpoint answers 201 when this is true and 207 when it is "
            "not, so a script can branch on either."
        ),
    )
    total: int = Field(ge=0, description="Rows submitted.")
    created: int = Field(ge=0, description="Rows that created something new.")
    already_existed: int = Field(
        ge=0,
        description=(
            "Rows whose target was already there. Counted as success. A re-run "
            "that changes nothing reports `created: 0` with this equal to "
            "`total`, which is how a caller knows the earlier run finished."
        ),
    )
    failed: int = Field(ge=0, description="Rows that did not succeed.")
    failed_by_status: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Failure counts keyed by row status, so a caller sees at a glance "
            "how many need access, how many were rejected and how many are "
            "worth retrying. Only non-zero entries appear."
        ),
    )
    rows: list[BatchRowResult] = Field(description="One entry per submitted row, in request order.")


__all__ = [
    "BATCH_FAILURE_STATUSES",
    "BATCH_SUCCESS_STATUSES",
    "MAX_BATCH_SIZE",
    "BatchResult",
    "BatchRowResult",
    "BatchRowStatus",
    "ProjectBatchCreate",
    "ScanBatchCreate",
]
