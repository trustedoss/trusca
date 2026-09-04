# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Bootstrap-from-empty-database sequence (testing-hardening-plan-2026-08.md
§1 유형 A / §2 A1).

Reproduces the exact operator path a fresh, air-gapped install takes:
``python -m scripts.create_super_admin`` against a schema-only database,
then log in as that admin and drive the first team, project, and scan
through the HTTP surface. Nothing else has ever written to this database.

This is the logic-level twin of A2 (which extends the ``install-uat``
workflow's real-deployment smoke test to the same depth, on a separate
branch). A1 proves the sequence in-process, without needing a full
docker-compose stack.

``create_super_admin`` now ensures exactly one ``Organization`` row exists
(reusing one if already present) on every invocation, so the team service's
``_pick_default_org`` always has a row to pick from and the very first
``POST /v1/admin/teams`` call an operator makes after bootstrap succeeds.
See ``services/admin_team_service.py::_pick_default_org`` and
``scripts/create_super_admin.py::_ensure_organization``.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

from tests._db_required import migrate_to_head, require_database_url

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = [pytest.mark.integration, pytest.mark.serial]


def _sync_dsn(url: str) -> str:
    """asyncpg DSN -> psycopg2 DSN (mirrors core.config.database_url_sync)."""
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _all_public_tables(conn) -> set[str]:  # type: ignore[no-untyped-def]
    rows = conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
    ).fetchall()
    return {r[0] for r in rows}


def _truncate_blocked_tables(conn) -> set[str]:  # type: ignore[no-untyped-def]
    """Tables carrying their own ``BEFORE TRUNCATE`` trigger.

    ``audit_logs`` (migration 0012) is append-only by design: even the
    schema owner cannot TRUNCATE or DELETE it, on purpose (CLAUDE.md
    로깅 규약 / audit trail integrity). Discovered from ``pg_trigger``
    rather than named literally so a future append-only table is picked up
    without touching this fixture. ``tgtype`` is a bitmask; bit 5
    (``1 << 5`` = 32) is Postgres's ``TRIGGER_TYPE_TRUNCATE``.
    """
    rows = conn.execute(
        text(
            "SELECT DISTINCT c.relname "
            "FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' "
            "AND NOT t.tgisinternal "
            "AND (t.tgtype::int & 32) <> 0"
        )
    ).fetchall()
    return {r[0] for r in rows}


def _fk_child_to_parents(conn) -> dict[str, set[str]]:  # type: ignore[no-untyped-def]
    """table -> set of tables it holds a foreign key into, in ``public``."""
    rows = conn.execute(
        text(
            "SELECT tc.table_name AS child, ccu.table_name AS parent "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name = ccu.constraint_name "
            "  AND tc.constraint_schema = ccu.constraint_schema "
            "WHERE tc.constraint_type = 'FOREIGN KEY' "
            "AND tc.table_schema = 'public'"
        )
    ).fetchall()
    edges: dict[str, set[str]] = {}
    for child, parent in rows:
        if child == parent:
            continue
        edges.setdefault(child, set()).add(parent)
    return edges


def _tables_poisoned_by_truncate_block(
    blocked: set[str], child_to_parents: dict[str, set[str]]
) -> set[str]:
    """Every table that TRUNCATE ... CASCADE would be forced to pull in a
    ``blocked`` table for.

    TRUNCATE CASCADE only cascades *forward* (to tables that reference the
    one you name), but that cascade is transitive: truncating a table also
    forces in every descendant, and every descendant of THAT, and so on. So
    truncating any ancestor of a blocked table (its parent, its parent's
    parent, ...) would transitively try to pull the blocked table in too and
    fail the same way ``TRUNCATE users`` does for ``audit_logs``. This
    returns the full poisoned set (blocked tables plus all their ancestors)
    so the caller knows which tables cannot go through a TRUNCATE statement
    at all, no matter which other tables are named alongside them.
    """
    poisoned = set(blocked)
    changed = True
    while changed:
        changed = False
        for table in list(poisoned):
            for parent in child_to_parents.get(table, ()):
                if parent not in poisoned:
                    poisoned.add(parent)
                    changed = True
    return poisoned


