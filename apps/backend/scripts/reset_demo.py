"""
Demo dataset DAILY RESET — v2.1 Track B (B5 live demo).

The public live demo accumulates whatever logged-in visitors do (mostly reads,
but auth flows still touch refresh-token rows). To keep it pristine and to roll
forward any seed changes, a Cloud Scheduler → Cloud Run Job runs this script
once a day: it drops the demo dataset and reseeds it from
``scripts.seed_demo._seed`` (the single source of truth for the dataset shape).

Why a separate script (not ``seed_demo.py --reset``)
----------------------------------------------------
``seed_demo.py`` is intentionally idempotent and short-circuits when it finds an
existing ``demo-org`` — adding a destructive ``--reset`` flag to it would make a
"seed" command capable of deleting data, which is the wrong blast radius. This
script owns the destructive half and DELEGATES the (re)build to ``seed_demo``.

Scope + safety
--------------
  * **APP_ENV guard** — reuses ``seed_demo._refuse_outside_safe_env`` (allow-list
    ``dev`` / ``demo``). Refuses with exit 1 anywhere else (prod/test/staging).
    This is the same guard that protects the seed, so the reset can never run
    against a production database.
  * **Scoped, not a truncate** — we delete ONLY:
      - the ``demo-org`` Organization (FK cascade removes its teams → projects →
        scans → scan_components / findings / artifacts), and
      - the demo Users, matched by the stable ``@demo.trustedoss.dev`` email
        suffix (FK cascade removes their memberships / notifications / oauth
        identities / api keys).
    No global TRUNCATE; a co-tenant's data (if any) is untouched.
  * **Idempotent** — deleting a non-existent demo-org is a no-op, so a reset on
    an empty DB simply seeds. Running it twice in a row yields the same dataset.
  * **Transactional drop** — the deletes run in one transaction so a partial
    failure rolls back rather than leaving the demo half-deleted.

Exit codes (match seed_demo)
----------------------------
  0 — success (dropped + reseeded, or seeded onto an empty DB)
  1 — refused (APP_ENV not allowed) or runtime failure
  2 — argument error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Allow running from any cwd — add the backend root to sys.path so the same
# imports resolve as seed_demo.py / seed_e2e_user.py.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Reuse the seed module's guard, identifiers, and (re)build logic so the dataset
# shape stays single-sourced.
from scripts import seed_demo  # noqa: E402

# The stable email suffix every demo user shares (admin@ / *-admin@ / dev@).
_DEMO_EMAIL_SUFFIX = "@demo.trustedoss.dev"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Drop the demo dataset (demo-org + demo users) and reseed it. "
            "Idempotent. Allowed envs: dev, demo."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "Validate APP_ENV + parse args but skip all DB work. Used by the "
            "unit smoke test so it does not need a live Postgres."
        ),
    )
    return parser.parse_args(argv)


async def _drop_demo() -> dict[str, int]:
    """Delete the demo-org and demo users in one transaction.

    Returns a small summary of how many top-level rows were removed so the Job
    log shows what happened. Relies on ON DELETE CASCADE for the dependent rows
    (teams/projects/scans/findings under the org; memberships/notifications/
    oauth identities/api keys under the users).
    """
    # Defense-in-depth: re-check the env guard here so the helper cannot be
    # bypassed by a direct call (mirrors seed_demo._seed).
    seed_demo._refuse_outside_safe_env()

    from sqlalchemy import delete, func, select
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from core.config import database_url
    from models import Organization, User

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            async with session.begin():
                org_count = (
                    await session.execute(
                        select(func.count())
                        .select_from(Organization)
                        .where(Organization.slug == seed_demo._DEMO_ORG_SLUG)
                    )
                ).scalar_one()
                user_count = (
                    await session.execute(
                        select(func.count())
                        .select_from(User)
                        .where(User.email.like(f"%{_DEMO_EMAIL_SUFFIX}"))
                    )
                ).scalar_one()

                # Org delete cascades to teams → projects → scans → findings.
                await session.execute(
                    delete(Organization).where(
                        Organization.slug == seed_demo._DEMO_ORG_SLUG
                    )
                )
                # User delete cascades to memberships / notifications / oauth /
                # api keys. Scoped tightly to the demo email suffix so a real
                # user can never be swept up.
                await session.execute(
                    delete(User).where(User.email.like(f"%{_DEMO_EMAIL_SUFFIX}"))
                )
        return {"organizations_deleted": int(org_count), "users_deleted": int(user_count)}
    finally:
        await engine.dispose()


async def _reset() -> dict[str, Any]:
    """Drop then reseed; return the seed summary plus the drop counts."""
    dropped = await _drop_demo()
    seeded = await seed_demo._seed()
    return {"reset": True, "dropped": dropped, **seeded}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Primary gate — refuse before any DB work.
    seed_demo._refuse_outside_safe_env()

    if args.dry_run:
        print(
            json.dumps(
                {"reset": True, "dropped": {}, "users": [], "projects": [], "ok": True,
                 "dry_run": True}
            )
        )
        return 0

    try:
        summary = asyncio.run(_reset())
    except SystemExit:
        # _refuse_outside_safe_env raises SystemExit(1); propagate.
        raise
    except Exception as exc:  # noqa: BLE001 — top-level CLI handler
        print(f"reset_demo failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
