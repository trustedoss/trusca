# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Creating many projects, and starting many scans, in one request.

The contract is in ``schemas.batch``. This module is the part that has to be
careful, because a batch of three hundred rows is a place where a partial
failure is normal and where getting the transaction boundary wrong is silent.

**A failed row does not undo the rows before it.** ``create_project`` and
``trigger_scan`` each commit their own row and roll back only their own failure
(that is where the slug-conflict and active-scan-conflict translations happen),
so a row is already the transaction boundary and the loop simply continues past
a failure. Rolling the whole batch back on the first failure would mean one
repository the caller lacks access to costs them the other 299, which is the
opposite of why a batch exists.

Wrapping each row in a SAVEPOINT was the first design here and it does not
work: the inner ``commit()`` closes the enclosing transaction the savepoint
belongs to, and the next ``nested.commit()`` raises ``ResourceClosedError``. The
integration tests caught that on the first run. Adding a no-commit variant of
each service would be the alternative, and it would mean two code paths through
project creation, which is a worse trade for a caller that wants per-row
outcomes anyway.

One consequence worth stating: because each row commits as it goes, a batch
interrupted halfway leaves the rows it had finished. That is the behaviour a
re-run depends on, and it is why an existing row reports ``already_exists``
rather than an error.
"""

from __future__ import annotations

from collections import Counter

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import CurrentUser
from models import Project
from schemas.batch import (
    BATCH_SUCCESS_STATUSES,
    BatchResult,
    BatchRowResult,
    ProjectBatchCreate,
    ScanBatchCreate,
)
from schemas.scan import ScanCreate
from services.project_service import (
    ProjectError,
    ProjectForbidden,
    ProjectSlugConflict,
    create_project,
)
from services.scan_service import (
    ConcurrentScanLimitExceeded,
    ScanError,
    ScanForbidden,
    ScanInProgressConflict,
    trigger_scan,
)

log = structlog.get_logger(__name__)


def _summarise(rows: list[BatchRowResult]) -> BatchResult:
    """Fold row outcomes into the envelope's top-level counts.

    ``all_succeeded`` is computed from the rows rather than tracked alongside
    them: a flag maintained in the loop can drift from what the rows say, and
    that flag is the one field a caller is most likely to trust without
    reading further.
    """
    by_status = Counter(row.status for row in rows)
    failures = {
        status: count for status, count in by_status.items() if status not in BATCH_SUCCESS_STATUSES
    }
    return BatchResult(
        all_succeeded=not failures,
        total=len(rows),
        created=by_status.get("created", 0),
        already_existed=by_status.get("already_exists", 0),
        failed=sum(failures.values()),
        failed_by_status=dict(sorted(failures.items())),
        rows=rows,
    )


async def create_projects_batch(
    session: AsyncSession,
    *,
    payload: ProjectBatchCreate,
    actor: CurrentUser,
) -> BatchResult:
    """Create every project in ``payload``, reporting each row's outcome.

    Rows are independent. A row that the actor may not create, or that names a
    slug the team already uses, is recorded and the batch continues.
    """
    rows: list[BatchRowResult] = []

    for index, spec in enumerate(payload.projects):
        try:
            project = await create_project(session, payload=spec, actor=actor)
        except ProjectSlugConflict:
            # Not a failure: the requested state holds. Re-running a batch to
            # finish an interrupted onboarding hits this for most rows, and
            # counting it as failure would make every re-run look broken.
            existing = (
                await session.execute(
                    select(Project).where(
                        Project.team_id == spec.team_id,
                        Project.slug == spec.slug,
                    )
                )
            ).scalar_one_or_none()
            rows.append(
                BatchRowResult(
                    index=index,
                    status="already_exists",
                    project_id=existing.id if existing is not None else None,
                    detail=f"team already has a project with slug {spec.slug!r}",
                )
            )
        except ProjectForbidden as exc:
            rows.append(BatchRowResult(index=index, status="forbidden", detail=str(exc)))
        except ProjectError as exc:
            # Every other domain error from the same service. Deliberately not
            # a bare `except`: an IntegrityError we did not anticipate, or a
            # dropped connection, must not be filed as this row being invalid
            # while the real cause disappears into a per-row detail string.
            rows.append(BatchRowResult(index=index, status="invalid", detail=str(exc)))
        else:
            rows.append(BatchRowResult(index=index, status="created", project_id=project.id))

    result = _summarise(rows)
    log.info(
        "projects.batch_created",
        actor_id=str(actor.id),
        total=result.total,
        created=result.created,
        already_existed=result.already_existed,
        failed=result.failed,
    )
    return result


async def trigger_scans_batch(
    session: AsyncSession,
    *,
    payload: ScanBatchCreate,
    actor: CurrentUser,
) -> BatchResult:
    """Start a scan for each project, honouring the team's concurrency cap.

    The cap is re-counted per row against the team's live active-scan total, so
    a batch starts scans up to the cap and reports the remainder as
    ``rate_limited``. It is not a way around the cap and is not meant to be:
    the cap protects the shared worker pool, and queueing past it would move
    the load rather than shed it.
    """
    rows: list[BatchRowResult] = []

    for index, project_id in enumerate(payload.project_ids):
        try:
            scan = await trigger_scan(
                session,
                project_id=project_id,
                # `ref` rides inside the metadata blob, the same shape the
                # single-scan endpoint sends, so the batch cannot drift from
                # what an ordinary trigger does.
                payload=ScanCreate(metadata={"ref": payload.ref} if payload.ref else {}),
                actor=actor,
            )
        except ConcurrentScanLimitExceeded as exc:
            rows.append(
                BatchRowResult(
                    index=index,
                    status="rate_limited",
                    project_id=project_id,
                    detail=str(exc),
                    retry_after_seconds=exc.estimated_wait_seconds,
                )
            )
        except ScanInProgressConflict:
            # A scan is already queued or running for this project, which is
            # the state the caller asked for. Same reasoning as a slug
            # conflict: re-running must not report failure.
            rows.append(
                BatchRowResult(
                    index=index,
                    status="already_exists",
                    project_id=project_id,
                    detail="a scan is already queued or running for this project",
                )
            )
        except ScanForbidden as exc:
            rows.append(
                BatchRowResult(
                    index=index,
                    status="forbidden",
                    project_id=project_id,
                    detail=str(exc),
                )
            )
        except ScanError as exc:
            rows.append(
                BatchRowResult(
                    index=index,
                    status="invalid",
                    project_id=project_id,
                    detail=str(exc),
                )
            )
        else:
            rows.append(
                BatchRowResult(
                    index=index,
                    status="created",
                    project_id=project_id,
                    scan_id=scan.id,
                )
            )

    result = _summarise(rows)
    log.info(
        "scans.batch_triggered",
        actor_id=str(actor.id),
        total=result.total,
        created=result.created,
        rate_limited=result.failed_by_status.get("rate_limited", 0),
        failed=result.failed,
    )
    return result


__all__ = ["create_projects_batch", "trigger_scans_batch"]
