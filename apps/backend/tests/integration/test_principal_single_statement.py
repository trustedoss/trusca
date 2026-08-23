# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""A3 (concurrency-scaling-plan-2026-08-22.md §3.3, §4): principal in one statement.

``_load_current_user`` used to run two statements per authenticated request,
one for the user and one for its memberships (a ``selectinload``), even
though nothing about the shape (a user has few teams) needed the extra round
trip. This file pins the fix from both directions the plan's §4 A3 row asks
for:

  - the query count. One join instead of two selects, regardless of how many
    memberships the user has (0, 1, or several).
  - the judgment logic. ``team_ids``, ``team_roles``, and the highest role
    ``_load_current_user`` computes must be exactly what the seeded
    memberships say, unaffected by the join rewrite. The expected values are
    computed independently in this file (not by calling any of the functions
    under test), so a regression in the join (a wrong row, a dropped
    duplicate, an unhydrated relationship) would show up as a mismatch
    rather than being hidden behind reusing the same logic on both sides.

Runs against real Postgres (CLAUDE.md: no SQLite, even in tests).
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.security import CurrentUser, _load_current_user, create_access_token
from tests._helpers import make_membership, make_organization, make_team, make_user

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration

# Independent of ``core.security._ROLE_PRIORITY``: hardcoded here so this file
# does not lean on the same table the code under test reads to decide what
# "highest" means.
_ROLE_RANK = {"viewer": 1, "developer": 2, "team_admin": 3, "super_admin": 4}


class _BearerRequest:
    """Just enough of a ``Request`` for ``_load_current_user``: a header dict.

    ``_bearer_token`` only calls ``.headers.get(...)``, which a plain dict
    already satisfies, so there is no need to construct a real ASGI scope.
    """

    def __init__(self, token: str) -> None:
        self.headers = {"Authorization": f"Bearer {token}"}


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set: skipping DB-backed tests")
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
        pytest.skip(f"alembic upgrade head failed:\n{result.stderr}")


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


async def _load(session: AsyncSession, user_id: uuid.UUID) -> tuple[CurrentUser | None, int]:
    """Run ``_load_current_user`` and return the principal plus statement count."""
    token = create_access_token(subject=str(user_id))
    request = _BearerRequest(token)

    engine = session.get_bind()
    counted = 0

    def _record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        nonlocal counted
        counted += 1

    event.listen(engine, "before_cursor_execute", _record)
    try:
        principal = await _load_current_user(request, session)
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    return principal, counted


@pytest.mark.parametrize(
    "roles",
    [
        pytest.param((), id="zero_memberships"),
        pytest.param(("team_admin",), id="one_membership"),
        pytest.param(("viewer", "developer", "team_admin"), id="many_memberships"),
    ],
)
async def test_principal_load_is_one_statement_and_survives_membership_count(
    db_session: AsyncSession, roles: tuple[str, ...]
) -> None:
    org = await make_organization(db_session)
    user = await make_user(db_session)

    expected_team_roles: dict[uuid.UUID, str] = {}
    for role in roles:
        team = await make_team(db_session, organization=org)
        await make_membership(db_session, user=user, team=team, role=role)
        expected_team_roles[team.id] = role

    principal, statement_count = await _load(db_session, user.id)

    assert principal is not None
    assert statement_count == 1, (
        f"principal load issued {statement_count} statements for "
        f"{len(roles)} membership(s), expected exactly 1 (a joined load, "
        "not a user select followed by a memberships select)"
    )

    assert set(principal.team_ids) == set(expected_team_roles)
    assert len(principal.team_ids) == len(expected_team_roles), "no duplicate rows from the join"
    assert principal.team_roles == expected_team_roles

    if roles:
        expected_role = max(roles, key=lambda r: _ROLE_RANK[r])
    else:
        expected_role = "developer"
    assert principal.role == expected_role


async def test_principal_load_survives_membership_count_for_superuser(
    db_session: AsyncSession,
) -> None:
    """A superuser's role is ``super_admin`` regardless of membership shape.

    Kept separate from the parametrized case above because it exercises a
    different branch of ``_highest_role`` (the ``is_superuser`` override), not
    another point on the same membership-count axis.
    """
    org = await make_organization(db_session)
    user = await make_user(db_session, is_superuser=True)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    await make_membership(db_session, user=user, team=team_a, role="viewer")
    await make_membership(db_session, user=user, team=team_b, role="developer")

    principal, statement_count = await _load(db_session, user.id)

    assert principal is not None
    assert statement_count == 1
    assert set(principal.team_ids) == {team_a.id, team_b.id}
    assert principal.team_roles == {team_a.id: "viewer", team_b.id: "developer"}
    assert principal.role == "super_admin"
