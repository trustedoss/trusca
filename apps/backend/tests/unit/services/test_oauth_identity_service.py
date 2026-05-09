"""
Service-layer tests for ``services.oauth_identity_service`` — Chore G.

Drives the pure async service against a live Postgres (``DATABASE_URL``) so
the SQLAlchemy listener fires and the service's INSERT/UPDATE/DELETE
statements hit the real schema (matching the shape of the other
``tests/unit/services/test_*_service.py`` files).

Coverage targets the contract spelled out in the chore prompt:

  - ``list_user_oauth_identities``: empty, isolation, oldest-first order.
  - ``unlink_oauth_identity`` happy path with password + 2 identities.
  - ``unlink_oauth_identity`` last identity when user HAS a password
    (password is the fallback → success).
  - ``unlink_oauth_identity`` last identity when user has NO password
    (raises :class:`OAuthUnlinkBlocksLoginError`).
  - ``unlink_oauth_identity`` on someone else's row → existence-hide
    (raises :class:`OAuthIdentityNotFoundError`).

The audit-row contract (explicit ``oauth.identity.unlinked`` row with
``provider_user_id_hash``) is verified at the integration layer where the
HTTP middleware binds a request_id; here we just confirm the row count
flips by 1 (listener delete + explicit unlink = 2 audit rows per unlink).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import make_user

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip oauth_identity_service tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            "alembic upgrade head failed; oauth_identity_service tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_pid() -> str:
    """Per-test ``provider_user_id`` so the unique constraint never collides."""
    return uuid.uuid4().hex


async def _make_identity(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    provider: str = "github",
    email: str | None = None,
    provider_user_id: str | None = None,
):
    from models import OAuthIdentity

    row = OAuthIdentity(
        user_id=user_id,
        provider=provider,
        provider_user_id=provider_user_id or _unique_pid(),
        email=email or f"{uuid.uuid4().hex}@example.com",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def _set_user_password(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    has_password: bool,
) -> None:
    """Force the user's ``hashed_password`` to either a real hash or empty."""
    from models import User

    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one()
    if has_password:
        # ``make_user`` already sets a real hash; this branch is a no-op.
        assert user.hashed_password
    else:
        # OAuth-only account: blank out the hash so the guard trips.
        user.hashed_password = ""
    await session.commit()


# ---------------------------------------------------------------------------
# list_user_oauth_identities
# ---------------------------------------------------------------------------


async def test_list_returns_empty_for_user_with_no_identities(
    db_session: AsyncSession,
) -> None:
    from services.oauth_identity_service import list_user_oauth_identities

    user = await make_user(db_session)
    rows = await list_user_oauth_identities(db_session, user_id=user.id)
    assert list(rows) == []


async def test_list_returns_user_own_identities_only(
    db_session: AsyncSession,
) -> None:
    """RBAC: one user must not see another user's identities via list."""
    from services.oauth_identity_service import list_user_oauth_identities

    alice = await make_user(db_session)
    bob = await make_user(db_session)

    a1 = await _make_identity(db_session, user_id=alice.id, provider="github")
    a2 = await _make_identity(db_session, user_id=alice.id, provider="google")
    await _make_identity(db_session, user_id=bob.id, provider="github")

    alice_rows = await list_user_oauth_identities(db_session, user_id=alice.id)
    bob_rows = await list_user_oauth_identities(db_session, user_id=bob.id)
    assert {r.id for r in alice_rows} == {a1.id, a2.id}
    assert len(list(bob_rows)) == 1


async def test_list_orders_oldest_first(db_session: AsyncSession) -> None:
    from services.oauth_identity_service import list_user_oauth_identities

    user = await make_user(db_session)
    first = await _make_identity(db_session, user_id=user.id, provider="github")
    second = await _make_identity(db_session, user_id=user.id, provider="google")

    rows = list(await list_user_oauth_identities(db_session, user_id=user.id))
    # Oldest-first: first.linked_at < second.linked_at.
    assert [r.id for r in rows] == [first.id, second.id]


# ---------------------------------------------------------------------------
# unlink_oauth_identity — happy path
# ---------------------------------------------------------------------------


async def test_unlink_with_password_and_two_identities_succeeds(
    db_session: AsyncSession,
) -> None:
    """User with password + 2 identities: removing one must succeed."""
    from services.oauth_identity_service import (
        list_user_oauth_identities,
        unlink_oauth_identity,
    )

    user = await make_user(db_session)
    gh = await _make_identity(db_session, user_id=user.id, provider="github")
    google = await _make_identity(db_session, user_id=user.id, provider="google")

    await unlink_oauth_identity(db_session, user_id=user.id, identity_id=gh.id)

    remaining = list(await list_user_oauth_identities(db_session, user_id=user.id))
    assert [r.id for r in remaining] == [google.id]


async def test_unlink_last_identity_when_user_has_password_succeeds(
    db_session: AsyncSession,
) -> None:
    """The last identity may be removed if the user can still log in via password."""
    from services.oauth_identity_service import (
        list_user_oauth_identities,
        unlink_oauth_identity,
    )

    user = await make_user(db_session)  # make_user always sets a real hashed_password
    only = await _make_identity(db_session, user_id=user.id, provider="github")

    await unlink_oauth_identity(db_session, user_id=user.id, identity_id=only.id)

    remaining = list(await list_user_oauth_identities(db_session, user_id=user.id))
    assert remaining == []


# ---------------------------------------------------------------------------
# unlink_oauth_identity — guards
# ---------------------------------------------------------------------------


