# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Global anonymisation-request expiry sweep - Celery Beat (#383).

``services.user_anonymisation_service._expire_stale_for`` retires a
subject's stale ``pending`` request, but only lazily: it runs from inside
``open_request`` and ``approve``, and only for the one subject that call is
already touching. A subject nobody opens a new request against and nobody
tries to approve again never triggers either call site, so a ``pending`` row
whose ``expires_at`` has passed can sit in the table forever, still
``pending``, with no queue empty and no health check red. Nothing else in the
system reads this table on a schedule, so nobody is told.

This beat closes that gap the way the lazy checkpoint structurally cannot:
sweep every subject, once a day, and tell somebody. It calls
``services.user_anonymisation_service._select_and_expire_due`` - the exact
same sync core the async ``expire_due_requests`` (used by the lazy
per-subject path) drives through ``AsyncSession.run_sync`` - with
``subject_user_id=None``. One query, one predicate
(``state == 'pending' AND expires_at <= now``), called from both directions;
see that function's docstring for why it is written sync-first (hardening
rule #2: one vocabulary, one owner - a future change to what "due" means
cannot update the lazy path and miss this one, or the reverse).

Only ``pending`` rows are ever in scope here, by construction of the shared
predicate. An ``approved`` request is a decision two people already made and
never expires - see the service module's docstring - so this sweep never
touches one and never needs to distinguish "approved, unexecuted" (that is
``list_awaiting_execution``'s backlog, a different obligation with a
different screen; ``AnonymisationBacklogPanel`` deliberately does not grow a
second column for this).

