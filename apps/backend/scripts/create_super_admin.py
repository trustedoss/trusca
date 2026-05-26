"""Bootstrap script — create the first super_admin user.

Invoked by ``scripts/install.sh`` inside the backend container as::

    docker-compose -f docker-compose.yml exec -T \\
      -e ADMIN_EMAIL=... \\
      -e ADMIN_PASSWORD=... \\
      backend python -m scripts.create_super_admin

Why env vars instead of CLI flags?
  - Avoids leaking the password into ``ps -ef`` / Docker container args.
  - Lets the wizard pipe the password without echoing it.

Idempotent (ensure-active semantics):
  - If a user with the email already exists AND is super_admin → ensure
    ``is_active=True`` (lift any stale deactivation) and return 0. This makes
    the script a safe recovery hatch when something in the dev/demo
    environment has deactivated the admin row (e.g. a stray integration test
    run against the dev DB), so the operator can simply re-run the same
    bootstrap command instead of opening a psql shell.
  - If the email exists but the user is NOT super_admin → SystemExit (the
    operator must explicitly demote / promote outside this script).
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import database_url
from core.security import hash_password
from models import User


async def _main() -> int:
    email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("ADMIN_PASSWORD", "")
    if not email:
        print("ADMIN_EMAIL env var is required", file=sys.stderr)
        return 2
    if len(password) < 12:
        print("ADMIN_PASSWORD must be at least 12 characters", file=sys.stderr)
        return 2

    engine = create_async_engine(database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            existing = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()

            if existing is not None:
                if existing.is_superuser:
                    # Ensure-active recovery hatch: a re-run with the same env
                    # vars must restore a usable admin even if the row was
                    # flipped to is_active=False by something else (an admin
                    # mistakenly deactivated it via the UI, a stray dev test
                    # tripped the `deactivate_user` path, etc.). We only touch
                    # is_active here — the password is left alone so a re-run
                    # is non-destructive for an admin who simply forgot they
                    # had been disabled.
                    if not existing.is_active:
                        existing.is_active = True
                        await session.commit()
                        print(f"super admin {email} reactivated")
                        return 0
                    print(f"super admin {email} already exists — noop")
                    return 0
                print(
                    f"user {email} exists but is not super_admin. "
                    "Promote / replace it manually before re-running this script.",
                    file=sys.stderr,
                )
                return 1

            session.add(
                User(
                    email=email,
                    hashed_password=hash_password(password),
                    full_name="Super Admin",
                    is_active=True,
                    is_superuser=True,
                )
            )
            await session.commit()
            print(f"created super admin {email}")
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
