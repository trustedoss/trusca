# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The role check runs at boot and cannot itself prevent one (ER49).

The unit tests state what each verdict should be. These drive the real
lifespan, because the defects this replaced and the one found writing it both
lived in the wiring rather than the logic:

* the old check refused to start a single-role install whenever APP_ENV was
  prod, which the logic alone never showed because the logic was the env read;
* the first version of this check shared the boot connection with the probe, so
  a probe failure aborted the transaction and the NEXT statement raised. The
  probe was wrapped in try/except, and the app still could not start: the
  exception that was caught was not the one that broke it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests._db_required import migrate_to_head, require_database_url

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


async def _boot() -> None:
    """Run the real lifespan once."""
    import main as m

    async with m.lifespan(m.app):
        pass


async def test_a_single_role_install_boots(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ER49 regression.

    The test database is owned by the connecting role, which is exactly a
    single-role install: the runtime holds DDL. That must warn, not refuse.
    Before this change the same configuration raised at boot, and on
    Kubernetes it told the operator to check docker-compose wiring.
    """
    monkeypatch.delenv("REQUIRE_DB_ROLE_SEPARATION", raising=False)
    await _boot()


async def test_strict_mode_refuses_a_privileged_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opt-in enforcement for organizations that mandate the split."""
    monkeypatch.setenv("REQUIRE_DB_ROLE_SEPARATION", "true")
    with pytest.raises(RuntimeError, match="REQUIRE_DB_ROLE_SEPARATION"):
        await _boot()


async def test_a_failing_probe_does_not_stop_the_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This is the test that keeps the probe on its own connection.

    Wrapping the probe in try/except is NOT enough and that is the whole point:
    a failed statement poisons its transaction, so if the probe shares the boot
    connection every later statement raises InFailedSQLTransactionError and the
    app refuses to start. Merge the connections again and this test fails,
    which is the only thing standing between the next reader and that bug.
    """
    monkeypatch.delenv("REQUIRE_DB_ROLE_SEPARATION", raising=False)
    monkeypatch.setattr("core.db_role.DDL_PROBE_SQL", "SELECT no_such_function_er49()")
    await _boot()


async def test_the_probe_tells_a_privileged_role_from_a_dml_only_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe SQL itself, run verbatim as each kind of role.

    Both halves execute ``DDL_PROBE_SQL`` unchanged, which is what makes this
    pin the privilege it asks about. An earlier version wrote its own query for
    the DML-only role and so kept passing when the probe was changed to ask
    about INSERT, a privilege the runtime role holds too and which therefore
    cannot distinguish anything.

    The DML-only role is deliberately NOT named ``trustedoss_app``: a name
    comparison would pass while being wrong for every external database whose
    role is called something else.
    """
    import secrets
    from urllib.parse import urlparse

    import asyncpg

    from core.db import build_engine
    from core.db_role import DDL_PROBE_SQL

    monkeypatch.delenv("REQUIRE_DB_ROLE_SEPARATION", raising=False)
    role = f"er49_probe_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(16)
    parsed = urlparse(require_database_url().replace("+asyncpg", ""))
    database = (parsed.path or "/").lstrip("/")

    engine = build_engine()
    try:
        async with engine.begin() as conn:
            owner_holds_ddl = (await conn.execute(text(DDL_PROBE_SQL))).scalar()
            await conn.execute(
                text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'")
            )
            await conn.execute(
                text(f'GRANT CONNECT ON DATABASE "{database}" TO "{role}"')
            )
            await conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            await conn.execute(
                text(f'GRANT SELECT, INSERT ON audit_logs TO "{role}"')
            )

        conn_as_role = await asyncpg.connect(
            user=role,
            password=password,
            host=parsed.hostname or "localhost",
            port=parsed.port or 5432,
            database=database,
        )
        try:
            dml_only_holds_ddl = await conn_as_role.fetchval(DDL_PROBE_SQL)
        finally:
            await conn_as_role.close()

        assert owner_holds_ddl is True, (
            "the owning role must read as privileged, or an unseparated "
            "deployment is reported as separated"
        )
        assert dml_only_holds_ddl is False, (
            "a role granted only SELECT/INSERT must not read as privileged, or "
            "the probe cannot tell a separated deployment from an unseparated one"
        )
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'REVOKE ALL ON audit_logs FROM "{role}"'))
            await conn.execute(text(f'REVOKE ALL ON SCHEMA public FROM "{role}"'))
            await conn.execute(
                text(f'REVOKE ALL ON DATABASE "{database}" FROM "{role}"')
            )
            await conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        await engine.dispose()
