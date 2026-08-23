"""
Integration tests for W9 (concurrency-scaling-plan-2026-08-22.md §3.5, §4)
auth-token retention: ``refresh_tokens`` / ``password_reset_tokens`` rows are
reclaimed once past ``expires_at`` plus their grace period, and NOT before,
regardless of whether the row was ever used or revoked.

Covers the regression contract from the plan's §4 W9 row, applied to these
two tables: what gets deleted and what gets kept is explicit, parametrized
across the expiry boundary.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from models import PasswordResetToken, RefreshToken, User

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _sync_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping auth token retention tests")
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic upgrade failed: {result.stderr[-400:]}")


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(_sync_url(), future=True)
    factory = sessionmaker(bind=engine, future=True)
    with factory() as s:
        yield s
    engine.dispose()


def _seed_user(session: Session) -> uuid.UUID:
    suffix = uuid.uuid4().hex[:10]
    user = User(email=f"w9-{suffix}@example.com", hashed_password="x")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user.id


def _add_refresh_token(
    session: Session,
    *,
    user_id: uuid.UUID,
    expires_at: datetime,
    revoked_at: datetime | None = None,
    revoked_reason: str | None = None,
) -> uuid.UUID:
    row = RefreshToken(
        user_id=user_id,
        jti=uuid.uuid4().hex,
        token_hash=uuid.uuid4().hex,
        expires_at=expires_at,
        revoked_at=revoked_at,
        revoked_reason=revoked_reason,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.id


def _add_password_reset_token(
    session: Session,
    *,
    user_id: uuid.UUID,
    expires_at: datetime,
    used_at: datetime | None = None,
) -> uuid.UUID:
    row = PasswordResetToken(
        user_id=user_id,
        token_hash=uuid.uuid4().hex,
        expires_at=expires_at,
        used_at=used_at,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.id


def _refresh_token_exists(session: Session, token_id: uuid.UUID) -> bool:
    return (
        session.execute(select(RefreshToken.id).where(RefreshToken.id == token_id)).first()
        is not None
    )


def _password_reset_token_exists(session: Session, token_id: uuid.UUID) -> bool:
    return (
        session.execute(
            select(PasswordResetToken.id).where(PasswordResetToken.id == token_id)
        ).first()
        is not None
    )


# ---------------------------------------------------------------------------
# refresh_tokens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age_past_expiry_days", "should_be_deleted"),
    [
        (10, True),  # well past expires_at + default 1-day grace
        (0.5, False),  # expired, but still inside the 1-day grace window
        (-1, False),  # not yet expired at all
    ],
)
def test_refresh_token_deleted_only_past_expiry_plus_grace(
    session: Session, age_past_expiry_days: float, should_be_deleted: bool
) -> None:
    from tasks.auth_token_retention import auth_token_retention_task

    user_id = _seed_user(session)
    now = datetime.now(UTC)
    expires_at = now - timedelta(days=age_past_expiry_days)
    token_id = _add_refresh_token(session, user_id=user_id, expires_at=expires_at)

    auth_token_retention_task()

    exists = _refresh_token_exists(session, token_id)
    assert exists is (not should_be_deleted)


def test_revoked_refresh_token_survives_until_its_original_expiry(session: Session) -> None:
    """Regression contract: rotation/logout/reuse-detection does NOT move
    expires_at, so a revoked row is deleted by the SAME predicate as an
    unrevoked one, not sooner. A revoked row inside its TTL window must
    survive the sweep so reuse detection still has it to compare against.
    """
    from tasks.auth_token_retention import auth_token_retention_task

    user_id = _seed_user(session)
    now = datetime.now(UTC)
    token_id = _add_refresh_token(
        session,
        user_id=user_id,
        expires_at=now + timedelta(days=5),  # still well inside its 7-day TTL
        revoked_at=now,
        revoked_reason="rotated",
    )

    auth_token_retention_task()

    assert _refresh_token_exists(session, token_id) is True


def test_auth_token_retention_grace_days_is_configurable(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks.auth_token_retention import auth_token_retention_task

    monkeypatch.setenv("REFRESH_TOKEN_RETENTION_GRACE_DAYS", "0")
    user_id = _seed_user(session)
    now = datetime.now(UTC)
    # Expired ten minutes ago; with grace=0 this is already reclaimable.
    token_id = _add_refresh_token(
        session, user_id=user_id, expires_at=now - timedelta(minutes=10)
    )

    auth_token_retention_task()

    assert _refresh_token_exists(session, token_id) is False


# ---------------------------------------------------------------------------
# password_reset_tokens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age_past_expiry_days", "should_be_deleted"),
    [
        (10, True),
        (0.5, False),
        (-1, False),
    ],
)
def test_password_reset_token_deleted_only_past_expiry_plus_grace(
    session: Session, age_past_expiry_days: float, should_be_deleted: bool
) -> None:
    from tasks.auth_token_retention import auth_token_retention_task

    user_id = _seed_user(session)
    now = datetime.now(UTC)
    expires_at = now - timedelta(days=age_past_expiry_days)
    token_id = _add_password_reset_token(session, user_id=user_id, expires_at=expires_at)

    auth_token_retention_task()

    exists = _password_reset_token_exists(session, token_id)
    assert exists is (not should_be_deleted)


def test_used_password_reset_token_deleted_by_expiry_not_by_use(session: Session) -> None:
    """A used-but-unexpired token must survive; a used-and-long-expired one
    must not. ``used_at`` never gates the delete on its own."""
    from tasks.auth_token_retention import auth_token_retention_task

    user_id = _seed_user(session)
    now = datetime.now(UTC)
    unexpired_used = _add_password_reset_token(
        session,
        user_id=user_id,
        expires_at=now + timedelta(hours=1),
        used_at=now,
    )
    expired_used = _add_password_reset_token(
        session,
        user_id=user_id,
        expires_at=now - timedelta(days=10),
        used_at=now - timedelta(days=10),
    )

    auth_token_retention_task()

    assert _password_reset_token_exists(session, unexpired_used) is True
    assert _password_reset_token_exists(session, expired_used) is False


def test_auth_token_retention_is_idempotent(session: Session) -> None:
    """Re-running the sweep with nothing new to reclaim deletes nothing more
    and does not error (safe to re-run, per the module docstring)."""
    from tasks.auth_token_retention import auth_token_retention_task

    user_id = _seed_user(session)
    now = datetime.now(UTC)
    _add_refresh_token(session, user_id=user_id, expires_at=now - timedelta(days=10))
    _add_password_reset_token(session, user_id=user_id, expires_at=now - timedelta(days=10))

    first = auth_token_retention_task()
    second = auth_token_retention_task()

    assert first["deleted_refresh_tokens"] >= 1
    assert first["deleted_password_reset_tokens"] >= 1
    assert second["deleted_refresh_tokens"] == 0
    assert second["deleted_password_reset_tokens"] == 0