async def test_unlink_last_identity_without_password_raises_blocks_login(
    db_session: AsyncSession,
) -> None:
    """OAuth-only user, single identity → cannot remove (would brick login)."""
    from services.oauth_identity_service import (
        OAuthUnlinkBlocksLoginError,
        list_user_oauth_identities,
        unlink_oauth_identity,
    )

    user = await make_user(db_session)
    await _set_user_password(db_session, user_id=user.id, has_password=False)
    only = await _make_identity(db_session, user_id=user.id, provider="github")

    with pytest.raises(OAuthUnlinkBlocksLoginError):
        await unlink_oauth_identity(
            db_session, user_id=user.id, identity_id=only.id
        )

    # Row is still present.
    remaining = list(await list_user_oauth_identities(db_session, user_id=user.id))
    assert [r.id for r in remaining] == [only.id]


async def test_unlink_one_of_two_without_password_succeeds(
    db_session: AsyncSession,
) -> None:
    """OAuth-only user with 2 identities can remove one (other is the fallback)."""
    from services.oauth_identity_service import (
        list_user_oauth_identities,
        unlink_oauth_identity,
    )

    user = await make_user(db_session)
    await _set_user_password(db_session, user_id=user.id, has_password=False)
    gh = await _make_identity(db_session, user_id=user.id, provider="github")
    google = await _make_identity(db_session, user_id=user.id, provider="google")

    await unlink_oauth_identity(db_session, user_id=user.id, identity_id=gh.id)

    remaining = list(await list_user_oauth_identities(db_session, user_id=user.id))
    assert [r.id for r in remaining] == [google.id]


async def test_unlink_someone_elses_identity_raises_not_found(
    db_session: AsyncSession,
) -> None:
    """Existence-hide: cross-user unlink → 404, indistinguishable from 'no row'."""
    from services.oauth_identity_service import (
        OAuthIdentityNotFoundError,
        list_user_oauth_identities,
        unlink_oauth_identity,
    )

    alice = await make_user(db_session)
    bob = await make_user(db_session)
    bob_identity = await _make_identity(db_session, user_id=bob.id, provider="github")

    with pytest.raises(OAuthIdentityNotFoundError):
        await unlink_oauth_identity(
            db_session, user_id=alice.id, identity_id=bob_identity.id
        )

    # Bob's row is untouched.
    bob_rows = list(await list_user_oauth_identities(db_session, user_id=bob.id))
    assert [r.id for r in bob_rows] == [bob_identity.id]


async def test_unlink_unknown_identity_id_raises_not_found(
    db_session: AsyncSession,
) -> None:
    from services.oauth_identity_service import (
        OAuthIdentityNotFoundError,
        unlink_oauth_identity,
    )

    user = await make_user(db_session)
    with pytest.raises(OAuthIdentityNotFoundError):
        await unlink_oauth_identity(
            db_session, user_id=user.id, identity_id=uuid.uuid4()
        )


# ---------------------------------------------------------------------------
# Audit row — explicit semantic action
# ---------------------------------------------------------------------------


async def test_unlink_writes_explicit_audit_row_with_hashed_pid(
    db_session: AsyncSession,
) -> None:
    """The explicit ``oauth.identity.unlinked`` row carries the provider +
    sha256(provider_user_id) and never the raw provider_user_id.
    """
    from models import AuditLog
    from services.oauth_identity_service import unlink_oauth_identity

    user = await make_user(db_session)
    # Per-test stable id so the unique-constraint never collides across runs.
    pid = f"stable-{uuid.uuid4().hex}"
    expected_hash = hashlib.sha256(pid.encode("utf-8")).hexdigest()
    identity = await _make_identity(
        db_session,
        user_id=user.id,
        provider="google",
        provider_user_id=pid,
    )
    # Two identities so the unlink succeeds (last-method guard untriggered).
    await _make_identity(db_session, user_id=user.id, provider="github")

    await unlink_oauth_identity(
        db_session, user_id=user.id, identity_id=identity.id
    )

    rows = (
        await db_session.execute(
            select(AuditLog)
            .where(AuditLog.target_table == "oauth_identities")
            .where(AuditLog.target_id == str(identity.id))
            .where(AuditLog.action == "oauth.identity.unlinked")
        )
    ).scalars().all()
    assert len(rows) == 1
    diff = rows[0].diff
    assert diff["provider"] == "google"
    assert diff["provider_user_id_hash"] == expected_hash
    # No raw provider_user_id field — the hash is the only forensic carrier.
    assert "provider_user_id" not in diff


async def test_unlink_increments_audit_row_count_by_two(
    db_session: AsyncSession,
) -> None:
    """One unlink writes both the listener delete row + the explicit row."""
    from models import AuditLog
    from services.oauth_identity_service import unlink_oauth_identity

    user = await make_user(db_session)
    identity = await _make_identity(db_session, user_id=user.id, provider="github")
    await _make_identity(db_session, user_id=user.id, provider="google")

    before = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.target_table == "oauth_identities")
            .where(AuditLog.target_id == str(identity.id))
        )
    ).scalar_one()

    await unlink_oauth_identity(
        db_session, user_id=user.id, identity_id=identity.id
    )

    after = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.target_table == "oauth_identities")
            .where(AuditLog.target_id == str(identity.id))
        )
    ).scalar_one()
    # Before: the original listener-driven INSERT row from _make_identity
    # may or may not be visible depending on session caching. The delta
    # is what matters: the unlink contributes exactly 2 rows.
    assert after - before == 2
