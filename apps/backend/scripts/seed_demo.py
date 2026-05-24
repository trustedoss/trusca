"""
Demo SaaS dataset seed — Chore F (GCP Demo SaaS bundle).

Populates a fresh database with a realistic-looking demo dataset so a fresh
visitor lands on a portal that actually has projects, scans, CVEs, and
notifications to look at instead of an empty dashboard.

The script is **idempotent**: running twice yields the same dataset. We
identify "demo" rows by stable slugs (``Organization.slug='demo-org'`` etc.)
and short-circuit when we find them.

Allowed environments
--------------------

Like ``scripts/seed_e2e_user.py --super-admin``, this script can mint a
super-admin and is therefore guarded behind a runtime ``APP_ENV`` allow-list
(``dev`` / ``demo``). Any other value (``prod``, ``test``, ``staging``,
unset) refuses with exit code 1.

We use ``demo`` rather than ``ci`` because the Cloud Run backend deploy
sets ``APP_ENV=demo`` (see ``terraform/modules/cloud_run_backend/main.tf``).
``test`` is excluded specifically — the pytest suite must not invoke this
script as a side-effect.

Dataset shape
-------------

  * 1 organization        — "Demo Org"  (slug ``demo-org``)
  * 3 teams               — "Frontend" / "Backend" / "Security"
  * 5 users               — 1 super_admin + 3 team_admins + 1 developer
  * 5 projects            — assorted teams; each with 1 succeeded scan
  * 2 of 5 projects       — 10 fake CVEs each (2 critical / 3 high / 3 medium / 2 low)
                            + 5 license findings (mix of permissive / copyleft / forbidden)
  * 1 of 5 projects       — 3 in-app notifications (mix read / unread)

Output
------

A single JSON line on stdout matching the ``seed_e2e_user.py`` contract::

    {"users": [{"email": "...", "role": "...", "id": "..."}],
     "projects": [{"id": "...", "name": "...", "team": "..."}],
     "ok": true}

Exit codes
----------

  0 — success (or already seeded)
  1 — refused (APP_ENV not allowed, or runtime failure)
  2 — argument error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Allow running the script from any cwd — adds the backend root to sys.path
# so `from core.config import database_url` resolves the same way as
# seed_e2e_user.py.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Allowed APP_ENV values for running this script. Mirrors the
# ``--super-admin`` guard in seed_e2e_user.py with ``demo`` added so the
# Cloud Run deploy can run a one-off seed Job. ``test`` is intentionally
# excluded — the unit test suite must not invoke this script.
_ALLOWED_ENVS = frozenset({"dev", "demo"})

# Stable identifiers that we use to detect "already seeded" so the script
# is idempotent. Changing any of these counts as a fresh demo dataset.
_DEMO_ORG_SLUG = "demo-org"
_DEMO_SUPER_ADMIN_EMAIL = "admin@demo.trustedoss.dev"


def _resolve_demo_password() -> str:
    """Resolve the demo super-admin password at runtime (Chore O / M2).

    Resolution order:
      1. ``DEMO_SUPER_ADMIN_PASSWORD`` env var if set (must be ≥ 12 chars).
         This is the **recommended** path for the demo deploy: provision the
         value in Secret Manager (terraform ``demo_super_admin_password``) so
         the daily reset Job never has to generate or surface a credential.
      2. Random ``secrets.token_urlsafe(18)`` if ``APP_ENV`` is ``dev`` or
         ``demo``.
           * ``dev`` — the plaintext is printed once to stdout for local
             convenience (a developer's own machine, not a shared log sink).
           * ``demo`` (and any other allowed-but-not-dev env) — the plaintext
             is **never** logged (CLAUDE.md §5: passwords must not appear in
             logs in plaintext). The Cloud Run Job log captured the value on
             every nightly run, persisting it in Cloud Logging — a standing
             credential leak. Instead we emit a masked advisory event pointing
             the operator at the secret-backed path. The generated value is
             only written to the super-admin user row (hashed).
      3. ``RuntimeError`` for any other ``APP_ENV`` (production guard).

    Replaces the previously-hardcoded ``DemoAdmin2026!`` constant. Called
    at runtime per CLAUDE.md core rule #11 (no module-level env caching).
    """
    explicit = os.getenv("DEMO_SUPER_ADMIN_PASSWORD")
    if explicit:
        if len(explicit) < 12:
            raise RuntimeError(
                "DEMO_SUPER_ADMIN_PASSWORD must be at least 12 characters."
            )
        return explicit
    app_env = os.getenv("APP_ENV", "dev").lower()
    if app_env not in _ALLOWED_ENVS:
        raise RuntimeError(
            "DEMO_SUPER_ADMIN_PASSWORD is required when APP_ENV is "
            f"{app_env!r}; refusing to generate a random demo password "
            "outside dev/demo."
        )
    pw = secrets.token_urlsafe(18)
    if app_env == "dev":
        # Local dev only — print once to stdout for the developer's convenience.
        # This is a single-developer machine, not a shared/retained log sink.
        print(
            json.dumps(
                {
                    "event": "seed_demo.generated_password",
                    "email": _DEMO_SUPER_ADMIN_EMAIL,
                    "password": pw,
                }
            ),
            flush=True,
        )
    else:
        # demo (or any other allowed non-dev env): NEVER log the plaintext.
        # Emit a masked advisory so the operator knows a credential was minted
        # and how to make it stable (Secret Manager) instead of relying on a
        # value that used to leak into Cloud Logging on every reset.
        from core.logging import mask_pii  # local import — see module header

        print(
            json.dumps(
                {
                    "event": "seed_demo.generated_password",
                    "email": mask_pii(_DEMO_SUPER_ADMIN_EMAIL),
                    "password": "***",
                    "note": (
                        "A random demo super-admin password was generated and "
                        "is NOT logged. Set DEMO_SUPER_ADMIN_PASSWORD (via "
                        "Secret Manager / terraform demo_super_admin_password) "
                        "to pin a known, stable credential."
                    ),
                }
            ),
            flush=True,
        )
    return pw

# Realistic fake CVE catalog. Severity buckets match VULN_SEVERITY_VALUES.
# external_id format: CVE-YYYY-NNNNN. We use the 90000 range so the values
# never collide with a real CVE if these rows leak into search.
_CVE_BANK: tuple[tuple[str, str, str, str], ...] = (
    # (external_id, severity, summary, source)
    ("CVE-2024-99001", "critical", "Authenticated RCE in template renderer.", "NVD"),
    ("CVE-2024-99002", "critical", "Path traversal allows arbitrary file read.", "NVD"),
    ("CVE-2024-99003", "high", "Prototype pollution in deep-merge utility.", "GHSA"),
    ("CVE-2024-99004", "high", "ReDoS in URL parser regex.", "NVD"),
    ("CVE-2024-99005", "high", "SSRF via unvalidated webhook target.", "OSV"),
    ("CVE-2024-99006", "medium", "Open redirect in OAuth callback handler.", "NVD"),
    ("CVE-2024-99007", "medium", "XSS in user-controlled error message.", "GHSA"),
    ("CVE-2024-99008", "medium", "Timing attack in token comparison.", "NVD"),
    ("CVE-2024-99009", "low", "Verbose stack trace exposed on 500.", "NVD"),
    ("CVE-2024-99010", "low", "Outdated dependency notice.", "OSV"),
)

# Per-project CVE plan: 2 critical / 3 high / 3 medium / 2 low = 10.
_CVE_PLAN: tuple[str, ...] = tuple(cve[0] for cve in _CVE_BANK)

# Fake license catalog — mix of permissive, copyleft, forbidden.
_LICENSE_BANK: tuple[tuple[str, str, str], ...] = (
    # (spdx_id, name, category)
    ("MIT", "MIT License", "allowed"),
    ("Apache-2.0", "Apache License 2.0", "allowed"),
    ("BSD-3-Clause", "BSD 3-Clause", "allowed"),
    ("LGPL-2.1-only", "GNU Lesser General Public License v2.1", "conditional"),
    ("GPL-3.0-only", "GNU General Public License v3.0", "forbidden"),
)

# Demo component bank. The first five map 1:1 to _LICENSE_BANK so every
# license finding has a believable package name in the UI. All ten give each
# entry in _CVE_BANK (10 CVEs) its own component, so the scan_components
# uniqueness constraint (scan_id, component_version_id, dependency_path) never
# collides — the CVE loop indexes this bank by `cve_idx`, which runs 0..9.
_COMPONENT_BANK: tuple[tuple[str, str, str], ...] = (
    # (purl, package_type, name)
    ("pkg:npm/lodash", "npm", "lodash"),
    ("pkg:pypi/requests", "pypi", "requests"),
    ("pkg:maven/org.springframework/spring-core", "maven", "spring-core"),
    ("pkg:npm/readline-sync", "npm", "readline-sync"),
    ("pkg:pypi/pyyaml", "pypi", "PyYAML"),
    ("pkg:npm/axios", "npm", "axios"),
    ("pkg:pypi/jinja2", "pypi", "Jinja2"),
    ("pkg:maven/com.fasterxml.jackson.core/jackson-databind", "maven", "jackson-databind"),
    ("pkg:npm/minimist", "npm", "minimist"),
    ("pkg:golang/github.com/gin-gonic/gin", "golang", "gin"),
)

# Per-license obligations so the Obligations tab and the NOTICE-file generator
# have content in the demo. Obligations are a separate table keyed by license,
# surfaced only when their license appears in a project's latest scan — every
# spdx_id below is in _LICENSE_BANK, which the license-finding loop attaches to
# the two CVE-target projects.
_OBLIGATION_BANK: tuple[tuple[str, str, str], ...] = (
    # (spdx_id, kind, text)
    (
        "MIT",
        "notice",
        "Include the copyright notice and the permission notice in all copies "
        "or substantial portions of the software.",
    ),
    (
        "Apache-2.0",
        "notice",
        "Retain the NOTICE file and all attribution notices; state significant "
        "changes made to the files.",
    ),
    (
        "Apache-2.0",
        "patent",
        "Apache-2.0 includes an express patent grant that terminates for a party "
        "who initiates patent litigation over the work.",
    ),
    (
        "BSD-3-Clause",
        "notice",
        "Reproduce the copyright notice, the condition list, and the disclaimer "
        "in the documentation and other materials.",
    ),
    (
        "LGPL-2.1-only",
        "source_disclosure",
        "Provide the source of the LGPL library (or a written offer) and allow "
        "the end user to relink against a modified version.",
    ),
    (
        "GPL-3.0-only",
        "source_disclosure",
        "Convey the complete corresponding source under GPL-3.0 to every "
        "recipient of the binary.",
    ),
    (
        "GPL-3.0-only",
        "copyleft",
        "Keep the GPL-3.0 license text, copyright notices, and the 'no warranty' "
        "disclaimers intact in all conveyed copies.",
    ),
)


def _refuse_outside_safe_env() -> None:
    """Refuse to run when ``APP_ENV`` is not in the allow-list.

    Reads ``os.getenv("APP_ENV")`` at call time so monkeypatching the env
    after import flips the decision (CLAUDE.md core rule #11 — runtime env
    reads, no module-level caching).
    """
    current = (os.getenv("APP_ENV") or "").strip().lower()
    if current in _ALLOWED_ENVS:
        return
    allowed = sorted(_ALLOWED_ENVS)
    print(
        "Refusing to run seed_demo.py: APP_ENV="
        f"{current or '<unset>'} not in {{{', '.join(allowed)}}}. "
        "Set APP_ENV=demo (or dev) to override.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Seed a realistic demo dataset (org / teams / users / projects "
            "/ scans / CVEs / notifications). Idempotent. Allowed envs: dev, demo."
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


# ---------------------------------------------------------------------------
# Seed implementation.
# ---------------------------------------------------------------------------


async def _seed() -> dict[str, Any]:  # noqa: PLR0915 — single linear seed reads better than 6 helpers
    """Run the seed against the live Postgres pointed at by ``DATABASE_URL``.

    Returns a JSON-serializable summary that the caller prints as a single
    stdout line.
    """
    # Defense-in-depth: re-check the env guard inside _seed so the helper
    # cannot be bypassed by calling it directly (e.g. from a future test).
    _refuse_outside_safe_env()

    from sqlalchemy import select
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
        Notification,
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

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with factory() as session:
            # ── Idempotency guard ──────────────────────────────────────────
            existing_org = (
                await session.execute(
                    select(Organization).where(Organization.slug == _DEMO_ORG_SLUG)
                )
            ).scalar_one_or_none()
            if existing_org is not None:
                # Already seeded — collect the existing identifiers and
                # return the same JSON contract.
                return await _collect_existing_summary(session, existing_org)

            # ── Organization ───────────────────────────────────────────────
            org = Organization(name="Demo Org", slug=_DEMO_ORG_SLUG)
            session.add(org)
            await session.flush()

            # ── Teams (3) ──────────────────────────────────────────────────
            team_specs = [
                ("Frontend", "frontend"),
                ("Backend", "backend"),
                ("Security", "security"),
            ]
            teams: dict[str, Team] = {}
            for tname, tslug in team_specs:
                team = Team(organization_id=org.id, name=tname, slug=tslug)
                session.add(team)
                teams[tslug] = team
            await session.flush()

            # ── Users (5) ──────────────────────────────────────────────────
            hashed_password = hash_password(_resolve_demo_password())

            super_admin = User(
                email=_DEMO_SUPER_ADMIN_EMAIL,
                hashed_password=hashed_password,
                full_name="Demo Super Admin",
                is_active=True,
                is_superuser=True,
                is_verified=True,
            )
            session.add(super_admin)

            team_admin_users: dict[str, User] = {}
            for tslug, _team in teams.items():
                u = User(
                    email=f"{tslug}-admin@demo.trustedoss.dev",
                    hashed_password=hashed_password,
                    full_name=f"{tslug.title()} Admin",
                    is_active=True,
                    is_superuser=False,
                    is_verified=True,
                )
                session.add(u)
                team_admin_users[tslug] = u

            developer = User(
                email="dev@demo.trustedoss.dev",
                hashed_password=hashed_password,
                full_name="Demo Developer",
                is_active=True,
                is_superuser=False,
                is_verified=True,
            )
            session.add(developer)
            await session.flush()

            # Memberships: each team_admin -> their team; developer -> Backend.
            for tslug, admin_user in team_admin_users.items():
                session.add(
                    Membership(
                        user_id=admin_user.id,
                        team_id=teams[tslug].id,
                        role="team_admin",
                    )
                )
            session.add(
                Membership(
                    user_id=developer.id,
                    team_id=teams["backend"].id,
                    role="developer",
                )
            )
            # Super admin gets a developer membership in Frontend so team-scoped
            # endpoints have something to return when the demo super-admin
            # browses the UI as themselves.
            session.add(
                Membership(
                    user_id=super_admin.id,
                    team_id=teams["frontend"].id,
                    role="developer",
                )
            )
            await session.flush()

            # ── Licenses (shared catalog, idempotent on spdx_id) ───────────
            license_by_spdx: dict[str, License] = {}
            for spdx_id, name, category in _LICENSE_BANK:
                existing = (
                    await session.execute(select(License).where(License.spdx_id == spdx_id))
                ).scalar_one_or_none()
                if existing is None:
                    lic = License(spdx_id=spdx_id, name=name, category=category)
                    session.add(lic)
                    license_by_spdx[spdx_id] = lic
                else:
                    license_by_spdx[spdx_id] = existing
            await session.flush()

            # ── Obligations (idempotent on (license, kind)) ────────────────
            # Keyed to the shared license catalog so the Obligations tab and the
            # NOTICE-file download are populated, not empty, in the demo.
            for ob_spdx, ob_kind, ob_text in _OBLIGATION_BANK:
                ob_lic = license_by_spdx[ob_spdx]
                existing_ob = (
                    await session.execute(
                        select(Obligation).where(
                            Obligation.license_id == ob_lic.id,
                            Obligation.kind == ob_kind,
                        )
                    )
                ).scalar_one_or_none()
                if existing_ob is None:
                    session.add(
                        Obligation(license_id=ob_lic.id, kind=ob_kind, text=ob_text)
                    )
            await session.flush()

            # ── Components (shared catalog, idempotent on purl) ────────────
            component_by_purl: dict[str, Component] = {}
            cv_by_purl: dict[str, ComponentVersion] = {}
            for purl, ptype, cname in _COMPONENT_BANK:
                existing_c = (
                    await session.execute(select(Component).where(Component.purl == purl))
                ).scalar_one_or_none()
                if existing_c is None:
                    comp = Component(purl=purl, package_type=ptype, name=cname)
                    session.add(comp)
                    component_by_purl[purl] = comp
                else:
                    component_by_purl[purl] = existing_c
            await session.flush()

            for purl, _ptype, _cname in _COMPONENT_BANK:
                comp = component_by_purl[purl]
                pwv = f"{purl}@1.0.0"
                existing_cv = (
                    await session.execute(
                        select(ComponentVersion).where(ComponentVersion.purl_with_version == pwv)
                    )
                ).scalar_one_or_none()
                if existing_cv is None:
                    cv = ComponentVersion(
                        component_id=comp.id,
                        version="1.0.0",
                        purl_with_version=pwv,
                    )
                    session.add(cv)
                    cv_by_purl[purl] = cv
                else:
                    cv_by_purl[purl] = existing_cv
            await session.flush()

            # ── Vulnerabilities (shared catalog, idempotent on external_id) ─
            vuln_by_id: dict[str, Vulnerability] = {}
            for ext_id, severity, summary, source in _CVE_BANK:
                existing_v = (
                    await session.execute(
                        select(Vulnerability).where(Vulnerability.external_id == ext_id)
                    )
                ).scalar_one_or_none()
                if existing_v is None:
                    v = Vulnerability(
                        external_id=ext_id,
                        source=source,
                        severity=severity,
                        summary=summary,
                    )
                    session.add(v)
                    vuln_by_id[ext_id] = v
                else:
                    vuln_by_id[ext_id] = existing_v
            await session.flush()

            # ── Projects (5) — each with a succeeded scan ──────────────────
            project_specs: tuple[tuple[str, str, str], ...] = (
                # (name, slug, team_slug)
                ("portal-web", "portal-web", "frontend"),
                ("portal-mobile", "portal-mobile", "frontend"),
                ("portal-api", "portal-api", "backend"),
                ("scan-pipeline", "scan-pipeline", "backend"),
                ("vuln-feed", "vuln-feed", "security"),
            )
            projects: list[Project] = []
            scans: list[Scan] = []
            for pname, pslug, tslug in project_specs:
                project = Project(
                    team_id=teams[tslug].id,
                    name=pname,
                    slug=pslug,
                    description=f"Demo project — {pname}",
                    git_url=f"https://github.com/example/{pname}.git",
                    default_branch="main",
                    visibility="team",
                    created_by_user_id=team_admin_users[tslug].id,
                )
                session.add(project)
                projects.append(project)
            await session.flush()

            now = datetime.now(tz=UTC)
            for project in projects:
                scan = Scan(
                    project_id=project.id,
                    kind="source",
                    status="succeeded",
                    progress_percent=100,
                    started_at=now - timedelta(minutes=12),
                    completed_at=now - timedelta(minutes=4),
                    scan_metadata={"seeded_demo": True, "branch": "main"},
                )
                session.add(scan)
                scans.append(scan)
            await session.flush()
            for project, scan in zip(projects, scans, strict=True):
                project.latest_scan_id = scan.id
            await session.flush()

            # ── First two projects: 10 CVEs + 5 license findings each ─────
            cve_target_projects = projects[:2]
            for proj_idx, _project in enumerate(cve_target_projects):
                scan = scans[proj_idx]

                # 10 CVEs from the bank — every CVE attached to a different
                # component so the dependency view shows variety.
                for cve_idx, ext_id in enumerate(_CVE_PLAN):
                    purl, _ptype, _cname = _COMPONENT_BANK[cve_idx % len(_COMPONENT_BANK)]
                    cv = cv_by_purl[purl]
                    sc = ScanComponent(
                        scan_id=scan.id,
                        component_version_id=cv.id,
                        direct=cve_idx < 3,
                        dependency_path=f"./{_COMPONENT_BANK[cve_idx % len(_COMPONENT_BANK)][2]}",
                        raw_data={"demo_cve_index": cve_idx},
                    )
                    session.add(sc)
                    finding = VulnerabilityFinding(
                        scan_id=scan.id,
                        component_version_id=cv.id,
                        vulnerability_id=vuln_by_id[ext_id].id,
                        status="new",
                    )
                    session.add(finding)

                # 5 license findings — one per license in the bank.
                for lf_idx, (spdx_id, _name, _cat) in enumerate(_LICENSE_BANK):
                    purl = _COMPONENT_BANK[lf_idx][0]
                    cv = cv_by_purl[purl]
                    lf = LicenseFinding(
                        scan_id=scan.id,
                        component_version_id=cv.id,
                        license_id=license_by_spdx[spdx_id].id,
                        kind="concluded",
                        source_path=f"package.json#L{lf_idx + 1}",
                    )
                    session.add(lf)
            await session.flush()

            # ── Third project: 3 in-app notifications for the developer ───
            notif_project = projects[2]
            developer_id = developer.id
            notif_specs: tuple[tuple[str, str, str, bool], ...] = (
                (
                    "scan_completed",
                    f"Scan completed for {notif_project.name}",
                    "10 components observed, 0 critical CVEs.",
                    True,  # already read
                ),
                (
                    "cve_detected",
                    "New critical CVE in dependency",
                    "CVE-2024-99001 detected in lodash@1.0.0.",
                    False,  # unread
                ),
                (
                    "license_violation",
                    "Forbidden license detected",
                    "GPL-3.0-only found in spring-core@1.0.0.",
                    False,
                ),
            )
            for kind, title, body, is_read in notif_specs:
                read_at = now - timedelta(hours=1) if is_read else None
                session.add(
                    Notification(
                        user_id=developer_id,
                        kind=kind,
                        title=title,
                        body=body,
                        link=f"/projects/{notif_project.id}",
                        target_table="projects",
                        target_id=notif_project.id,
                        read_at=read_at,
                    )
                )

            await session.commit()

            # ── Build the summary that the orchestrator parses. ───────────
            users_summary: list[dict[str, str]] = [
                {
                    "id": str(super_admin.id),
                    "email": super_admin.email,
                    "role": "super_admin",
                },
            ]
            for tslug, admin_user in team_admin_users.items():
                users_summary.append(
                    {
                        "id": str(admin_user.id),
                        "email": admin_user.email,
                        "role": f"team_admin:{tslug}",
                    }
                )
            users_summary.append(
                {
                    "id": str(developer.id),
                    "email": developer.email,
                    "role": "developer",
                }
            )

            projects_summary = [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "team": next(ts for ts, t in teams.items() if t.id == p.team_id),
                }
                for p in projects
            ]

            return {
                "users": users_summary,
                "projects": projects_summary,
                "ok": True,
            }
    finally:
        await engine.dispose()


async def _collect_existing_summary(session: Any, org: Any) -> dict[str, Any]:
    """Build the same summary contract from an already-seeded database."""
    from sqlalchemy import select

    from models import Membership, Project, Team, User

    teams_rows = (
        (await session.execute(select(Team).where(Team.organization_id == org.id))).scalars().all()
    )
    team_id_to_slug = {t.id: t.slug for t in teams_rows}

    project_rows = (
        (
            await session.execute(
                select(Project).where(Project.team_id.in_(list(team_id_to_slug.keys())))
            )
        )
        .scalars()
        .all()
    )
    projects_summary = [
        {
            "id": str(p.id),
            "name": p.name,
            "team": team_id_to_slug.get(p.team_id, ""),
        }
        for p in project_rows
    ]

    users_summary: list[dict[str, str]] = []
    super_admin = (
        await session.execute(select(User).where(User.email == _DEMO_SUPER_ADMIN_EMAIL))
    ).scalar_one_or_none()
    if super_admin is not None:
        users_summary.append(
            {
                "id": str(super_admin.id),
                "email": super_admin.email,
                "role": "super_admin",
            }
        )

    membership_rows = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.team_id.in_(list(team_id_to_slug.keys())))
        )
    ).all()
    seen_emails = {u["email"] for u in users_summary}
    for membership, user in membership_rows:
        if user.email in seen_emails:
            continue
        users_summary.append(
            {
                "id": str(user.id),
                "email": user.email,
                "role": (
                    f"team_admin:{team_id_to_slug.get(membership.team_id, '')}"
                    if membership.role == "team_admin"
                    else membership.role
                ),
            }
        )
        seen_emails.add(user.email)

    return {
        "users": users_summary,
        "projects": projects_summary,
        "ok": True,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Primary gate — refuse before doing any DB work.
    _refuse_outside_safe_env()

    if args.dry_run:
        # The unit smoke test exercises this branch — APP_ENV guard +
        # argparse round-trip without touching the DB.
        print(json.dumps({"users": [], "projects": [], "ok": True, "dry_run": True}))
        return 0

    try:
        summary = asyncio.run(_seed())
    except SystemExit:
        # _refuse_outside_safe_env raises SystemExit(1); propagate.
        raise
    except Exception as exc:  # noqa: BLE001 — top-level CLI handler
        print(f"seed_demo failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
