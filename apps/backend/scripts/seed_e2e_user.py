"""
E2E seed helper — Phase 2 PR #9 + Phase 3 PR #10.

The frontend e2e suite (``apps/frontend/tests/e2e/scan_flow.spec.ts``,
``apps/frontend/tests/e2e/project_detail.spec.ts``) needs a user with team
memberships and one or more projects so it can drive the project list,
detail, and scan progress flows. The auth surface has no team-creation
endpoint by design (Phase 3 work — onboarding wizard) and brand-new users
have no memberships, so the e2e cannot bootstrap itself purely via REST.

This script bridges the gap: invoked from a Playwright spec via
``child_process``, it creates an organization + team + user + membership +
``N`` projects directly against the live Postgres, then prints a JSON
summary to stdout that the test parses.

For PR #10 (Project Detail) the script optionally also seeds:

  * a single ``succeeded`` scan per project (``--with-scan``)
  * ``--component-count`` rows of components attached to that scan with a
    deterministic round-robin distribution across severity (critical,
    high, medium, low, info, none) and license_category (forbidden,
    conditional, allowed, unknown). Names are generated as ``{prefix}-N``
    so spec searches like ``searchComponents("react")`` can hit a known
    prefix without having to fetch the seeded id list.

Why a Python script and not Node? psycopg / asyncpg + the SQLAlchemy
factories (``tests._helpers``) are already available in this repo. Pulling
``pg`` into the frontend package just to seed a few rows would balloon the
dependency surface for one feature.

Usage:

    python3 apps/backend/scripts/seed_e2e_user.py \
        --project-names alpha,beta,gamma \
        --password 'Sup3rSecret!aabbccdd'

    python3 apps/backend/scripts/seed_e2e_user.py \
        --project-names ci-smoke \
        --with-scan \
        --component-count 200 \
        --component-prefix react

Output (stdout, single JSON line):

    {"email": "...", "password": "...", "user_id": "...",
     "team_id": "...", "project_names": ["alpha","beta","gamma"],
     "project_ids": ["...", "...", "..."],
     "scan_ids": ["...", "...", "..."],
     "component_count": 200}

Exit code: 0 on success, non-zero on any failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

# Allow running the script from any cwd — adds the backend root to sys.path
# so `from tests._helpers import ...` resolves.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Round-robin distributions used when --component-count > 0. Kept short so
# every bucket is hit even at small counts (n=12 → all severities touched
# at least twice).
_SEVERITY_CYCLE = ("critical", "high", "medium", "low", "info", "none")
_LICENSE_CATEGORY_CYCLE = ("forbidden", "conditional", "allowed", "unknown")


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
    parser.add_argument(
        "--with-scan",
        action="store_true",
        default=False,
        help=(
            "Seed a `succeeded` scan per project and wire it as "
            "project.latest_scan_id. Required when --component-count > 0."
        ),
    )
    parser.add_argument(
        "--component-count",
        type=int,
        default=0,
        help=(
            "Number of components to attach to the first project's scan. "
            "Default: 0 (no components seeded). Implies --with-scan."
        ),
    )
    parser.add_argument(
        "--component-prefix",
        default="comp",
        help=(
            "Name prefix for the seeded components. Component i is named "
            "'{prefix}-{i}'. Default: 'comp'. e2e search scenarios fix this "
            "to a known string (e.g. 'react') so they can match a row by "
            "substring without having to learn ids."
        ),
    )
    return parser.parse_args()


async def _seed(  # noqa: PLR0915 — a single linear seed routine reads better than 5 helpers
    *,
    project_names: list[str],
    email: str | None,
    password: str | None,
    with_scan: bool,
    component_count: int,
    component_prefix: str,
) -> dict[str, object]:
    """Create the org/team/user/membership/projects[/scans/components]."""
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from core.config import database_url
    from core.security import hash_password
    from models import (
        Component,
        ComponentVersion,
        License,
        LicenseFinding,
        Membership,
        Organization,
        Project,
        Scan,
        ScanComponent,
        Team,
        User,
        Vulnerability,
        VulnerabilityFinding,
    )

    chosen_password = password or f"Sup3rSecret!{uuid.uuid4().hex[:12]}"
    chosen_email = email or f"e2e-{uuid.uuid4().hex[:12]}@example.com"

    # --component-count implies --with-scan; we cannot attach components
    # without a scan to anchor on.
    if component_count > 0 and not with_scan:
        with_scan = True

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
            scan_ids: list[str] = []
            project_rows: list[Project] = []
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
                project_rows.append(project)

                if with_scan:
                    scan = Scan(
                        project_id=project.id,
                        kind="source",
                        status="succeeded",
                        progress_percent=100,
                        started_at=datetime.now(tz=UTC),
                        completed_at=datetime.now(tz=UTC),
                        scan_metadata={"seeded": True},
                    )
                    session.add(scan)
                    await session.commit()
                    await session.refresh(scan)
                    project.latest_scan_id = scan.id
                    project.updated_at = datetime.now(tz=UTC)
                    await session.commit()
                    await session.refresh(project)
                    scan_ids.append(str(scan.id))

            seeded_components = 0
            if component_count > 0 and project_rows:
                # Anchor every seeded component on the first project's scan.
                first_project = project_rows[0]
                anchor_scan_id = first_project.latest_scan_id
                assert anchor_scan_id is not None  # with_scan was forced True

                # Pre-create one license per category so we can attach a
                # license_finding deterministically per component.
                licenses_by_cat: dict[str, License] = {}
                for cat in _LICENSE_CATEGORY_CYCLE:
                    spdx = f"E2E-{cat[:4].upper()}-{suffix}"
                    licence = License(
                        spdx_id=spdx,
                        name=f"E2E License {cat}",
                        category=cat,
                    )
                    session.add(licence)
                    licenses_by_cat[cat] = licence
                await session.commit()
                for licence in licenses_by_cat.values():
                    await session.refresh(licence)

                # Pre-create one vulnerability per non-trivial severity. The
                # 'info' / 'none' buckets get no finding (so the component's
                # severity_max collapses to the absence of CVEs).
                vulns_by_severity: dict[str, Vulnerability] = {}
                for sev in ("critical", "high", "medium", "low"):
                    v = Vulnerability(
                        external_id=f"CVE-2099-{sev[:3].upper()}-{suffix}",
                        source="NVD",
                        severity=sev,
                        cvss_score=None,
                        summary=f"e2e seed CVE — {sev}",
                    )
                    session.add(v)
                    vulns_by_severity[sev] = v
                await session.commit()
                for v in vulns_by_severity.values():
                    await session.refresh(v)

                # Now create components in batches. We commit every 100 rows
                # so the connection isn't held with a huge in-memory tx.
                BATCH = 100
                for i in range(component_count):
                    cname = f"{component_prefix}-{i:05d}"
                    purl = f"pkg:npm/{cname}"
                    component = Component(
                        purl=purl,
                        package_type="npm",
                        name=cname,
                    )
                    session.add(component)
                    await session.flush()  # need component.id

                    cv = ComponentVersion(
                        component_id=component.id,
                        version="1.0.0",
                        purl_with_version=f"{purl}@1.0.0",
                    )
                    session.add(cv)
                    await session.flush()

                    sc = ScanComponent(
                        scan_id=anchor_scan_id,
                        component_version_id=cv.id,
                        direct=True,
                        raw_data={"seed_index": i},
                    )
                    session.add(sc)

                    # License — round-robin across the four categories.
                    cat = _LICENSE_CATEGORY_CYCLE[i % len(_LICENSE_CATEGORY_CYCLE)]
                    lf = LicenseFinding(
                        scan_id=anchor_scan_id,
                        component_version_id=cv.id,
                        license_id=licenses_by_cat[cat].id,
                        kind="concluded",
                        source_path=f"seed/{i}",
                    )
                    session.add(lf)

                    # Severity — round-robin across the six buckets.
                    sev = _SEVERITY_CYCLE[i % len(_SEVERITY_CYCLE)]
                    if sev in vulns_by_severity:
                        vf = VulnerabilityFinding(
                            scan_id=anchor_scan_id,
                            component_version_id=cv.id,
                            vulnerability_id=vulns_by_severity[sev].id,
                        )
                        session.add(vf)
                    # info / none → no VF, severity_max collapses to "none".

                    if (i + 1) % BATCH == 0:
                        await session.commit()
                        seeded_components = i + 1
                await session.commit()
                seeded_components = component_count

            return {
                "email": user.email,
                "password": chosen_password,
                "user_id": str(user.id),
                "team_id": str(team.id),
                "project_names": project_names,
                "project_ids": project_ids,
                "scan_ids": scan_ids,
                "component_count": seeded_components,
            }
    finally:
        await engine.dispose()


def main() -> int:
    args = _parse_args()
    project_names = [n.strip() for n in args.project_names.split(",") if n.strip()]
    if not project_names:
        print("at least one --project-name required", file=sys.stderr)
        return 2
    if args.component_count < 0:
        print("--component-count must be non-negative", file=sys.stderr)
        return 2

    try:
        summary = asyncio.run(
            _seed(
                project_names=project_names,
                email=args.email,
                password=args.password,
                with_scan=args.with_scan,
                component_count=args.component_count,
                component_prefix=args.component_prefix,
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
