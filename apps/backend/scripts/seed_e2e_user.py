"""
E2E seed helper — Phase 2 PR #9.

The frontend e2e suite (``apps/frontend/tests/e2e/scan_flow.spec.ts``) needs
a user with team memberships and one or more projects so it can drive the
project list + scan progress flows. The auth surface has no
team-creation endpoint by design (Phase 3 work — onboarding wizard) and
brand-new users have no memberships, so the e2e cannot bootstrap itself
purely via REST.

This script bridges the gap: invoked from a Playwright spec via
``child_process``, it creates an organization + team + user + membership +
``N`` projects directly against the live Postgres, then prints a JSON
summary to stdout that the test parses.

Why a Python script and not Node? psycopg / asyncpg + the SQLAlchemy
factories (``tests._helpers``) are already available in this repo. Pulling
``pg`` into the frontend package just to seed a few rows would balloon the
dependency surface for one feature.

Usage:

    python3 apps/backend/scripts/seed_e2e_user.py \
        --project-names alpha,beta,gamma \
        --password 'Sup3rSecret!aabbccdd'

Output (stdout, single JSON line):

    {"email": "...", "password": "...", "user_id": "...",
     "team_id": "...", "project_names": ["alpha","beta","gamma"],
     "project_ids": ["...", "...", "..."]}

Exit code: 0 on success, non-zero on any failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

# Allow running the script from any cwd — adds the backend root to sys.path
# so `from tests._helpers import ...` resolves.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed an e2e user + projects.")
    parser.add_argument(
        "--project-names",
        default="alpha",
        help="Comma-separated project names. Default: 'alpha'.",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Override the seeded password. Default: random strong password.",
    )
    parser.add_argument(
        "--email",
        default=None,
        help="Override the seeded email. Default: e2e-<uuid>@example.com.",
    )
    return parser.parse_args()


async def _seed(
    *,
    project_names: list[str],
    email: str | None,
    password: str | None,
) -> dict[str, object]:
    """Create the org/team/user/membership/projects. Return a JSON summary."""
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from core.config import database_url
    from core.security import hash_password
    from models import Membership, Organization, Project, Team, User

    chosen_password = password or f"Sup3rSecret!{uuid.uuid4().hex[:12]}"
    chosen_email = email or f"e2e-{uuid.uuid4().hex[:12]}@example.com"

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            suffix = uuid.uuid4().hex[:10]
            org = Organization(name=f"E2E Org {suffix}", slug=f"e2e-org-{suffix}")
            session.add(org)
            await session.commit()
            await session.refresh(org)

            team = Team(
                organization_id=org.id,
                name=f"E2E Team {suffix}",
                slug=f"e2e-team-{suffix}",
            )
            session.add(team)
            await session.commit()
            await session.refresh(team)

            user = User(
                email=chosen_email.strip().lower(),
                hashed_password=hash_password(chosen_password),
                full_name="E2E Seed User",
                is_active=True,
                is_superuser=False,
                is_verified=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            membership = Membership(
                user_id=user.id, team_id=team.id, role="developer"
            )
            session.add(membership)
            await session.commit()

            project_ids: list[str] = []
            for name in project_names:
                slug = f"{name.lower()}-{uuid.uuid4().hex[:6]}"
                project = Project(
                    team_id=team.id,
                    name=name,
                    slug=slug,
                    description=f"Seeded for e2e — {name}",
                    git_url=None,
                    default_branch="main",
                    visibility="team",
                    created_by_user_id=user.id,
                )
                session.add(project)
                await session.commit()
                await session.refresh(project)
                project_ids.append(str(project.id))

            return {
                "email": user.email,
                "password": chosen_password,
                "user_id": str(user.id),
                "team_id": str(team.id),
                "project_names": project_names,
                "project_ids": project_ids,
            }
    finally:
        await engine.dispose()


def main() -> int:
    args = _parse_args()
    project_names = [n.strip() for n in args.project_names.split(",") if n.strip()]
    if not project_names:
        print("at least one --project-name required", file=sys.stderr)
        return 2

    try:
        summary = asyncio.run(
            _seed(
                project_names=project_names,
                email=args.email,
                password=args.password,
            )
        )
    except Exception as exc:  # noqa: BLE001 — top-level CLI handler
        print(f"seed failed: {exc}", file=sys.stderr)
        return 1

    # Single-line JSON so the caller can parse one stdout line trivially.
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