def _topological_delete_order(
    tables: set[str], child_to_parents: dict[str, set[str]]
) -> list[str]:
    """Order ``tables`` children-before-parents (Kahn's algorithm) so a
    plain ``DELETE FROM`` sequence never trips a RESTRICT/NO ACTION FK.

    Edges are restricted to ``tables``: dependencies outside the set are
    irrelevant here (they were already emptied by the bulk TRUNCATE).
    """
    edges = {t: (child_to_parents.get(t, set()) & tables) for t in tables}
    in_degree = dict.fromkeys(tables, 0)
    for child, parents in edges.items():
        for parent in parents:
            in_degree[parent] += 1
    queue = sorted(t for t, deg in in_degree.items() if deg == 0)
    ordered: list[str] = []
    while queue:
        node = queue.pop(0)
        ordered.append(node)
        for parent in sorted(edges.get(node, ())):
            in_degree[parent] -= 1
            if in_degree[parent] == 0:
                queue.append(parent)
    if len(ordered) != len(tables):
        remaining = tables - set(ordered)
        raise AssertionError(
            f"pristine_db: could not order {remaining!r} for DELETE. "
            "A foreign-key cycle among tables that feed an append-only "
            "table would need manual handling here."
        )
    return ordered


def _truncate_every_table_except_alembic_version(url: str) -> None:
    """Wipe every row in the public schema so the DB matches a fresh install.

    Table names are discovered from ``information_schema.tables`` at run
    time, never hardcoded, so a future migration that adds a table is
    wiped automatically instead of silently surviving as leftover state.
    ``alembic_version`` is kept: dropping it would make the schema look
    unmigrated, which is a different (and wrong) state than "migrated, but
    empty".

    Two passes, both driven by catalog discovery rather than a literal
    table list:

    1. Every table that is not append-only and not an ancestor of an
       append-only table goes through one ``TRUNCATE ... CASCADE``
       statement (fast, resets identity sequences).
    2. The remaining "poisoned" tables (``audit_logs`` plus its FK
       ancestors, currently ``users``, ``teams``, ``organizations``) are
       emptied with per-table ``DELETE FROM``, children first, so the
       already-empty descendants from pass 1 don't leave a dangling FK
       behind. ``audit_logs`` itself is never touched: it is append-only
       by design (0012_audit_logs_immutable_trigger) and is expected to
       carry rows left over from whatever ran in this database before this
       module's test. That's fine; this test never reads audit_logs.

       ``users`` carries a second guard (0013_last_super_admin_constraint)
       that raises rather than let a DELETE/UPDATE drop the active
       super_admin count to zero. By design, the migration's own docstring
       names TRUNCATE as the sanctioned escape hatch for a wholesale wipe
       ("the operator who runs TRUNCATE has already chosen the
       non-recoverable path"). We cannot take that hatch here (``users`` is
       already forced onto the DELETE path by the audit_logs FK above), so
       each deletable table has its user-defined triggers disabled for the
       duration of its own DELETE and re-enabled immediately after, inside
       the same transaction as everything else here. This is the
       TRUNCATE-equivalent bypass, scoped to exactly the table being wiped.
       FK-enforcement triggers are internal (system) triggers, not user
       triggers, so ``DISABLE TRIGGER USER`` never touches referential
       integrity, only custom invariants like the super_admin guard.
    """
    engine = create_engine(_sync_dsn(url), future=True)
    try:
        with engine.begin() as conn:
            all_tables = _all_public_tables(conn) - {"alembic_version"}
            blocked = _truncate_blocked_tables(conn)
            child_to_parents = _fk_child_to_parents(conn)
            poisoned = _tables_poisoned_by_truncate_block(blocked, child_to_parents)

            truncatable = all_tables - poisoned
            if truncatable:
                quoted = ", ".join(f'"{name}"' for name in sorted(truncatable))
                conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))

            deletable = (all_tables & poisoned) - blocked
            for table in _topological_delete_order(deletable, child_to_parents):
                conn.execute(text(f'ALTER TABLE "{table}" DISABLE TRIGGER USER'))
                try:
                    conn.execute(text(f'DELETE FROM "{table}"'))
                finally:
                    conn.execute(text(f'ALTER TABLE "{table}" ENABLE TRIGGER USER'))
    finally:
        engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def pristine_db() -> Iterator[None]:
    """Module-scoped, serial: leaves the whole public schema empty (bar
    ``alembic_version``) before the single test in this module runs.

    Deliberately synchronous (subprocess plus a plain psycopg2 engine, no
    async fixture): pytest-asyncio in this repo pins
    ``asyncio_default_fixture_loop_scope = "function"`` (see
    ``pyproject.toml``), so a module-scoped *async* fixture would try to
    reuse asyncpg connections across event loops from different test
    functions and crash. Staying sync sidesteps that entirely.

    This does NOT use ``tests/_helpers.py``: those factories create
    organizations/teams directly via the ORM, which is exactly the
    shortcut this test exists to avoid. It must go through the real
    bootstrap script and the real HTTP surface instead.

    Marked ``serial`` (see ``pyproject.toml`` markers) because it discards
    every row in the database: it must never run concurrently with another
    test that expects its own data to survive. This repo's CI does not run
    pytest under xdist today, so this is a documented invariant rather than
    an enforced one. The marker exists so a future parallelization change
    has something to grep for.
    """
    url = require_database_url()
    migrate_to_head()
    _truncate_every_table_except_alembic_version(url)
    yield


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _create_super_admin_via_function(*, email: str, password: str) -> None:
    """Call the real bootstrap entry point in-process (not a subprocess).

    ``scripts/create_super_admin.py::_main`` reads ``ADMIN_EMAIL`` /
    ``ADMIN_PASSWORD`` from the environment at call time (CLAUDE.md core
    rule #11, no module-level env caching), so setting them right before
    the call and calling the coroutine directly reproduces exactly what
    ``docker-compose ... exec backend python -m scripts.create_super_admin``
    does, without the subprocess indirection.
    """
    os.environ["ADMIN_EMAIL"] = email
    os.environ["ADMIN_PASSWORD"] = password
    try:
        from scripts.create_super_admin import _main

        exit_code = await _main()
    finally:
        del os.environ["ADMIN_EMAIL"]
        del os.environ["ADMIN_PASSWORD"]
    assert exit_code == 0, (
        f"create_super_admin bootstrap itself failed (exit={exit_code}); "
        "this must succeed before the rest of the sequence can even be "
        "attempted"
    )


