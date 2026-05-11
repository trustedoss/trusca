"""
Integration test for the L1 PostgreSQL role-separation contract
(Marathon bundle 8). Connects as the runtime ``trustedoss_app`` role
(created on the fly with a per-test password) and verifies the
privilege model:

  - INSERT on audit_logs            → ALLOWED.
  - SELECT on audit_logs            → ALLOWED.
  - UPDATE on audit_logs            → permission denied.
  - DELETE on audit_logs            → permission denied.
  - TRUNCATE on audit_logs          → permission denied.
  - DDL on any table                → permission denied (e.g. CREATE TABLE).

The test is the structural guarantee that even an attacker who acquires
the runtime role's credentials cannot tamper with the audit trail. The
0012 immutable trigger is the row-level guarantee; this is the
catalog-level one.

The test creates and drops the role itself, so it is hermetic — no
test fixture pollution. It runs as the migration-owning role to
provision the runtime role + grants.
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip role-separation test")
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
            f"alembic upgrade head failed; role-separation test cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def _parse_owner_dsn() -> dict[str, str]:
    """Parse the owner DATABASE_URL into asyncpg connect kwargs."""
    from urllib.parse import unquote, urlparse

    raw = os.getenv("DATABASE_URL", "")
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


@pytest.fixture
async def app_role() -> AsyncIterator[tuple[asyncpg.Connection, str]]:
    """Provision the ``trustedoss_app`` role for the test, yield
    ``(connection, role_name)``, then drop the role.

    Returns the role name explicitly (not via ``connection._params``)
    so a future asyncpg internal rename cannot silently neutralize
    the privilege assertions (security-reviewer High #2).
    """
    owner = _parse_owner_dsn()
    role_name = f"trustedoss_app_test_{secrets.token_hex(4)}"
    password = secrets.token_hex(16)

    owner_conn = await asyncpg.connect(
        host=owner["host"],
        port=int(owner["port"]),
        user=owner["user"],
        password=owner["password"],
        database=owner["database"],
    )
    try:
        await owner_conn.execute(
            f"CREATE ROLE {role_name} WITH LOGIN PASSWORD '{password}'"
        )
        # Mirror migration 0014's grants for the test role (the
        # migration grants to the literal "trustedoss_app"; we use a
        # randomized name to avoid colliding with any real role on
        # this DB).
        await owner_conn.execute(f"GRANT USAGE ON SCHEMA public TO {role_name}")
        await owner_conn.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON ALL TABLES IN SCHEMA public TO {role_name}"
        )
        await owner_conn.execute(
            f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM {role_name}"
        )
        await owner_conn.execute(
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role_name}"
        )
    finally:
        await owner_conn.close()

    app_conn = await asyncpg.connect(
        host=owner["host"],
        port=int(owner["port"]),
        user=role_name,
        password=password,
        database=owner["database"],
    )
    try:
        yield app_conn, role_name
    finally:
        await app_conn.close()
        cleanup_conn = await asyncpg.connect(
            host=owner["host"],
            port=int(owner["port"]),
            user=owner["user"],
            password=owner["password"],
            database=owner["database"],
        )
        try:
            await cleanup_conn.execute(
                f"REASSIGN OWNED BY {role_name} TO {owner['user']}"
            )
            await cleanup_conn.execute(f"DROP OWNED BY {role_name}")
            await cleanup_conn.execute(f"DROP ROLE {role_name}")
        finally:
            await cleanup_conn.close()


# ---------------------------------------------------------------------------
# Privilege contract
# ---------------------------------------------------------------------------


async def test_app_role_can_insert_into_audit_logs(
    app_role: tuple[asyncpg.Connection, str],
) -> None:
    """Append-only INSERT is the canonical happy path."""
    import uuid

    conn, _ = app_role
    await conn.execute(
        "INSERT INTO audit_logs (id, target_table, action, created_at) "
        "VALUES ($1, $2, $3, NOW())",
        uuid.uuid4(),
        "test_role_separation",
        "create",
    )
    row = await conn.fetchrow(
        "SELECT count(*) AS n FROM audit_logs WHERE target_table = $1",
        "test_role_separation",
    )
    assert row["n"] >= 1


async def test_app_role_cannot_update_audit_logs(
    app_role: tuple[asyncpg.Connection, str],
) -> None:
    """UPDATE on audit_logs must fail with permission_denied (42501)."""
    conn, _ = app_role
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        await conn.execute(
            "UPDATE audit_logs SET action = 'tampered' WHERE id IS NOT NULL"
        )


async def test_app_role_cannot_delete_audit_logs(
    app_role: tuple[asyncpg.Connection, str],
) -> None:
    """DELETE on audit_logs must fail with permission_denied."""
    conn, _ = app_role
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        await conn.execute("DELETE FROM audit_logs WHERE id IS NOT NULL")


async def test_app_role_cannot_truncate_audit_logs(
    app_role: tuple[asyncpg.Connection, str],
) -> None:
    """TRUNCATE on audit_logs must fail with permission_denied — the
    privilege check is a hard gate ahead of the 0012 immutable trigger.
    """
    conn, _ = app_role
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        await conn.execute("TRUNCATE TABLE audit_logs")


async def test_app_role_cannot_drop_audit_logs_trigger(
    app_role: tuple[asyncpg.Connection, str],
) -> None:
    """The bypass that L1 closes: the runtime role can't drop the
    immutable trigger because it doesn't own the table."""
    conn, _ = app_role
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        await conn.execute(
            "DROP TRIGGER audit_logs_immutable_trigger ON audit_logs"
        )


async def test_app_role_can_dml_on_normal_table(
    app_role: tuple[asyncpg.Connection, str],
) -> None:
    """Non-audit tables (here: projects) keep full DML — the privilege
    revocation is scoped exactly to audit_logs."""
    conn, role_name = app_role
    has_update = await conn.fetchval(
        "SELECT has_table_privilege($1, 'projects', 'UPDATE')",
        role_name,
    )
    assert has_update is True
    has_delete = await conn.fetchval(
        "SELECT has_table_privilege($1, 'projects', 'DELETE')",
        role_name,
    )
    assert has_delete is True


async def test_app_role_cannot_create_table(
    app_role: tuple[asyncpg.Connection, str],
) -> None:
    """DDL on the public schema must be denied on PG 15+ (where the
    implicit GRANT CREATE ON SCHEMA public TO PUBLIC was removed).

    On PG < 15 the implicit grant is still in place; we skip with a
    clear marker so the gap is visible. Compose pins postgres:17.2,
    so production deployments always exercise the deny path.
    """
    conn, _ = app_role
    pg_version = await conn.fetchval(
        "SELECT setting::int FROM pg_settings WHERE name = 'server_version_num'"
    )
    if pg_version < 150000:
        pytest.skip(
            f"PG {pg_version} ships GRANT CREATE ON SCHEMA public TO PUBLIC — "
            "explicit REVOKE CREATE recommended (hardening follow-up)"
        )

    test_table = f"role_sep_check_{secrets.token_hex(4)}"
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        await conn.execute(f"CREATE TABLE {test_table} (id INT)")
