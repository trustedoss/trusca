# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Integration tests for the group-hierarchy DB triggers (migrations
0090/0091): ``trg_groups_derive_path_insert``, ``trg_groups_derive_path_update``
and the ``verify_group_paths()`` diagnostic function.

Why this file exists rather than relying on the service-layer suite:
``admin_team_service`` (and its group-hierarchy successor) only ever creates
ROOT groups as of this Phase; nothing in the running application sets
``parent_group_id`` yet (no API/service change this phase; Phase 5 adds the
reparent service). That means the trigger's non-trivial branch (a row with a
parent) is untested by every existing production code path today, and would
stay untested until Phase 5 landed if this file didn't exercise it directly.

Every mutation below goes through raw SQL (``session.execute(text(...))``)
against ``groups``, never through an ORM model write or a service call,
the point is to pin what the trigger itself does when a caller writes the
columns directly, independent of whatever validation a future service adds
on top.

Mirrors the structure of ``test_last_super_admin_db_trigger.py`` (module
fixture that migrates once, ``app``/``client`` fixtures, a
``session_factory`` accessor) rather than introducing a new pattern.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests._db_required import migrate_to_head
from tests._helpers import make_organization

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


# ---------------------------------------------------------------------------
# Raw-SQL helpers: deliberately bypass the ORM model and every service.
# ---------------------------------------------------------------------------


async def _insert_group(
    session: AsyncSession,
    *,
    group_id: uuid.UUID,
    organization_id: uuid.UUID,
    slug: str,
    parent_group_id: uuid.UUID | None = None,
    path: list[uuid.UUID] | None = None,
) -> None:
    """INSERT one row into ``groups`` via raw SQL.

    ``path`` is accepted so a test can supply a caller-forged value and
    confirm the trigger overwrites it (test 3); when omitted, the column
    is left off the INSERT entirely so the trigger's derivation is the only
    thing that populates it.
    """
    columns = ["id", "organization_id", "name", "slug", "parent_group_id"]
    params: dict[str, object] = {
        "id": str(group_id),
        "organization_id": str(organization_id),
        "name": f"Group {slug}",
        "slug": slug,
        "parent_group_id": str(parent_group_id) if parent_group_id else None,
    }
    if path is not None:
        columns.append("path")
        params["path"] = [str(p) for p in path]

    placeholders = ", ".join(f":{c}" for c in columns)
    await session.execute(
        text(f"INSERT INTO groups ({', '.join(columns)}) VALUES ({placeholders})"),
        params,
    )
    await session.commit()


async def _fetch_group(
    session: AsyncSession, group_id: uuid.UUID
) -> tuple[uuid.UUID | None, list[uuid.UUID]]:
    """Return ``(parent_group_id, path)`` for a group, both raw SQL reads."""
    row = (
        await session.execute(
            text("SELECT parent_group_id, path FROM groups WHERE id = :id"),
            {"id": str(group_id)},
        )
    ).first()
    assert row is not None, f"group {group_id} not found"
    return row.parent_group_id, list(row.path)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# 1. Root INSERT -> path = '{}'
# ---------------------------------------------------------------------------


