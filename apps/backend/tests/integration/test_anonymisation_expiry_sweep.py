# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Integration tests for the global anonymisation-request expiry sweep (#383),
real Postgres.

CLAUDE.md hardening rule #6 exists because two backfill tasks reported work
they never committed. This file runs
``tasks.anonymisation_expiry_sweep.anonymisation_expiry_sweep`` end to end and
asserts on what a SEPARATE session reads afterwards, not on what the task
returned - the exact shape the rule asks for, and the exact bug class #383
is: a lazily-expired table nobody sweeps and nobody is told about.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from core.config import database_url
from models import Notification
from models.user_anonymisation_request import (
    ANONYMISATION_APPROVED,
    ANONYMISATION_EXPIRED,
    ANONYMISATION_PENDING,
    UserAnonymisationRequest,
)
from services.user_anonymisation_service import approve, open_request
from tasks.anonymisation_expiry_sweep import _run_sweep
from tests._db_required import migrate_to_head
from tests._helpers import make_user

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def _inline_notify(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route ``send_notification_task.delay`` through a synchronous ``.apply``
    so the notify task body (prefs filter → in-app INSERT) runs in-line in the
    test process, matching ``tests/integration/test_vuln_sla_sweep_db.py``."""
    import tasks.notify as notify_module

    def _inline(*args: Any, **kwargs: Any) -> None:
        notify_module.send_notification_task.apply(args=args, kwargs=kwargs)

    monkeypatch.setattr(notify_module.send_notification_task, "delay", _inline)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _seed_pending_request(
    *,
    expires_at: datetime,
    requester_is_super_admin: bool = True,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A subject + requester + one pending request, backdated to ``expires_at``.

    Returns ``(request_id, subject_id, requester_id)``.
    """
    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        subject = await make_user(session)
        requester = await make_user(session, is_superuser=requester_is_super_admin)
        row = await open_request(
            session, subject_user_id=subject.id, requested_by_user_id=requester.id
        )
        # Backdate past the TTL directly - the service's TTL is fixed
        # (REQUEST_TTL), so this is the only way to get a genuinely stale row
        # without waiting seven real days.
        row.expires_at = expires_at
        await session.commit()
        out = (row.id, subject.id, requester.id)
    await engine.dispose()
    return out


async def _seed_approved_request(
    *, backdate_expires_at_to: datetime
) -> tuple[uuid.UUID, uuid.UUID]:
    """An APPROVED request whose ``expires_at`` looks stale.

    Approved through the normal (fresh-TTL) path first - ``approve()`` itself
    refuses to approve a request already past its window, so a request cannot
    reach ``approved`` with a stale ``expires_at`` through the service API.
    ``expires_at`` is backdated with a raw UPDATE afterwards, bypassing that
    check, specifically to prove the SWEEP's query predicate does not
    re-derive "expired" from an old timestamp on an ``approved`` row - it
    must gate on ``state == 'pending'`` alone.
    """
    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        subject = await make_user(session)
        requester = await make_user(session, is_superuser=True)
        approver = await make_user(session, is_superuser=True)
        row = await open_request(
            session, subject_user_id=subject.id, requested_by_user_id=requester.id
        )
        approved = await approve(
            session, request_id=row.id, approved_by_user_id=approver.id
        )
        await session.execute(
            update(UserAnonymisationRequest)
            .where(UserAnonymisationRequest.id == approved.id)
            .values(expires_at=backdate_expires_at_to)
        )
        await session.commit()
        out = (approved.id, subject.id)
    await engine.dispose()
    return out


def _reread_state(sync_session: Session, request_id: uuid.UUID) -> str:
    """Read the row from a session distinct from anything the sweep used."""
    sync_session.expire_all()
    row = sync_session.execute(
        select(UserAnonymisationRequest).where(UserAnonymisationRequest.id == request_id)
    ).scalar_one()
    return str(row.state)


def _notifications_for(
    sync_session: Session, user_ids: list[uuid.UUID]
) -> list[Notification]:
    sync_session.expire_all()
    return list(
        sync_session.execute(
            select(Notification).where(
                Notification.user_id.in_(user_ids),
                Notification.kind == "approval_pending",
            )
        ).scalars()
    )


def _stale() -> datetime:
    return datetime.now(UTC) - timedelta(hours=1)


def _fresh() -> datetime:
    return datetime.now(UTC) + timedelta(days=7)


# ---------------------------------------------------------------------------
# Happy path - the transition is actually committed
# ---------------------------------------------------------------------------


def test_sweep_commits_the_expiry_readable_from_a_separate_session(
    sync_session: Session, _inline_notify: None
) -> None:
    request_id, _subject_id, _requester_id = _run(
        _seed_pending_request(expires_at=_stale())
    )

    summary = _run_sweep()

    assert summary["expired"] >= 1
    assert _reread_state(sync_session, request_id) == ANONYMISATION_EXPIRED


def test_sweep_notifies_requester_and_super_admins(
    sync_session: Session, _inline_notify: None
) -> None:
    other_admin = _run(make_user_superadmin())
    request_id, _subject_id, requester_id = _run(
        _seed_pending_request(expires_at=_stale(), requester_is_super_admin=False)
    )

    _run_sweep()

    requester_rows = _notifications_for(sync_session, [requester_id])
    assert len(requester_rows) == 1
    row = requester_rows[0]
    assert row.title == "Anonymisation request expired"
    assert row.target_table == "user_anonymisation_requests"
    assert row.target_id == request_id

    admin_rows = _notifications_for(sync_session, [other_admin])
    assert len(admin_rows) == 1


async def make_user_superadmin() -> uuid.UUID:
    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
        out = admin.id
    await engine.dispose()
    return out


def test_sweep_deduplicates_a_requester_who_is_also_a_super_admin(
    sync_session: Session, _inline_notify: None
) -> None:
    request_id, _subject_id, requester_id = _run(
        _seed_pending_request(expires_at=_stale(), requester_is_super_admin=True)
    )

    _run_sweep()

    rows = _notifications_for(sync_session, [requester_id])
    assert len(rows) == 1, "requester who is also a super-admin gets ONE row, not two"
    assert rows[0].target_id == request_id


# ---------------------------------------------------------------------------
# Negative controls
# ---------------------------------------------------------------------------


def test_sweep_never_touches_an_approved_request(sync_session: Session) -> None:
    """The design invariant this issue must not break: an approved request is
    a commitment two people made and never expires, however old it is."""
    request_id, _subject_id = _run(_seed_approved_request(backdate_expires_at_to=_stale()))

    _run_sweep()

    assert _reread_state(sync_session, request_id) == ANONYMISATION_APPROVED


def test_sweep_leaves_a_fresh_pending_request_alone(sync_session: Session) -> None:
    request_id, _subject_id, _requester_id = _run(
        _seed_pending_request(expires_at=_fresh())
    )

    _run_sweep()

    assert _reread_state(sync_session, request_id) == ANONYMISATION_PENDING


def test_sweep_is_idempotent_on_immediate_rerun(
    sync_session: Session, _inline_notify: None
) -> None:
    """The window IS the dedup here too: a row this tick already flipped to
    ``expired`` no longer matches ``state == pending``, so a second run in the
    same tick enqueues nothing new for it."""
    request_id, _subject_id, requester_id = _run(
        _seed_pending_request(expires_at=_stale())
    )

    first = _run_sweep()
    assert first["expired"] >= 1
    assert _reread_state(sync_session, request_id) == ANONYMISATION_EXPIRED

    second = _run_sweep()

    # This specific row cannot be re-selected: it is no longer pending.
    assert _reread_state(sync_session, request_id) == ANONYMISATION_EXPIRED
    rows_after_rerun = _notifications_for(sync_session, [requester_id])
    assert len(rows_after_rerun) == 1, "re-running must not duplicate the notification"
    # second["expired"] may be > 0 from OTHER tests' leftover stale rows in
    # this shared database, so only this row's un-re-selection is asserted.
    assert second is not None


def test_sweep_with_nothing_stale_enqueues_nothing_for_a_fresh_row(
    sync_session: Session, _inline_notify: None
) -> None:
    request_id, _subject_id, requester_id = _run(
        _seed_pending_request(expires_at=_fresh())
    )

    _run_sweep()

    assert _notifications_for(sync_session, [requester_id]) == []
    assert _reread_state(sync_session, request_id) == ANONYMISATION_PENDING


# ---------------------------------------------------------------------------
# Guard: the shared predicate did not drift when it was split in two
# ---------------------------------------------------------------------------


def test_shared_predicate_still_used_by_the_lazy_per_subject_path(
    sync_session: Session,
) -> None:
    """``services.user_anonymisation_service._select_and_expire_due`` backs
    BOTH this sweep and the lazy ``_expire_stale_for`` used by
    ``open_request``/``approve``. Drive the lazy path directly and confirm it
    still expires a stale row - regression guard for the refactor that
    extracted the sync core (hardening rule #2: one predicate, one owner)."""
    from services.user_anonymisation_service import _expire_stale_for

    async def _drive() -> tuple[int, str]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            subject = await make_user(session)
            requester = await make_user(session)
            row = await open_request(
                session, subject_user_id=subject.id, requested_by_user_id=requester.id
            )
            row.expires_at = _stale()
            await session.commit()

            count = await _expire_stale_for(
                session, subject_user_id=subject.id, now=datetime.now(UTC)
            )
            await session.commit()
            state = (
                await session.execute(
                    select(UserAnonymisationRequest.state).where(
                        UserAnonymisationRequest.id == row.id
                    )
                )
            ).scalar_one()
        await engine.dispose()
        return count, state

    count, state = _run(_drive())
    assert count == 1
    assert state == ANONYMISATION_EXPIRED
