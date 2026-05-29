"""
Scan retention reaper — DT-style ref-keyed retention. Celery Beat (6 hours).

CI/webhook triggers a scan on every PR merge / push. Without retention the
``scans`` table (and its ``scan_components`` / ``vulnerability_findings`` /
``license_findings`` children) grows monotonically forever. This is the DB-side
counterpart to the disk-artifact sweepers (``tasks.scan_source_cleaner`` /
``tasks.workspace_cleaner``).

Model (chosen over Snyk's stateless gate to keep "results live in the UI"):
  Layer 1 — ref-keyed retire (``supersede_prior_ref_scans``, called from the
    scan finalize path): when a scan succeeds it becomes the live snapshot for
    its normalized ref; older succeeded same-ref scans without an explicit
    ``metadata.release`` label are stamped ``superseded_at``.
  Layer 2 — this beat:
    (a) superseded scans past a grace period are hard-deleted (cascade reclaims
        their findings/components/artifacts);
    (b) scans that retire does NOT manage — ref-less succeeded scans (ad-hoc
        triggers) and all failed/cancelled scans — are reclaimed per project by
        keep-last-N + max-age, never touching the live cohort.

Absolute protections (never reclaimed):
  - active scans (queued/running);
  - the project's ``latest_scan_id``;
  - any scan carrying a non-blank string ``metadata.release`` label;
  - the ref-keyed live snapshot (succeeded + ref set + superseded_at NULL) —
    excluded from the keep-last sweep because retire owns it.

Audit (security-reviewer Critical): hard delete leaves no row in
``session.deleted`` (Core ``DELETE``) and the sync session installs no audit
listener, so each reclaim emits an explicit ``AuditLog`` row (system actor =
NULL ``actor_user_id``, ``request_id='scan_retention'``) carrying the scan's
team / ref / cascade-child counts BEFORE the delete commits.

CLAUDE.md compliance:
  - Core rule #3: runs in Celery, never on the request path.
  - Core rule #11: every limit is read via ``os.getenv`` at call time.
  - §5: structlog JSON, one event per reclaimed scan; no payload contents.
  - §6: hard delete relies on DB-level ON DELETE rules (children CASCADE,
    pointers SET NULL) so a bulk DELETE reclaims correctly without an ORM pass.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import String, cast, delete, func, or_, select, update
from sqlalchemy.orm import Session

from core.db import sync_session_scope
from models import (
    AuditLog,
    LicenseFinding,
    Project,
    Scan,
    ScanArtifact,
    ScanComponent,
    VulnerabilityFinding,
)
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.scan_retention")

# Scans in these states are mid-flight — never reclaim them regardless of age.
_ACTIVE_SCAN_STATES: tuple[str, ...] = ("queued", "running")

_CHILD_MODELS: tuple[tuple[str, Any], ...] = (
    ("vulnerability_findings", VulnerabilityFinding),
    ("license_findings", LicenseFinding),
    ("scan_components", ScanComponent),
    ("scan_artifacts", ScanArtifact),
)


# ---------------------------------------------------------------------------
# Policy (os.getenv at call time — CLAUDE.md #11)
# ---------------------------------------------------------------------------


def _superseded_grace_days() -> int:
    """Days a superseded snapshot is kept before hard delete (default 7)."""
    return max(int(os.getenv("SCAN_RETENTION_SUPERSEDED_GRACE_DAYS", "7")), 0)


def _keep_last() -> int:
    """Min number of newest reclaimable scans kept per project (default 30)."""
    return max(int(os.getenv("SCAN_RETENTION_KEEP_LAST", "30")), 0)


def _max_age_days() -> int:
    """Age past which excess reclaimable scans are deleted (default 180)."""
    return max(int(os.getenv("SCAN_RETENTION_MAX_AGE_DAYS", "180")), 0)


# ---------------------------------------------------------------------------
# Shared predicate
# ---------------------------------------------------------------------------


def _release_absent() -> Any:
    """SQL predicate: the scan has no non-blank **string** ``metadata.release``.

    Type-aligned with ``services.scan_service._has_release_label`` (Python
    ``isinstance(release, str)``): a ``release`` value whose JSON type is not
    ``string`` (e.g. a number ``123``) reads as "no label" in BOTH layers, so a
    scan is never protected by the beat yet force-deletable by the manual
    endpoint (or vice-versa). Release-labelled snapshots are immutable — retire
    (Layer 1) and the reclaim sweeps (Layer 2) both skip them.
    """
    label = Scan.scan_metadata["release"]
    return or_(
        label.astext.is_(None),  # key absent
        func.jsonb_typeof(label) != "string",  # present but not a string
        func.btrim(label.astext) == "",  # string but blank
    )


# ---------------------------------------------------------------------------
# Audit (security-reviewer Critical — explicit rows for the hard-delete path)
# ---------------------------------------------------------------------------


def _child_counts_bulk(
    session: Session, scan_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, int]]:
    """``{scan_id: {child_table: count}}`` for the about-to-be-deleted scans.

    One grouped query per child table (4 total), not one per scan. Read BEFORE
    the delete, since the cascade removes the children.
    """
    out: dict[uuid.UUID, dict[str, int]] = {sid: {} for sid in scan_ids}
    for label, model in _CHILD_MODELS:
        rows = session.execute(
            select(model.scan_id, func.count())
            .where(model.scan_id.in_(scan_ids))
            .group_by(model.scan_id)
        ).all()
        counts = {row[0]: int(row[1]) for row in rows}
        for sid in scan_ids:
            out[sid][label] = counts.get(sid, 0)
    return out


def _emit_reclaim_audit(
    session: Session,
    *,
    scan_id: uuid.UUID,
    ref: str | None,
    team_id: uuid.UUID | None,
    reason: str,
    child_counts: dict[str, int],
) -> None:
    """Add a system-actor AuditLog row for one reclaimed scan (pre-commit)."""
    session.add(
        AuditLog(
            action="delete",
            target_table="scans",
            target_id=str(scan_id),
            actor_user_id=None,  # system actor — the retention beat
            team_id=team_id,
            request_id="scan_retention",
            diff={
                "reason": reason,
                "ref": ref,
                "cascade_deleted": child_counts,
            },
        )
    )


# ---------------------------------------------------------------------------
# Layer 1 — ref-keyed retire (called from the scan finalize path, sync session)
# ---------------------------------------------------------------------------


def supersede_prior_ref_scans(
    session: Session,
    *,
    project_id: uuid.UUID,
    winner_scan_id: uuid.UUID,
    ref: str | None,
    now: datetime | None = None,
) -> int:
    """Mark prior succeeded same-ref scans superseded by *winner_scan_id*.

    Invoked when a scan transitions to succeeded. The winner becomes the live
    snapshot for its ref; older succeeded scans for the same (project, ref) that
    carry no explicit ``metadata.release`` label are stamped ``superseded_at`` +
    pointed at the winner. No-op when *ref* is None — an ad-hoc scan has no
    retention key, so the keep-last/max-age sweep reclaims it instead.

    Returns the number of rows superseded. The caller owns the commit (the
    finalize path commits the scan's terminal state in the same transaction).
    Superseding is a reversible UPDATE (not a delete), so it is logged via
    structlog rather than the audit table; the irreversible reclaim is audited.
    """
    if not ref:
        return 0
    now = now or datetime.now(UTC)
    stmt = (
        update(Scan)
        .where(
            Scan.project_id == project_id,
            Scan.ref == ref,
            Scan.id != winner_scan_id,
            cast(Scan.status, String) == "succeeded",
            Scan.superseded_at.is_(None),
            _release_absent(),
        )
        .values(superseded_at=now, superseded_by_scan_id=winner_scan_id)
    )
    result = session.execute(stmt)
    count = int(result.rowcount or 0)
    if count:
        log.info(
            "scan_superseded",
            project_id=str(project_id),
            ref=ref,
            winner_scan_id=str(winner_scan_id),
            superseded_count=count,
        )
    return count


# ---------------------------------------------------------------------------
# Layer 2 — retention beat
# ---------------------------------------------------------------------------


@celery_app.task(name="trustedoss.scan_retention")  # type: ignore[misc]
def scan_retention_task() -> dict[str, Any]:
    """Reclaim superseded + aged-excess scans. Idempotent, safe to re-run.

    Returns ``{"reclaimed_superseded": N, "reclaimed_aged": M}`` for the admin
    disk dashboard.
    """
    structlog.contextvars.bind_contextvars(task_name="scan_retention")
    now = datetime.now(UTC)
    keep_last = _keep_last()
    max_age_days = _max_age_days()
    grace_days = _superseded_grace_days()
    # Log the resolved policy up front so on-call can see what the sweep will do
    # before it runs (security-reviewer Low — config footgun visibility).
    log.info(
        "scan_retention_policy",
        superseded_grace_days=grace_days,
        keep_last=keep_last,
        max_age_days=max_age_days,
    )
    if keep_last == 0 and max_age_days == 0:
        log.warning(
            "scan_retention_aggressive_policy",
            detail=(
                "KEEP_LAST=0 and MAX_AGE_DAYS=0 — every reclaimable (ref-less "
                "succeeded / failed / cancelled) scan will be deleted on this "
                "run; release / live-ref / latest scans remain protected"
            ),
        )

    reclaimed_superseded = 0
    reclaimed_aged = 0
    try:
        with sync_session_scope() as session:
            reclaimed_superseded = _reclaim_superseded(
                session, now=now, grace_days=grace_days
            )
        with sync_session_scope() as session:
            reclaimed_aged = _reclaim_aged(
                session, now=now, keep_last=keep_last, max_age_days=max_age_days
            )
    finally:
        structlog.contextvars.unbind_contextvars("task_name")

    log.info(
        "scan_retention_done",
        reclaimed_superseded=reclaimed_superseded,
        reclaimed_aged=reclaimed_aged,
    )
    return {
        "reclaimed_superseded": reclaimed_superseded,
        "reclaimed_aged": reclaimed_aged,
    }


def _reclaim_superseded(session: Session, *, now: datetime, grace_days: int) -> int:
    """Hard-delete superseded snapshots older than the grace period.

    A superseded scan already lost its ref slot to a newer winner; after the
    grace window it is reclaimed. Children cascade; pointers SET NULL. Each
    reclaimed scan gets an explicit AuditLog row (counts captured pre-delete).
    """
    cutoff = now - timedelta(days=grace_days)
    pre = session.execute(
        select(Scan.id, Scan.ref, Scan.project_id).where(
            Scan.superseded_at.is_not(None),
            Scan.superseded_at < cutoff,
        )
    ).all()
    if not pre:
        return 0

    ids = [row[0] for row in pre]
    counts = _child_counts_bulk(session, ids)
    project_ids = [row[2] for row in pre]
    team_by_project: dict[uuid.UUID, uuid.UUID] = {
        row[0]: row[1]
        for row in session.execute(
            select(Project.id, Project.team_id).where(Project.id.in_(project_ids))
        ).all()
    }

    deleted_ids = {
        row[0]
        for row in session.execute(
            delete(Scan).where(Scan.id.in_(ids)).returning(Scan.id)
        )
    }
    for sid, ref, pid in pre:
        if sid not in deleted_ids:
            continue
        _emit_reclaim_audit(
            session,
            scan_id=sid,
            ref=ref,
            team_id=team_by_project.get(pid),
            reason="superseded",
            child_counts=counts.get(sid, {}),
        )
        log.info("scan_retention_reclaimed", scan_id=str(sid), reason="superseded")
    session.commit()
    return len(deleted_ids)


def _reclaim_aged(
    session: Session, *, now: datetime, keep_last: int, max_age_days: int
) -> int:
    """Reclaim ref-less succeeded + all failed/cancelled scans by keep-last/age.

    Scope is everything retire does NOT manage: succeeded scans without a ref
    (ad-hoc triggers), plus failed/cancelled scans of any ref. Per project the
    newest ``keep_last`` such scans are always kept; among the remainder, those
    older than ``max_age_days`` are deleted.

    The protections are re-asserted INSIDE the DELETE predicate (not just the
    precomputed id list) so a scan that became ``latest`` / was re-triggered
    between the read and the delete is not destroyed (security-reviewer High —
    TOCTOU on the latest pointer). ``is_distinct_from`` is NULL-safe, so a
    project with a NULL ``latest_scan_id`` still deletes correctly.
    """
    age_cutoff = now - timedelta(days=max_age_days)

    candidate_where = (
        cast(Scan.status, String).notin_(_ACTIVE_SCAN_STATES),
        Scan.superseded_at.is_(None),
        _release_absent(),
        # Exclude the ref-keyed live snapshot (succeeded + ref set) — retire owns
        # it. What's left: ref-less succeeded scans + all failed/cancelled scans.
        or_(
            cast(Scan.status, String) != "succeeded",
            Scan.ref.is_(None),
        ),
    )

    project_ids = list(
        session.execute(
            select(Scan.project_id).where(*candidate_where).distinct()
        ).scalars()
    )

    reclaimed = 0
    for pid in project_ids:
        latest = session.execute(
            select(Project.latest_scan_id).where(Project.id == pid)
        ).scalar_one_or_none()
        team_id = session.execute(
            select(Project.team_id).where(Project.id == pid)
        ).scalar_one_or_none()

        rows = session.execute(
            select(Scan.id, Scan.ref, Scan.created_at)
            .where(Scan.project_id == pid, *candidate_where)
            .order_by(Scan.created_at.desc(), Scan.id.desc())
        ).all()

        stale_ids = [
            sid for (sid, _ref, created_at) in rows[keep_last:] if created_at < age_cutoff
        ]
        if not stale_ids:
            continue

        counts = _child_counts_bulk(session, stale_ids)
        # Re-assert every protection atomically in the DELETE so a concurrent
        # trigger/finalize between the read above and here cannot lose a scan
        # that just became latest / live / release-labelled.
        deleted = session.execute(
            delete(Scan)
            .where(
                Scan.id.in_(stale_ids),
                Scan.project_id == pid,
                Scan.created_at < age_cutoff,
                Scan.superseded_at.is_(None),
                _release_absent(),
                cast(Scan.status, String).notin_(_ACTIVE_SCAN_STATES),
                Scan.id.is_distinct_from(latest),
            )
            .returning(Scan.id, Scan.ref)
        ).all()
        for sid, ref in deleted:
            _emit_reclaim_audit(
                session,
                scan_id=sid,
                ref=ref,
                team_id=team_id,
                reason="aged",
                child_counts=counts.get(sid, {}),
            )
            log.info(
                "scan_retention_reclaimed",
                scan_id=str(sid),
                project_id=str(pid),
                reason="aged",
            )
        session.commit()
        reclaimed += len(deleted)

    return reclaimed


__all__ = ["scan_retention_task", "supersede_prior_ref_scans"]
