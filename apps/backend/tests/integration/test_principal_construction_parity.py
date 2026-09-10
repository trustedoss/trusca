# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Group-hierarchy PR 2-B: the three principal-construction paths agree.

PR 2-A's cascade functions (``services/group_service.py``) take a "direct
membership role map" (``direct_roles: Mapping[uuid.UUID, str]``) as input.
The design note that motivated PR 2-A called this ``group_roles_direct`` as
if it were a field the codebase still needed to grow. Reading the three
places that build a :class:`core.security.CurrentUser` shows it already
exists: it is ``CurrentUser.team_roles``, built the same way in all three
places, straight off ``user.memberships``, with no cascade expansion
anywhere in sight:

    team_ids = [m.team_id for m in memberships]
    team_roles = {m.team_id: m.role for m in memberships}

(``Membership.team_id`` is a synonym for the ``group_id`` column per
migration 0088, so this is already exactly the group-hierarchy vocabulary
under an old name.) There is no separate ``group_roles_direct`` field to add;
PR 2-C's cascade wiring can hand ``team_roles`` straight to
``effective_role_at`` / ``can_access_group`` once it lands.

This file is the contract that keeps that true. Three independent call
sites build a ``CurrentUser`` from the same ``memberships`` table:

  - ``core.security._load_current_user``          (JWT bearer path)
  - ``core.api_key_auth.get_api_key_principal``    (API-key bearer path)
  - ``api.v1.ws._resolve_user``                    (WebSocket path)

``ws._resolve_user``'s own docstring says it is a hand-copied mirror of
``_load_current_user`` because a WebSocket scope carries no ``Request`` for
the shared dependency to accept, and that copy has drifted before (it once
omitted the ``viewer`` role from the priority map, see
``core.security.highest_role``'s docstring). ``get_api_key_principal``
additionally narrows ``team_roles``/``team_ids`` to the key's declared scope
for team- and project-scoped keys, which is a deliberate authorization
boundary, not construction drift, so this file compares against an
ORG-scoped key, the one scope where nothing is narrowed and the three paths
must produce byte-for-byte the same ``team_ids``/``team_roles``/``role``.

Runs against real Postgres (CLAUDE.md: no SQLite, even in tests).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.api_key_auth import get_api_key_principal
from core.security import CurrentUser, _load_current_user, create_access_token
from services.api_key_service import issue_api_key
from tests._db_required import migrate_to_head
from tests._helpers import make_membership, make_organization, make_team, make_user

pytestmark = pytest.mark.integration


class _BearerRequest:
    """Just enough of a ``Request`` for the bearer-header readers under test.

    Both ``core.security._bearer_token`` and ``core.api_key_auth._bearer_token``
    only call ``.headers.get(...)``, which a plain dict already satisfies.
    """

    def __init__(self, token: str) -> None:
        self.headers = {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


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


async def _build_three_principals(
    session: AsyncSession, *, user_id: uuid.UUID, api_key_plaintext: str
) -> tuple[CurrentUser, CurrentUser, CurrentUser]:
    """Resolve the same user through all three auth surfaces.

    Local import for ``_resolve_user``: it is WebSocket-module-private and
    the other two suites that reach into ``api.v1.ws`` for private helpers
    (``test_ws_scan_progress.py``, ``tests/unit/test_ws_helpers.py``) do the
    same rather than promoting it to ``__all__`` just for tests.
    """
    from api.v1.ws import _resolve_user

    jwt_token = create_access_token(subject=str(user_id))
    jwt_principal = await _load_current_user(_BearerRequest(jwt_token), session)
    assert jwt_principal is not None

    key_principal = await get_api_key_principal(_BearerRequest(api_key_plaintext), session)
    assert key_principal is not None

    ws_principal = await _resolve_user(session, user_id)
    assert ws_principal is not None

    return jwt_principal, key_principal, ws_principal


async def test_jwt_api_key_and_websocket_principals_agree(db_session: AsyncSession) -> None:
    """Same user, same memberships (including a ``viewer`` grade) -> same principal.

    ``viewer`` is included deliberately: it is the grade the WebSocket path's
    hand-copied priority map used to drop (see this file's module docstring),
    so a regression that reintroduces that drift shows up here as a role
    mismatch, not just a missing/extra team.
    """
    org = await make_organization(db_session)
    user = await make_user(db_session, is_superuser=True)
    team_viewer = await make_team(db_session, organization=org)
    team_developer = await make_team(db_session, organization=org)
    team_admin = await make_team(db_session, organization=org)

    await make_membership(db_session, user=user, team=team_viewer, role="viewer")
    await make_membership(db_session, user=user, team=team_developer, role="developer")
    await make_membership(db_session, user=user, team=team_admin, role="group_admin")

    expected_team_roles = {
        team_viewer.id: "viewer",
        team_developer.id: "developer",
        team_admin.id: "group_admin",
    }

    # ORG scope: nothing narrows team_roles/team_ids for this scope (unlike
    # team/project scope), so it is the fair comparison point against the JWT
    # and WebSocket paths, which never narrow at all.
    actor = CurrentUser(
        id=user.id,
        email=user.email,
        role="super_admin",
        team_ids=list(expected_team_roles),
        team_roles=dict(expected_team_roles),
        is_active=True,
        is_superuser=True,
    )
    _row, api_key_plaintext = await issue_api_key(
        db_session,
        actor,
        name="principal-parity-contract-test",
        scope="org",
        team_id=None,
        project_id=None,
        permission_breadth="read_write",
    )

    jwt_principal, key_principal, ws_principal = await _build_three_principals(
        db_session, user_id=user.id, api_key_plaintext=api_key_plaintext
    )

    for label, principal in (
        ("jwt", jwt_principal),
        ("api_key", key_principal),
        ("websocket", ws_principal),
    ):
        assert set(principal.team_ids) == set(expected_team_roles), label
        assert len(principal.team_ids) == len(expected_team_roles), (
            f"{label}: duplicate membership rows leaked into team_ids"
        )
        assert principal.team_roles == expected_team_roles, label
        assert principal.role == "super_admin", label

    assert set(jwt_principal.team_ids) == set(key_principal.team_ids) == set(
        ws_principal.team_ids
    )
    assert jwt_principal.team_roles == key_principal.team_roles == ws_principal.team_roles
    assert jwt_principal.role == key_principal.role == ws_principal.role


async def test_jwt_api_key_and_websocket_principals_agree_with_zero_memberships(
    db_session: AsyncSession,
) -> None:
    """A user with no memberships is the same empty answer on every path."""
    user = await make_user(db_session, is_superuser=True)

    actor = CurrentUser(
        id=user.id,
        email=user.email,
        role="super_admin",
        team_ids=[],
        team_roles={},
        is_active=True,
        is_superuser=True,
    )
    _row, api_key_plaintext = await issue_api_key(
        db_session,
        actor,
        name="principal-parity-contract-test-empty",
        scope="org",
        team_id=None,
        project_id=None,
        permission_breadth="read_write",
    )

    jwt_principal, key_principal, ws_principal = await _build_three_principals(
        db_session, user_id=user.id, api_key_plaintext=api_key_plaintext
    )

    for label, principal in (
        ("jwt", jwt_principal),
        ("api_key", key_principal),
        ("websocket", ws_principal),
    ):
        assert principal.team_ids == [], label
        assert principal.team_roles == {}, label
        assert principal.role == "super_admin", label
