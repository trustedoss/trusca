"""
Alembic migration environment.

- DATABASE_URL_OWNER (with DATABASE_URL fallback) is read at runtime via
  ``core.config.database_url_owner_sync()``. Alembic must connect as the
  table-owning role so DDL (CREATE / ALTER / DROP) succeeds — the
  runtime DML-only role (``trustedoss_app``) intentionally lacks those
  privileges (Marathon bundle 8 / L1).
- The sync DSN (psycopg2) is used here because Alembic still drives migrations
  through the synchronous engine; the async driver belongs to the app runtime.
- target_metadata is wired to `models.Base.metadata` so autogenerate sees
  every domain model (the `models` package imports each submodule for its
  metadata side effects).
- Forward-only policy: see versions/0001_init.py — `downgrade()` raises
  NotImplementedError per CLAUDE.md §6 (Migration policy).
- Concurrency guard (M4 + L1race): run_migrations_online() takes a
  transaction-scoped Postgres advisory lock before running migrations, so two
  concurrent `alembic upgrade head` runs (e.g. ``docker-compose up
  --scale backend=N`` or a K8s rollout) serialise instead of racing on DDL.
  This is migration *infrastructure*, not migration logic — it changes no
  schema, only who gets to apply it at a given instant.
- Secret hygiene (M1 / CLAUDE.md §5): the engine is created with
  ``hide_parameters=True`` so a failed statement's traceback never echoes the
  DSN / bound password into the logs.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool, text

from alembic import context

# ---------------------------------------------------------------------------
# Advisory-lock key (M4 + L1race).
#
# A single fixed 64-bit integer namespaces the migration lock. Postgres
# advisory locks are pure conventions keyed by this number — any session that
# asks for the SAME key contends; unrelated keys never collide. We derive the
# value from the ASCII bytes of "TOSSMIGR" (TrustedOSS MIGRation) so it is
# memorable, stable across releases, and unlikely to clash with an app-level
# advisory lock chosen elsewhere:
#   0x544F53534D494752 = b"TOSSMIGR" big-endian.
# Postgres advisory-lock keys are signed bigint, and 0x544F53534D494752
# already fits in the positive signed range (< 2**63), so no wraparound.
# DO NOT change this value: an in-flight migration on an old replica and a
# new replica must agree on the key to serialise correctly.
MIGRATION_ADVISORY_LOCK_KEY = 0x544F53534D494752  # b"TOSSMIGR", 6075166039590127442

# Make the backend root importable so we can pull in core.config.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.config import database_url_owner_sync  # noqa: E402  (import after sys.path tweak)
from models import Base  # noqa: E402  (import after sys.path tweak)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolved_url() -> str:
    # Use the owner DSN so DDL has table ownership. Falls back to
    # DATABASE_URL when DATABASE_URL_OWNER is unset (legacy / dev /
    # single-role deployments).
    return database_url_owner_sync()


def run_migrations_offline() -> None:
    context.configure(
        url=_resolved_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _resolved_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # M1 / CLAUDE.md §5 — never echo the DSN or bound params (incl. the
        # password) into a failed-statement traceback.
        hide_parameters=True,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        # M4 + L1race — serialise concurrent migration runs. A
        # transaction-scoped advisory lock (pg_advisory_xact_lock) blocks any
        # second `alembic upgrade head` until this transaction commits/rolls
        # back, then the loser finds the schema already at HEAD and no-ops.
        # _xact_ scope means the lock is released automatically with the
        # transaction even if a migration raises — no manual unlock / leak on
        # error. This makes --scale backend=N and K8s rollouts safe regardless
        # of replica count; it is a concurrency guard only and changes no
        # schema.
        with context.begin_transaction():
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:k)"),
                {"k": MIGRATION_ADVISORY_LOCK_KEY},
            )
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
