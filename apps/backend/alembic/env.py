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
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

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
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
