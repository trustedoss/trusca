"""
Alembic migration environment.

- DATABASE_URL is read at runtime via `core.config.database_url_sync()` so the
  same source of truth feeds both the app and migrations.
- The sync DSN (psycopg2) is used here because Alembic still drives migrations
  through the synchronous engine; the async driver belongs to the app runtime.
- target_metadata is None for now; Phase 1 wires it to the SQLAlchemy
  declarative base once domain models exist.
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

from core.config import database_url_sync  # noqa: E402  (import after sys.path tweak)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # populated when domain models land (Phase 1)


def _resolved_url() -> str:
    return database_url_sync()


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
