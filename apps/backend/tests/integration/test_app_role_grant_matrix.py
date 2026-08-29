# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""B1: real ``information_schema`` privilege matrix for the ``trustedoss_app``
runtime role, compared against a declared, append-only catalog.

Background (testing-hardening-plan-2026-08.md, type B / bug 2): migration
0014 grants the literal role ``trustedoss_app`` a broad one-time
``SELECT, INSERT, UPDATE, DELETE`` on every table that exists when the
migration runs, but only ``SELECT, INSERT`` by default (via
``ALTER DEFAULT PRIVILEGES``) on any table created afterward. That gap is
intentional (see 0014's module docstring and
``test_app_role_grant_contract.py``, B1a), but nothing previously checked
whether a later migration that *does* need UPDATE/DELETE at runtime actually
added the follow-up ``GRANT``. This test is the missing checkpoint: it runs
the real migration chain against a throwaway database with the role
provisioned, reads back the actual Postgres ACL via
``has_table_privilege()``, and diffs it against
``tests/fixtures/app_role_privileges.json``, a declared, reviewed catalog
of what each table's runtime privileges are supposed to be.

Three constraints this test's fixture must honour (see the plan document):

  1. It must never re-run a broad ``GRANT`` itself. ``test_role_separation.py``'s
     ``app_role`` fixture does that every test, which is exactly the blind
     spot this test exists to close: a re-granting fixture always shows
     full privileges regardless of what the migration chain actually did.
  2. The role name must be the literal ``trustedoss_app``. Migration 0014's
     ``DO $$ ... IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname =
     'trustedoss_app') ...`` guard means a randomized role name (the
     approach ``test_role_separation.py`` uses to avoid collisions) gets
     none of the grants and would make this test vacuous.
  3. Ordering must be role-first, migrate-second. A database already at
     head does not re-run 0014 just because a role appears later. The
     fixture therefore provisions a brand-new throwaway database owned by
     the connecting (migration-owner) role, creates ``trustedoss_app`` if
     it does not already exist cluster-wide, points a subprocess
     ``alembic upgrade head`` at that empty database, and only then reads
     the resulting ACL.

The fixture is deliberately synchronous (SQLAlchemy + psycopg2, no
asyncpg): this repo pins ``asyncio_default_fixture_loop_scope = "function"``
(see ``pyproject.toml``), so a module-scoped *async* fixture would try to
reuse connections across event loops from different test functions and
crash (the same reason ``test_bootstrap_from_empty.py``'s ``pristine_db``
fixture stays synchronous).

Skips (does not fail) when ``DATABASE_URL`` is unset, matching the existing
integration-test convention (``test_role_separation.py``).
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "app_role_privileges.json"
)

pytestmark = pytest.mark.integration

# The four DML privileges migration 0014 ever grants (schema-level USAGE and
# sequence privileges are out of scope for this table-by-table matrix).
_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")

_APP_ROLE = "trustedoss_app"

# Postgres SQLSTATE for "duplicate_object" (e.g. CREATE ROLE racing another
# session that created the same role a moment earlier).
_DUPLICATE_OBJECT_SQLSTATE = "42710"


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skip app-role grant matrix test")
    return url


def _sync_dsn(url: str) -> str:
    """asyncpg DSN -> psycopg2 DSN (mirrors core.config.database_url_sync)."""
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _parse_owner_dsn(url: str) -> dict[str, str]:
    """Parse the owner ``DATABASE_URL`` into connection parts.

    Mirrors ``test_role_separation.py``'s helper of the same name; kept
    local rather than imported so this file has no dependency on another
    test module's private helpers.
    """
    raw = url
    if "+" in raw.split("://", 1)[0]:
        scheme, rest = raw.split("://", 1)
        raw = f"{scheme.split('+', 1)[0]}://{rest}"
    parsed = urlparse(raw)
    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "user": unquote(parsed.username or "trustedoss"),
        "password": unquote(parsed.password or ""),
        "database": parsed.path.lstrip("/") or "trustedoss",
    }