async def test_root_insert_gets_empty_path(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        root_id = _uuid()
        await _insert_group(
            session, group_id=root_id, organization_id=org.id, slug="root"
        )
        parent_group_id, path = await _fetch_group(session, root_id)
        assert parent_group_id is None
        assert path == []


# ---------------------------------------------------------------------------
# 2. Child INSERT -> path = parent.path || parent.id
# ---------------------------------------------------------------------------


async def test_child_insert_derives_path_from_parent(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        parent_id = _uuid()
        child_id = _uuid()
        await _insert_group(
            session, group_id=parent_id, organization_id=org.id, slug="parent"
        )
        await _insert_group(
            session,
            group_id=child_id,
            organization_id=org.id,
            slug="child",
            parent_group_id=parent_id,
        )
        parent_group_id, path = await _fetch_group(session, child_id)
        assert parent_group_id == parent_id
        assert path == [parent_id]


# ---------------------------------------------------------------------------
# 3. INSERT with a caller-supplied path -> trigger overwrites it
# ---------------------------------------------------------------------------


async def test_insert_supplied_path_is_overwritten_by_trigger(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        root_id = _uuid()
        forged_id = _uuid()
        await _insert_group(
            session, group_id=root_id, organization_id=org.id, slug="root"
        )

        forged_path = [_uuid(), _uuid()]  # nonsense ancestors that don't exist
        await _insert_group(
            session,
            group_id=forged_id,
            organization_id=org.id,
            slug="forged",
            parent_group_id=root_id,
            path=forged_path,
        )
        parent_group_id, path = await _fetch_group(session, forged_id)
        assert parent_group_id == root_id
        # The trigger's derivation from the real parent wins, not the
        # caller-supplied value.
        assert path == [root_id]
        assert path != forged_path


# ---------------------------------------------------------------------------
# 4. UPDATE parent_group_id -> path re-derived for the new parent
# ---------------------------------------------------------------------------


async def test_update_parent_group_id_rederives_path(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        old_parent_id = _uuid()
        new_parent_id = _uuid()
        moved_id = _uuid()
        await _insert_group(
            session, group_id=old_parent_id, organization_id=org.id, slug="old-parent"
        )
        await _insert_group(
            session, group_id=new_parent_id, organization_id=org.id, slug="new-parent"
        )
        await _insert_group(
            session,
            group_id=moved_id,
            organization_id=org.id,
            slug="moved",
            parent_group_id=old_parent_id,
        )
        parent_group_id, path = await _fetch_group(session, moved_id)
        assert parent_group_id == old_parent_id
        assert path == [old_parent_id]

        await session.execute(
            text("UPDATE groups SET parent_group_id = :new WHERE id = :id"),
            {"new": str(new_parent_id), "id": str(moved_id)},
        )
        await session.commit()
        parent_group_id, path = await _fetch_group(session, moved_id)
        assert parent_group_id == new_parent_id
        assert path == [new_parent_id]


# ---------------------------------------------------------------------------
# 5. UPDATE that does NOT touch parent_group_id -> trigger does not fire,
#    path is unchanged. This is the case the WHEN gate (and the `OF
#    parent_group_id` column list) exists for, see 0091's docstring.
# ---------------------------------------------------------------------------


async def test_update_unrelated_column_leaves_path_unchanged(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        parent_id = _uuid()
        child_id = _uuid()
        await _insert_group(
            session, group_id=parent_id, organization_id=org.id, slug="parent"
        )
        await _insert_group(
            session,
            group_id=child_id,
            organization_id=org.id,
            slug="child",
            parent_group_id=parent_id,
        )
        _, path_before = await _fetch_group(session, child_id)
        assert path_before == [parent_id]

        await session.execute(
            text("UPDATE groups SET name = :name WHERE id = :id"),
            {"name": "Renamed Child", "id": str(child_id)},
        )
        await session.commit()

        parent_group_id, path_after = await _fetch_group(session, child_id)
        assert parent_group_id == parent_id
        assert path_after == path_before == [parent_id]


# ---------------------------------------------------------------------------
# 6. Moving a group under its own descendant is rejected by
#    ck_groups_not_self_ancestor.
# ---------------------------------------------------------------------------


async def test_move_under_own_descendant_is_rejected(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        a_id, b_id, c_id = _uuid(), _uuid(), _uuid()
        await _insert_group(session, group_id=a_id, organization_id=org.id, slug="a")
        await _insert_group(
            session, group_id=b_id, organization_id=org.id, slug="b", parent_group_id=a_id
        )
        await _insert_group(
            session, group_id=c_id, organization_id=org.id, slug="c", parent_group_id=b_id
        )
        # c's path is [a, b] at this point, a is c's grandparent.

        with pytest.raises(IntegrityError) as excinfo:
            await session.execute(
                text("UPDATE groups SET parent_group_id = :new WHERE id = :id"),
                {"new": str(c_id), "id": str(a_id)},
            )
            await session.flush()
        orig = excinfo.value.orig
        pgcode = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
        assert pgcode == "23514"
        assert "ck_groups_not_self_ancestor" in str(orig)
        await session.rollback()

        # a is unaffected: still a root group.
        async with factory() as verify_session:
            parent_group_id, path = await _fetch_group(verify_session, a_id)
            assert parent_group_id is None
            assert path == []


# ---------------------------------------------------------------------------
# 7. A depth-3 chain (A -> B -> C) has the correct path at every level, and
#    verify_group_paths() reports zero mismatches. Phase 5 (reparent) is not
#    built yet, this only checks the chain built via INSERT, never moved.
# ---------------------------------------------------------------------------


async def test_depth_three_chain_matches_verify_group_paths(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        a_id, b_id, c_id = _uuid(), _uuid(), _uuid()
        await _insert_group(session, group_id=a_id, organization_id=org.id, slug="a")
        await _insert_group(
            session, group_id=b_id, organization_id=org.id, slug="b", parent_group_id=a_id
        )
        await _insert_group(
            session, group_id=c_id, organization_id=org.id, slug="c", parent_group_id=b_id
        )

        _, path_a = await _fetch_group(session, a_id)
        _, path_b = await _fetch_group(session, b_id)
        _, path_c = await _fetch_group(session, c_id)
        assert path_a == []
        assert path_b == [a_id]
        assert path_c == [a_id, b_id]

        mismatches = (
            await session.execute(text("SELECT id FROM verify_group_paths()"))
        ).fetchall()
        assert mismatches == [], (
            f"verify_group_paths() reported {len(mismatches)} mismatch(es) "
            "after a plain 3-level INSERT chain, the trigger's derivation "
            "and the recursive-CTE cross-check disagree"
        )


# ---------------------------------------------------------------------------
# 8. A raw UPDATE that sets ONLY `path` (never mentions parent_group_id in
#    its SET list) is exactly the shape Phase 5's descendant propagation
#    step will use, and must land untouched. This is the direct proof of
#    the claim in 0091's docstring, not the name-only-column proxy in test
#    5 above, which passes for a different reason (the column simply isn't
#    `parent_group_id`) and would pass even if this exact mechanism were
#    broken in a way that only affects a `path`-setting statement.
# ---------------------------------------------------------------------------


async def test_direct_path_write_without_touching_parent_group_id_is_not_clobbered(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        parent_id = _uuid()
        child_id = _uuid()
        await _insert_group(
            session, group_id=parent_id, organization_id=org.id, slug="parent"
        )
        await _insert_group(
            session,
            group_id=child_id,
            organization_id=org.id,
            slug="child",
            parent_group_id=parent_id,
        )
        _, path_before = await _fetch_group(session, child_id)
        assert path_before == [parent_id]

        # Simulate what a Phase-5 reparent service would do to a
        # descendant: write `path` directly, without touching
        # `parent_group_id` at all. If the trigger's event scope
        # (`UPDATE OF parent_group_id`) or its WHEN gate regressed to fire
        # unconditionally, this write would be silently discarded and
        # replaced with a fresh derivation from the (here, unchanged)
        # parent, this asserts the caller's literal value survives.
        forged_path = [_uuid(), _uuid(), _uuid()]
        await session.execute(
            text("UPDATE groups SET path = :path WHERE id = :id"),
            {"path": [str(p) for p in forged_path], "id": str(child_id)},
        )
        await session.flush()

        parent_group_id, path_after = await _fetch_group(session, child_id)
        assert parent_group_id == parent_id  # untouched
        assert path_after == forged_path, (
            "a path-only UPDATE was overwritten by the derive-path trigger; "
            "Phase 5's reparent service would have no way to update a "
            "descendant's path"
        )
        # Roll back the forged write rather than committing it: this test's
        # point is only that the trigger does not clobber a direct `path`
        # write, not to leave a genuinely inconsistent row behind for every
        # later test/run to trip over `verify_group_paths()` on. `org` and
        # both groups' legitimate rows (inserted above) were already
        # committed by `_insert_group` and are unaffected; only this
        # uncommitted UPDATE is discarded.
        await session.rollback()