Idempotency (hardening rule #6/#8)
-----------------------------------
The sweep needs no delivery ledger. A row the query selects is mutated to
``expired`` and committed inside the SAME session before notifications are
built, so a row already flipped by a previous tick (or by a lazy call that
happened to run first) simply no longer matches ``state == 'pending'`` and is
never selected again. Re-running the task back-to-back against an unchanged
table enqueues zero notifications the second time - asserted directly in
``tests/integration/test_anonymisation_expiry_sweep.py``.

Who is told
-----------
For each row that expires:
  - the requester (``requested_by_user_id``) - they opened it and it is their
    outcome to know about, whether or not they still want it;
  - the approver (``approved_by_user_id``), defensively, if the row somehow
    carries one - in the normal lifecycle a ``pending`` row never does (an
    approval moves it to ``approved``, which this sweep never touches), so
    this is a guard against a future state-machine change rather than a path
    exercised by an ordinary run;
  - every active super-admin - they are the only role that can open a fresh
    request or approve one, and an expired request is exactly "somebody needs
    to decide whether this still needs doing".
Recipients are de-duplicated per row (a super-admin who also opened the
request gets one notification, not two).

One in-app notification per (expired request, recipient) - not aggregated
across requests the way the vulnerability SLA sweep aggregates findings per
project. Anonymisation requests are a two-person, security-review-gated
action expected to be rare; a deployment expiring enough of them in one day
for aggregation to matter has a bigger operational problem than notification
volume.

CLAUDE.md compliance:
  - Core rule #3: pure DB read/write + broker enqueues behind a Celery beat.
  - Hardening rule #6: the task owns its own session
    (``core.db.sync_session_scope``) and explicitly commits the expiry
    before returning - this is exactly the ER32 shape (a backfill task that
    only flushed, reported success, and moved nothing). The integration test
    drives the task itself and re-reads the row from a SEPARATE session
    afterwards.
  - §5 logging: structlog JSON; only ids and counts, no PII.

Why the notification ``kind`` is ``approval_pending``, not a new value
-----------------------------------------------------------------------
``Notification.kind`` is a closed, native Postgres ENUM
(``models.notification.NOTIFICATION_KIND_VALUES``), mirrored 1:1 by
``schemas.notification.NotificationKind``, ``tests/contracts/notification-kinds.json``
and the frontend's ``notificationsApi.ts`` - four files two contract-test
suites (one backend, one frontend) pin against each other specifically so a
kind cannot go live on one side without the other (``test_catalog_contracts.py``
tells the story: ``vuln_sla_breach`` shipped before the frontend fixture was
wired to anything, and reached production rendering a raw i18n key). Adding a
fifth value here would need an additive migration (fine on its own) AND a
frontend PR to keep both contract tests green, and this fix is scoped to the
backend (see the PR description). ``approval_pending`` is reused instead: it
has been a valid enum value since the very first notification migration but
has never had a producer (grep confirms it - the model, schema, fixture and
frontend array all already carry it, and the frontend already ships a label
("Approval pending" / "승인 대기"), an icon and a tone for it, unexercised
until now). Reusing it costs no migration, no frontend change and no
contract-test edit, and it is the closest existing category to what this
event actually is: an approval-workflow item that needs a human decision.
The recipient-facing ``title``/``body`` (built in
:func:`_notification_descriptors`, not from the ``kind`` column) say
"expired" explicitly, so nobody reads only the category label. This is a
placeholder until a follow-up PR gives anonymisation expiry its own kind
end-to-end (model + schema + fixture + frontend together, one PR, per the
contract tests' own house rule).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import sync_session_scope
from models import User
from models.user_anonymisation_request import UserAnonymisationRequest
from services.user_anonymisation_service import _select_and_expire_due
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.anonymisation_expiry_sweep")

# Reused, not new - see the module docstring's "Why the notification kind is
# approval_pending" section for why this is not a schema change.
_NOTIFICATION_KIND = "approval_pending"


def _active_super_admin_ids(session: Session) -> list[uuid.UUID]:
    """Every active super-admin - the only role that can act on this table."""
    rows = session.execute(
        select(User.id).where(User.is_active.is_(True), User.is_superuser.is_(True))
    ).all()
    return [r[0] for r in rows]


def _notification_descriptors(
    row: UserAnonymisationRequest, *, super_admin_ids: list[uuid.UUID]
) -> list[dict[str, Any]]:
    """One descriptor per de-duplicated recipient for one expired row."""
    recipients: set[uuid.UUID] = {row.requested_by_user_id}
    if row.approved_by_user_id is not None:
        recipients.add(row.approved_by_user_id)
    recipients.update(super_admin_ids)

    title = "Anonymisation request expired"
    body = (
        f"The anonymisation request for user {row.subject_user_id} expired "
        f"unapproved on {row.expires_at.date().isoformat()}. It can no longer "
        "be approved - open a new request if the erasure is still needed."
    )
    link = "/admin/users"
    context = {
        "request_id": str(row.id),
        "subject_user_id": str(row.subject_user_id),
    }
    return [
        {
            "kind": _NOTIFICATION_KIND,
            "context": context,
            "user_id": str(user_id),
            "title": title,
            "body": body,
            "link": link,
            "request_id": str(row.id),
        }
        for user_id in recipients
    ]


def _run_sweep() -> dict[str, Any]:
    """Body of the beat task (testable without Celery's task machinery)."""
    summary: dict[str, Any] = {"expired": 0, "notifications_enqueued": 0}
    now = datetime.now(UTC)

    # Descriptors are collected inside the session and enqueued AFTER it
    # closes (rematch-beat / vuln-sla-sweep precedent: a slow broker must not
    # extend the DB transaction window). The expiry itself is committed
    # BEFORE that enqueue step, so a broker hiccup after this point can only
    # cost a notification, never the state transition - the transition is
    # already durable (hardening rule #6).
    to_enqueue: list[dict[str, Any]] = []

    with sync_session_scope() as session:
        expired_rows = _select_and_expire_due(session, now=now)
        summary["expired"] = len(expired_rows)
        if not expired_rows:
            return summary

        session.commit()

        super_admin_ids = _active_super_admin_ids(session)
        for row in expired_rows:
            to_enqueue.extend(
                _notification_descriptors(row, super_admin_ids=super_admin_ids)
            )

    summary["notifications_enqueued"] = _enqueue_notifications(to_enqueue)
    log.info(
        "anonymisation_expiry_sweep_complete",
        expired=summary["expired"],
        notifications_enqueued=summary["notifications_enqueued"],
    )
    return summary


def _enqueue_notifications(descriptors: list[dict[str, Any]]) -> int:
    """One ``trustedoss.send_notification`` per (request × recipient) descriptor.

    ``channels=[]`` - in-app only, matching ``tasks.vuln_sla_sweep``: the
    notify task's prefs filter writes the in-app row (iff ``in_app_enabled``)
    and short-circuits on the empty channel list. Late import + per-descriptor
    exception swallow so a broker hiccup never fails the whole sweep (the
    expiry itself already committed by the time this runs).
    """
    from tasks.notify import send_notification_task

    enqueued = 0
    for d in descriptors:
        try:
            send_notification_task.delay(
                d["kind"],
                d["context"],
                [],  # channels - in-app only
                [],  # recipients
                user_id=d["user_id"],
                in_app_title=d["title"],
                in_app_body=d["body"],
                in_app_link=d["link"],
                in_app_target_table="user_anonymisation_requests",
                in_app_target_id=d["request_id"],
            )
            enqueued += 1
        except Exception as exc:  # noqa: BLE001 - broker failure is per-descriptor
            log.warning(
                "anonymisation_expiry_sweep_notification_dispatch_failed",
                kind=d.get("kind"),
                request_id=d.get("request_id"),
                error=str(exc)[:300],
            )
    return enqueued


@celery_app.task(name="trustedoss.anonymisation_expiry_sweep")  # type: ignore[misc]
def anonymisation_expiry_sweep() -> dict[str, Any]:
    """Daily beat entry - see the module docstring for the selection contract.

    Never raises: any unexpected failure degrades to a skip summary + WARNING
    so the beat stays healthy (rematch-beat / vuln-sla-sweep convention).
    """
    structlog.contextvars.bind_contextvars(task_name="anonymisation_expiry_sweep")
    try:
        return _run_sweep()
    except Exception as exc:  # noqa: BLE001 - beat task must not raise
        log.warning(
            "anonymisation_expiry_sweep_unexpected_error",
            error=str(exc)[:300],
        )
        return {"expired": 0, "notifications_enqueued": 0}
    finally:
        structlog.contextvars.unbind_contextvars("task_name")


__all__ = [
    "anonymisation_expiry_sweep",
    "_active_super_admin_ids",
    "_enqueue_notifications",
    "_notification_descriptors",
    "_run_sweep",
]