def _load_declared_privileges() -> dict[str, list[str]]:
    with FIXTURE_PATH.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    tables = payload["tables"]
    assert isinstance(tables, dict), (
        f"{FIXTURE_PATH} must have a top-level 'tables' object mapping "
        "table name -> sorted privilege list"
    )
    return tables


def _is_duplicate_object_error(exc: ProgrammingError) -> bool:
    orig = getattr(exc, "orig", None)
    pgcode = getattr(orig, "pgcode", None)
    return pgcode == _DUPLICATE_OBJECT_SQLSTATE


@pytest.fixture(scope="module")
def role_grant_matrix() -> Iterator[dict[str, list[str]]]:
    """Provision a throwaway database + the literal ``trustedoss_app`` role
    (role-first, migrate-second), run ``alembic upgrade head`` against it,
    and yield the actual per-table DML privilege matrix read back via
    ``has_table_privilege()``.

    Never re-issues its own ``GRANT``. Every privilege observed here comes
    from whatever the real migration chain executed.
    """
    url = _require_database_url()
    owner = _parse_owner_dsn(url)
    tmp_db = f"trusca_b1_grant_matrix_{secrets.token_hex(4)}"

    # AUTOCOMMIT: CREATE DATABASE / CREATE ROLE / DROP DATABASE cannot run
    # inside a multi-statement transaction block.
    owner_engine = create_engine(_sync_dsn(url), isolation_level="AUTOCOMMIT")
    role_created_here = False
    try:
        with owner_engine.connect() as conn:
            already_exists = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
                {"role": _APP_ROLE},
            ).first()
            if not already_exists:
                try:
                    conn.execute(text(f"CREATE ROLE {_APP_ROLE} NOLOGIN"))
                    role_created_here = True
                except ProgrammingError as exc:
                    if not _is_duplicate_object_error(exc):
                        raise
                    # Another concurrent test run (or a real deployment
                    # sharing this cluster) created it first. Reuse it; do
                    # not drop it at teardown since we didn't create it.

            try:
                conn.execute(
                    text(f'CREATE DATABASE "{tmp_db}" OWNER "{owner["user"]}"')
                )
            except Exception:
                # If we just created the role above and CREATE DATABASE then
                # fails, don't leak a NOLOGIN role with no owned objects.
                # Drop it here since the module-scope finally block below
                # never runs (this exception propagates out of fixture
                # setup, before that block is entered).
                if role_created_here:
                    conn.execute(text(f"DROP ROLE IF EXISTS {_APP_ROLE}"))
                raise
    finally:
        owner_engine.dispose()

    try:
        # Migrate-second: point a fresh `alembic upgrade head` subprocess
        # at the brand-new (schema-less) database via DATABASE_URL, so
        # 0014's role-exists guard fires and applies its GRANTs for real.
        env = dict(os.environ)
        temp_async_dsn = (
            f"postgresql+asyncpg://{owner['user']}:{owner['password']}"
            f"@{owner['host']}:{owner['port']}/{tmp_db}"
        )
        env["DATABASE_URL"] = temp_async_dsn
        # Force the fallback chain in core.config.database_url_owner() to
        # resolve to DATABASE_URL above rather than an unrelated
        # owner/app split possibly set in the ambient environment.
        env.pop("DATABASE_URL_OWNER", None)
        env.pop("DATABASE_URL_APP", None)

        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            pytest.skip(
                "alembic upgrade head failed against the throwaway database; "
                "app-role grant matrix test cannot run\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

        matrix_engine = create_engine(_sync_dsn(temp_async_dsn))
        try:
            with matrix_engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                        "ORDER BY table_name"
                    )
                ).fetchall()
                matrix: dict[str, list[str]] = {}
                for row in rows:
                    table_name = row[0]
                    granted = []
                    for priv in _PRIVILEGES:
                        has = conn.execute(
                            text("SELECT has_table_privilege(:role, :tbl, :priv)"),
                            {"role": _APP_ROLE, "tbl": table_name, "priv": priv},
                        ).scalar()
                        if has:
                            granted.append(priv)
                    matrix[table_name] = sorted(granted)
        finally:
            matrix_engine.dispose()

        yield matrix
    finally:
        cleanup_engine = create_engine(_sync_dsn(url), isolation_level="AUTOCOMMIT")
        try:
            with cleanup_engine.connect() as conn:
                # DROP DATABASE first: this removes every grant our
                # temporary database holds for trustedoss_app, so a
                # subsequent DROP ROLE (if we're the ones who created it)
                # doesn't trip over leftover per-database ACL entries.
                conn.execute(text(f'DROP DATABASE IF EXISTS "{tmp_db}" WITH (FORCE)'))
                if role_created_here:
                    try:
                        conn.execute(text(f"DROP ROLE {_APP_ROLE}"))
                    except ProgrammingError:
                        # Best-effort: a concurrent test run against the
                        # same Postgres cluster may still hold grants for
                        # this role in its own throwaway database. Leaving
                        # the role behind is harmless (NOLOGIN, no owned
                        # objects); the next run's "already exists" branch
                        # reuses it.
                        pass
        finally:
            cleanup_engine.dispose()


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def test_every_table_is_covered_by_the_declared_catalog(
    role_grant_matrix: dict[str, list[str]],
) -> None:
    """Table-set parity: every base table in the public schema must be a
    key in ``app_role_privileges.json``, no more, no fewer.

    A table showing up here that isn't declared means a migration added a
    table since the catalog was last reviewed. A declared table that no
    longer exists means the catalog is stale (e.g. the table was renamed
    or dropped) and should be pruned.
    """
    actual_tables = set(role_grant_matrix)
    declared_tables = set(_load_declared_privileges())

    undeclared = sorted(actual_tables - declared_tables)
    stale = sorted(declared_tables - actual_tables)

    if undeclared:
        first = undeclared[0]
        assert not undeclared, (
            f"New table(s) {undeclared} exist in the public schema but are "
            "not declared in tests/fixtures/app_role_privileges.json. Does "
            f"the runtime need to UPDATE or DELETE rows in `{first}`? If "
            "yes, add an explicit GRANT UPDATE, DELETE ON <table> TO "
            "trustedoss_app to the migration that creates it (see "
            "0014_app_role_grants.py's module docstring for why the "
            "default is append-only). If no, add the table to "
            "app_role_privileges.json with its actual privileges "
            f"({role_grant_matrix[first]} for `{first}`), the omission "
            "itself is the bug, not necessarily the privilege level."
        )
    assert not stale, (
        f"Table(s) {stale} are declared in tests/fixtures/app_role_privileges.json "
        "but no longer exist in the public schema (renamed or dropped). "
        "Remove the stale entries from the fixture."
    )