async def test_bootstrap_from_empty_db_reaches_first_scan(client: AsyncClient) -> None:
    """The exact sequence a fresh install walks: bootstrap -> login -> team
    -> project -> scan. Every step asserts its own status code so a
    regression (or, right now, the known bug) is visible from the failing
    assertion message alone, without reading the traceback.
    """
    admin_email = f"bootstrap-admin-{uuid.uuid4().hex[:10]}@example.com"
    admin_password = f"BootstrapAdmin!{uuid.uuid4().hex[:8]}"

    # Step 1: bootstrap the super admin the way install.sh does.
    await _create_super_admin_via_function(email=admin_email, password=admin_password)

    # Step 2: log in as that admin.
    login = await client.post(
        "/auth/login",
        json={"email": admin_email, "password": admin_password},
    )
    assert login.status_code == 200, (
        f"login failed for the just-bootstrapped super admin: {login.status_code} {login.text}"
    )
    access_token = login.json()["access_token"]
    auth_headers = {"Authorization": f"Bearer {access_token}"}

    # Step 3: create the first team. create_super_admin's _ensure_organization
    # gives _pick_default_org() a row to pick from.
    team_slug = f"bootstrap-team-{uuid.uuid4().hex[:10]}"
    team_resp = await client.post(
        "/v1/admin/teams",
        json={"name": "Bootstrap Team", "slug": team_slug},
        headers=auth_headers,
    )
    assert team_resp.status_code == 201, (
        "team creation failed on a pristine database: "
        f"{team_resp.status_code} {team_resp.text}"
    )
    team_id = team_resp.json()["id"]

    # Step 4: create the first project in that team.
    project_slug = f"bootstrap-project-{uuid.uuid4().hex[:10]}"
    project_resp = await client.post(
        "/v1/projects",
        json={
            "team_id": team_id,
            "name": "Bootstrap Project",
            "slug": project_slug,
            "git_url": "https://github.com/example/trustedoss-fixture.git",
        },
        headers=auth_headers,
    )
    assert project_resp.status_code == 201, (
        f"project creation failed after a successful team creation: "
        f"{project_resp.status_code} {project_resp.text}"
    )
    project_id = project_resp.json()["id"]

    # Step 5: register the first scan. `tests/conftest.py::_stub_enqueue_scan`
    # is autouse and replaces the real Celery dispatch with a deterministic
    # stub, so this exercises the HTTP + service layer without a live worker
    # (the "mock backend" the task description asks for).
    scan_resp = await client.post(
        f"/v1/projects/{project_id}/scans",
        json={"kind": "source"},
        headers=auth_headers,
    )
    assert scan_resp.status_code == 202, (
        f"scan registration failed after a successful project creation: "
        f"{scan_resp.status_code} {scan_resp.text}"
    )
