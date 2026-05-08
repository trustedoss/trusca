"""
E2E seed helper — Phase 2 PR #9 + Phase 3 PR #10 + Phase 3 PR #11 + Phase 3 PR #13.

The frontend e2e suite (``apps/frontend/tests/e2e/scan_flow.spec.ts``,
``apps/frontend/tests/e2e/project_detail.spec.ts``,
``apps/frontend/tests/e2e/vulnerabilities.spec.ts``,
``apps/frontend/tests/e2e/obligations.spec.ts``) needs a user with team
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

For PR #13 (Obligations tab) the script optionally also seeds:

  * ``--with-obligations`` attaches a small obligation catalog to each of
    the seed-licenses created by ``--component-count``. Two obligations per
    license (kind + text + link) so distribution / list / NOTICE scenarios
    have meaningful rows. No-op when ``--component-count`` is 0 (no
    licenses are created in that mode).

For PR #11 (Vulnerabilities tab) the script optionally also seeds:

  * ``--vulnerability-count N`` distinct VulnerabilityFinding rows attached
    to fresh component_versions on the first project's scan. Each finding
    gets a fresh Vulnerability row with a deterministic severity + status
    mix. The default mix is::

        critical=2, high=5, medium=10, low=20, info=5, unknown=2

    Override the mix with ``--vulnerability-severity-mix
    'critical:N,high:N,...'`` (any unspecified bucket defaults to 0).
    Statuses cycle: 80% ``new``, 15% ``analyzing``, 5% ``not_affected`` so
    filter-by-status scenarios exercise multiple values.

Why a Python script and not Node? psycopg / asyncpg + the SQLAlchemy
factories (``tests._helpers``) are already available in this repo. Pulling
``pg`` into the frontend package just to seed a few rows would balloon the
dependency surface for one feature.

Usage:

    python3 apps/backend/scripts/seed_e2e_user.py \\
        --project-names alpha,beta,gamma \\
        --password 'Sup3rSecret!aabbccdd'

    python3 apps/backend/scripts/seed_e2e_user.py \\
        --project-names ci-smoke \\
        --with-scan \\
        --component-count 200 \\
        --component-prefix react

    python3 apps/backend/scripts/seed_e2e_user.py \\
        --project-names ci-vulns \\
        --with-scan \\
        --vulnerability-count 44

Output (stdout, single JSON line):

    {"email": "...", "password": "...", "user_id": "...",
     "team_id": "...", "project_names": ["alpha","beta","gamma"],
     "project_ids": ["...", "...", "..."],
     "scan_ids": ["...", "...", "..."],
     "component_count": 200,
     "vulnerability_count": 44}

Exit code: 0 on success, non-zero on any failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

# Environments where this seed script is allowed to mint a super-admin via
# ``--super-admin`` (security-reviewer F8 / CWE-489 Active Debug Code).
# Any other value of ``APP_ENV`` (production / staging / unset) refuses the
# operation. The unset case is a deliberate footgun-prevention default — a
# forgotten ``APP_ENV`` MUST NOT allow a super-admin to spawn from a
# convenience script that ended up in the prod image. Phase 7 PR #20 will
# additionally exclude ``scripts/`` from the prod Dockerfile build context.
_SUPER_ADMIN_ALLOWED_ENVS = frozenset({"dev", "test", "ci"})


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

# PR #11 — vulnerability seed mix.
# Sum across buckets is the default `--vulnerability-count` (44) so callers
# that do not pass --vulnerability-count get a sane out-of-the-box mix when
# they request --vulnerability-count by itself.
_DEFAULT_VULN_SEVERITY_MIX: dict[str, int] = {
    "critical": 2,
    "high": 5,
    "medium": 10,
    "low": 20,
    "info": 5,
    "unknown": 2,
}
# Status mix — 80% new, 15% analyzing, 5% not_affected.
_VULN_STATUS_CYCLE: tuple[str, ...] = (
    *(("new",) * 16),
    *(("analyzing",) * 3),
    *(("not_affected",) * 1),
)
_VULN_SEVERITY_VALUES: frozenset[str] = frozenset(
    {"critical", "high", "medium", "low", "info", "unknown"}
)

# PR #13 — obligation catalog seed.
# Two obligations per category-license. Kind matches the canonical ranking
# advertised by KNOWN_OBLIGATION_KINDS (schemas.obligation_detail) so the
# distribution payload renders in a stable order. Text and link are stubs;
# their length is comfortably > 50 chars so e2e content checks have material
# to grep against.
_OBLIGATIONS_BY_CATEGORY: dict[str, tuple[tuple[str, str, str], ...]] = {
    "forbidden": (
        (
            "copyleft",
            "Distribution requires releasing source code under the same license terms.",
            "https://example.invalid/policy/forbidden-copyleft",
        ),
        (
            "source-disclosure",
            "Customers must be granted access to the corresponding source code on demand.",
            "https://example.invalid/policy/forbidden-source",
        ),
    ),
    "conditional": (
        (
            "attribution",
            "You must include the original copyright notice in user-facing materials.",
            "https://example.invalid/policy/conditional-attribution",
        ),
        (
            "modifications",
            "Modified files must carry prominent notices of the changes made.",
            "https://example.invalid/policy/conditional-modifications",
        ),
    ),
    "allowed": (
        (
            "attribution",
            "Include the original copyright notice when redistributing source or binaries.",
            "https://example.invalid/policy/allowed-attribution",
        ),
        (
            "no-endorsement",
            "Do not use the project name or contributors to endorse derivative products.",
            "https://example.invalid/policy/allowed-no-endorsement",
        ),
    ),
    "unknown": (
        (
            "attribution",
            "License terms could not be determined automatically — preserve any attribution found.",
            "https://example.invalid/policy/unknown-attribution",
        ),
    ),
}


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
    parser.add_argument(
        "--with-obligations",
        action="store_true",
        default=False,
        help=(
            "Phase 3 PR #13. Attach a small obligation catalog (1-2 rows) "
            "to each seed-license created by --component-count. No-op when "
            "--component-count is 0 because no seed-licenses exist."
        ),
    )
    parser.add_argument(
        "--vulnerability-count",
        type=int,
        default=0,
        help=(
            "Phase 3 PR #11. Number of CVE findings to attach to the first "
            "project's scan. Each finding gets a fresh component_version + "
            "Vulnerability with a deterministic severity + status mix. "
            "Default: 0 (no findings seeded). Implies --with-scan."
        ),
    )
    parser.add_argument(
        "--vulnerability-severity-mix",
        default=None,
        help=(
            "Override the default severity mix for --vulnerability-count. "
            "Format: 'critical:N,high:N,medium:N,low:N,info:N,unknown:N'. "
            "Buckets not listed default to 0; the sum is clamped to "
            "--vulnerability-count. Default: 'critical:2,high:5,medium:10,"
            "low:20,info:5,unknown:2'."
        ),
    )
    # ── Phase 4 PR #13 — admin e2e fixtures ────────────────────────────────
    parser.add_argument(
        "--super-admin",
        action="store_true",
        default=False,
        help=(
            "Phase 4 PR #13. Mark the seeded primary user as a super-admin "
            "(``User.is_superuser=True``). Required for the admin-panel e2e "
            "scenarios that exercise ``/admin/users`` and ``/admin/teams``."
        ),
    )
    parser.add_argument(
        "--extra-members",
        type=int,
        default=0,
        help=(
            "Phase 4 PR #13. Seed N additional users with ``developer`` role "
            "in the same team as the primary user. Their emails follow "
            "``e2e-extra-{i}-<suffix>@example.com`` and they share the "
            "primary user's password. Output JSON gets an ``extra_members`` "
            "list with per-user ``user_id``/``email``/``role`` triples."
        ),
    )
    parser.add_argument(
        "--extra-team-admin",
        action="store_true",
        default=False,
        help=(
            "Phase 4 PR #13. When combined with --extra-members, the *first* "
            "extra user is given the ``team_admin`` role instead of "
            "``developer``. Used by the role-management scenarios."
        ),
    )
    return parser.parse_args()


def _parse_severity_mix(raw: str | None, *, total: int) -> dict[str, int]:
    """Parse the ``--vulnerability-severity-mix`` flag.

    Returns a dict keyed by severity bucket. Values are clamped to the
    requested total (truncating proportionally is not worth the
    complexity for an e2e seed; we just stop emitting once we've reached
    the count). Invalid buckets are ignored with a stderr warning.
    """
    if raw is None or not raw.strip():
        # Use the default mix scaled to `total` if the caller didn't override.
        default = dict(_DEFAULT_VULN_SEVERITY_MIX)
        default_sum = sum(default.values())
        if total == default_sum:
            return default
        # Caller asked for a non-default total — use the default ratios.
        out: dict[str, int] = {}
        remaining = total
        keys = list(default.keys())
        for i, key in enumerate(keys):
            if i == len(keys) - 1:
                out[key] = max(remaining, 0)
            else:
                share = round(default[key] * total / default_sum) if default_sum else 0
                share = max(0, min(share, remaining))
                out[key] = share
                remaining -= share
        return out

    parsed: dict[str, int] = {sev: 0 for sev in _VULN_SEVERITY_VALUES}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            print(f"ignoring malformed severity mix entry: {chunk!r}", file=sys.stderr)
            continue
        sev, _, n_raw = chunk.partition(":")
        sev = sev.strip().lower()
        if sev not in _VULN_SEVERITY_VALUES:
            print(f"ignoring unknown severity bucket: {sev!r}", file=sys.stderr)
            continue
        try:
            n = int(n_raw.strip())
        except ValueError:
            print(f"ignoring non-integer count in {chunk!r}", file=sys.stderr)
            continue
        parsed[sev] = max(parsed[sev], n)

    return parsed


async def _seed(  # noqa: PLR0915 — a single linear seed routine reads better than 5 helpers
    *,
    project_names: list[str],
    email: str | None,
    password: str | None,
    with_scan: bool,
    component_count: int,
    component_prefix: str,
    vulnerability_count: int = 0,
    vulnerability_severity_mix: str | None = None,
    with_obligations: bool = False,
    super_admin: bool = False,
    extra_members: int = 0,
    extra_team_admin: bool = False,
) -> dict[str, object]:
    """Create the org/team/user/membership/projects[/scans/components]."""
    # M2 — defense-in-depth: re-check APP_ENV inside _seed so the guard
    # cannot be bypassed by calling _seed() directly (e.g. from a test helper
    # that skips main()).  The check in main() is the primary gate; this one
    # catches accidental direct invocations.
    if super_admin:
        _refuse_super_admin_outside_safe_env()
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
        Obligation,
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
    # --vulnerability-count likewise implies --with-scan.
    if vulnerability_count > 0 and not with_scan:
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
                is_superuser=super_admin,
                is_verified=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            # Primary user always gets a developer membership in the team so
            # team-scoped flows (project list, scans) keep working. The
            # ``is_superuser`` flag is the source of truth for the admin
            # existence-hide guard, independent of this membership row.
            membership = Membership(
                user_id=user.id, team_id=team.id, role="developer"
            )
            session.add(membership)
            await session.commit()

            # Phase 4 PR #13 — extra members for admin e2e scenarios.
            extra_members_summary: list[dict[str, str]] = []
            if extra_members > 0:
                hashed = hash_password(chosen_password)
                for i in range(extra_members):
                    role = (
                        "team_admin"
                        if extra_team_admin and i == 0
                        else "developer"
                    )
                    extra_email = f"e2e-extra-{i}-{suffix}@example.com"
                    extra_user = User(
                        email=extra_email,
                        hashed_password=hashed,
                        full_name=f"E2E Extra User {i}",
                        is_active=True,
                        is_superuser=False,
                        is_verified=True,
                    )
                    session.add(extra_user)
                    await session.flush()  # need extra_user.id

                    extra_membership = Membership(
                        user_id=extra_user.id,
                        team_id=team.id,
                        role=role,
                    )
                    session.add(extra_membership)
                    extra_members_summary.append(
                        {
                            "user_id": str(extra_user.id),
                            "email": extra_user.email,
                            "role": role,
                        }
                    )
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
            seeded_obligations_count = 0
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

                # PR #13 — obligation catalog rows hanging off each seed
                # license. Only seeded when the caller asked for them so we
                # don't perturb existing PR #10 / PR #11 e2e fixtures that
                # don't expect obligations.
                if with_obligations:
                    for cat, licence in licenses_by_cat.items():
                        for kind, text, link in _OBLIGATIONS_BY_CATEGORY.get(cat, ()):
                            obligation = Obligation(
                                license_id=licence.id,
                                kind=kind,
                                text=text,
                                link=link,
                            )
                            session.add(obligation)
                            seeded_obligations_count += 1
                    if seeded_obligations_count:
                        await session.commit()

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

            seeded_vulnerabilities = 0
            if vulnerability_count > 0 and project_rows:
                first_project = project_rows[0]
                anchor_scan_id = first_project.latest_scan_id
                assert anchor_scan_id is not None  # with_scan was forced True

                mix = _parse_severity_mix(
                    vulnerability_severity_mix, total=vulnerability_count
                )
                # Build the seed plan: a flat list of `severity` values, one
                # per finding, ordered for deterministic output.
                seed_plan: list[str] = []
                for sev in ("critical", "high", "medium", "low", "info", "unknown"):
                    seed_plan.extend([sev] * mix.get(sev, 0))
                # Clamp / pad to the requested total. If the mix sum is less
                # than the count, pad with `low` (the most benign bucket).
                # If it's greater, truncate.
                if len(seed_plan) > vulnerability_count:
                    seed_plan = seed_plan[:vulnerability_count]
                while len(seed_plan) < vulnerability_count:
                    seed_plan.append("low")

                BATCH = 50
                for idx, sev in enumerate(seed_plan):
                    vname = f"vuln-{idx:05d}"
                    purl = f"pkg:npm/{vname}"
                    component = Component(
                        purl=f"{purl}-{suffix}",
                        package_type="npm",
                        name=vname,
                    )
                    session.add(component)
                    await session.flush()

                    cv = ComponentVersion(
                        component_id=component.id,
                        version="1.0.0",
                        purl_with_version=f"{purl}-{suffix}@1.0.0",
                    )
                    session.add(cv)
                    await session.flush()

                    sc = ScanComponent(
                        scan_id=anchor_scan_id,
                        component_version_id=cv.id,
                        direct=True,
                        raw_data={"vuln_seed_index": idx},
                    )
                    session.add(sc)

                    vuln = Vulnerability(
                        external_id=f"CVE-2099-VLN-{suffix}-{idx:05d}",
                        source="NVD",
                        severity=sev,
                        cvss_score=None,
                        summary=f"e2e seed vulnerability {idx} ({sev})",
                    )
                    session.add(vuln)
                    await session.flush()

                    status = _VULN_STATUS_CYCLE[idx % len(_VULN_STATUS_CYCLE)]
                    finding = VulnerabilityFinding(
                        scan_id=anchor_scan_id,
                        component_version_id=cv.id,
                        vulnerability_id=vuln.id,
                        status=status,
                        analysis_state=status,
                    )
                    session.add(finding)

                    if (idx + 1) % BATCH == 0:
                        await session.commit()
                await session.commit()
                seeded_vulnerabilities = vulnerability_count

            return {
                "email": user.email,
                "password": chosen_password,
                "user_id": str(user.id),
                "is_super_admin": bool(super_admin),
                "team_id": str(team.id),
                "project_names": project_names,
                "project_ids": project_ids,
                "scan_ids": scan_ids,
                "component_count": seeded_components,
                "vulnerability_count": seeded_vulnerabilities,
                "obligation_count": seeded_obligations_count,
                "extra_members": extra_members_summary,
            }
    finally:
        await engine.dispose()


def _refuse_super_admin_outside_safe_env() -> None:
    """
    Refuse to run when ``--super-admin`` is requested outside dev/test/ci.

    Security-reviewer F8 (CWE-489 Active Debug Code in Production):
    ``--super-admin`` writes ``is_superuser=True`` directly via the seed
    helper. If this script ever ships with the prod image and the on-call
    runs it by accident (e.g. for a "quick test"), a super-admin appears
    out of band — bypassing the audit trail's actor record and any
    onboarding flow.

    Read ``APP_ENV`` at runtime (CLAUDE.md core rule #11 — no module-level
    env caching). Default of "" / unset → refuse.
    """
    current_env = (os.getenv("APP_ENV") or "").strip().lower()
    if current_env in _SUPER_ADMIN_ALLOWED_ENVS:
        return
    allowed = sorted(_SUPER_ADMIN_ALLOWED_ENVS)
    print(
        "Refusing to create super-admin: APP_ENV="
        f"{current_env or '<unset>'} not in {{{', '.join(allowed)}}}. "
        "Set APP_ENV=dev to override.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def main() -> int:
    args = _parse_args()
    project_names = [n.strip() for n in args.project_names.split(",") if n.strip()]
    if not project_names:
        print("at least one --project-name required", file=sys.stderr)
        return 2
    if args.component_count < 0:
        print("--component-count must be non-negative", file=sys.stderr)
        return 2
    if args.vulnerability_count < 0:
        print("--vulnerability-count must be non-negative", file=sys.stderr)
        return 2
    if args.extra_members < 0:
        print("--extra-members must be non-negative", file=sys.stderr)
        return 2

    # F8 — gate the super-admin convenience path on a known-safe APP_ENV.
    # The check runs ONLY when --super-admin is requested; the rest of the
    # seed (project / component fixtures) is harmless without the flag.
    if args.super_admin:
        _refuse_super_admin_outside_safe_env()

    try:
        summary = asyncio.run(
            _seed(
                project_names=project_names,
                email=args.email,
                password=args.password,
                with_scan=args.with_scan,
                component_count=args.component_count,
                component_prefix=args.component_prefix,
                vulnerability_count=args.vulnerability_count,
                vulnerability_severity_mix=args.vulnerability_severity_mix,
                with_obligations=args.with_obligations,
                super_admin=args.super_admin,
                extra_members=args.extra_members,
                extra_team_admin=args.extra_team_admin,
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