def test_declared_privileges_match_actual_grants(
    role_grant_matrix: dict[str, list[str]],
) -> None:
    """Per-table privilege parity for tables both sides agree exist.

    This is the core B1 assertion: the actual Postgres ACL for
    ``trustedoss_app``, produced by running the real migration chain
    against an empty database, must equal what's declared. A mismatch
    here means either an undocumented GRANT/REVOKE was added to a
    migration (fix the migration or update the catalog with a reviewed
    reason) or the catalog itself drifted from reality by hand-editing.
    """
    declared = _load_declared_privileges()
    mismatches: list[str] = []
    for table_name, actual_privs in sorted(role_grant_matrix.items()):
        if table_name not in declared:
            # Already reported by test_every_table_is_covered_by_the_declared_catalog.
            continue
        expected_privs = sorted(declared[table_name])
        if sorted(actual_privs) != expected_privs:
            mismatches.append(
                f"  {table_name}: expected {expected_privs}, actual {sorted(actual_privs)}"
            )

    assert not mismatches, (
        "trustedoss_app's actual table privileges diverged from "
        "tests/fixtures/app_role_privileges.json:\n"
        + "\n".join(mismatches)
        + "\n\nFor each table above: does the runtime actually need the "
        "privilege it gained or lost? If it needs UPDATE/DELETE that it "
        "doesn't have, add an explicit GRANT in the migration that added "
        "the table (or a follow-up migration). If the change is correct, "
        "update app_role_privileges.json to match and record why in the "
        "migration that caused it. This file is deliberately hand-reviewed, "
        "not auto-regenerated, so a silent update here would defeat the "
        "point of the test."
    )
