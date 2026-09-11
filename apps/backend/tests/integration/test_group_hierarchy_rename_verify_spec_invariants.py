"""The verify-specs baseline still satisfies its own invariants after the
group-hierarchy rename (#401).

Ten checks across seven vendored spec modules (audit-log, dashboard,
docker-compose, gcp-deploy, quickstart, users-and-teams, projects) run raw
SQL against `teams` / `team_id`, which the group-hierarchy rollout (PR
#464) renamed to `groups` / `group_id`. That SQL lives in specs we don't
edit (PROVENANCE.md); tests/verify-specs/excluded.json excludes all ten with
a reason pointing at https://github.com/haksungjang/bug-hunter/issues/21.

This is the guard on our side: the same assertions, re-keyed to the current
schema, against a real demo seed. If a future migration touches any of
these tables/columns again without updating this file too, it fails here
instead of only as a silent nightly exclusion.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")


@pytest.fixture
async def _seeded(db_factory: async_sessionmaker[Any]) -> None:
    from scripts import seed_demo

    await seed_demo._seed()


async def _scalar(session: Any, sql: str) -> Any:
    return (await session.execute(text(sql))).scalar_one()


# TC-AUDIT-01-003: the 11-column existence list, re-keyed team_id -> group_id.
async def test_audit_logs_has_all_eleven_columns_including_group_id(
    _seeded: None, db_factory: async_sessionmaker[Any]
) -> None:
    async with db_factory() as session:
        count = await _scalar(
            session,
            "SELECT count(*) FROM information_schema.columns WHERE table_name='audit_logs' "
            "AND column_name IN ('id','created_at','actor_user_id','group_id','action',"
            "'target_table','target_id','request_id','diff','ip','user_agent')",
        )
    assert count == 11


# TC-AUDIT-01-005: at least one audit row keeps a null group_id (system events).
async def test_audit_logs_has_a_null_group_id_row(
    _seeded: None, db_factory: async_sessionmaker[Any]
) -> None:
    async with db_factory() as session:
        count = await _scalar(
            session, "SELECT count(*) FROM audit_logs WHERE group_id IS NULL"
        )
    assert count > 0


# TC-DASH-07-001 / TC-DASH-07-002: a group admin's dashboard sees exactly the
# non-archived projects their membership's group_id reaches.
@pytest.mark.parametrize(
    "email",
    ["backend-admin@demo.trustedoss.dev", "dev@demo.trustedoss.dev"],
)
async def test_dashboard_project_count_follows_group_membership(
    _seeded: None, db_factory: async_sessionmaker[Any], email: str
) -> None:
    async with db_factory() as session:
        count = await _scalar(
            session,
            "SELECT count(*) FROM projects p WHERE p.archived_at IS NULL AND "
            "p.group_id IN (SELECT m.group_id FROM memberships m JOIN users u "
            f"ON u.id=m.user_id WHERE u.email='{email}')",
        )
    assert count == 5


# TC-DCOMP-02-005 / TC-QSTART-04-002: the demo org/group/user/project/CVE
# counts the install-verification guides assert, re-keyed teams -> groups.
async def test_demo_seed_counts_match_the_install_guides(
    _seeded: None, db_factory: async_sessionmaker[Any]
) -> None:
    async with db_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT (SELECT count(*) FROM organizations WHERE name='Demo Org'), "
                    "(SELECT count(*) FROM groups t JOIN organizations o "
                    "ON t.organization_id=o.id WHERE o.name='Demo Org' AND t.name "
                    "IN ('Frontend','Backend','Security')), "
                    "(SELECT count(*) FROM users WHERE email IN "
                    "('admin@demo.trustedoss.dev','frontend-admin@demo.trustedoss.dev',"
                    "'backend-admin@demo.trustedoss.dev','security-admin@demo.trustedoss.dev',"
                    "'dev@demo.trustedoss.dev','explore@demo.trustedoss.dev')), "
                    "(SELECT count(*) FROM projects WHERE slug IN "
                    "('portal-web','portal-mobile','portal-api','scan-pipeline','vuln-feed')), "
                    "(SELECT count(*) FROM vulnerabilities WHERE external_id LIKE 'CVE-2024-990%')"
                )
            )
        ).one()
    assert tuple(row) == (1, 3, 6, 5, 10)


# TC-GCPDEPLOY-06-002 / TC-GCPDEPLOY-06-003: the five demo-org projects join
# through groups, and the two CVE-target projects carry the seeded finding
# counts, re-keyed teams -> groups.
async def test_gcp_deploy_projects_and_findings_join_through_groups(
    _seeded: None, db_factory: async_sessionmaker[Any]
) -> None:
    async with db_factory() as session:
        project_count = await _scalar(
            session,
            "SELECT count(*) FROM projects p JOIN groups t ON p.group_id=t.id "
            "JOIN organizations o ON t.organization_id=o.id WHERE o.slug='demo-org' "
            "AND p.name IN ('portal-web','portal-mobile','portal-api','scan-pipeline','vuln-feed')",
        )
        assert project_count == 5

        rows = (
            await session.execute(
                text(
                    "SELECT p.name, "
                    "(SELECT count(*) FROM vulnerability_findings vf "
                    "WHERE vf.scan_id=p.latest_scan_id), "
                    "(SELECT count(*) FROM license_findings lf "
                    "WHERE lf.scan_id=p.latest_scan_id) "
                    "FROM projects p JOIN groups t ON p.group_id=t.id "
                    "JOIN organizations o ON t.organization_id=o.id WHERE o.slug='demo-org' "
                    "AND p.name IN ('portal-web','portal-mobile') ORDER BY p.name"
                )
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        ("portal-mobile", 10, 5),
        ("portal-web", 10, 5),
    ]


# TC-USER-04-003-verify: the super-admin's Frontend membership, re-keyed
# team_id -> group_id.
async def test_super_admin_has_a_frontend_membership(
    _seeded: None, db_factory: async_sessionmaker[Any]
) -> None:
    async with db_factory() as session:
        count = await _scalar(
            session,
            "SELECT count(*) FROM memberships m JOIN users u ON u.id=m.user_id "
            "JOIN groups g ON g.id=m.group_id WHERE u.email='admin@demo.trustedoss.dev' "
            "AND g.slug='frontend'",
        )
    assert count == 1


# TC-PROJ-13-003: the unique key on projects is (group_id, slug), not
# (team_id, slug), uq_projects_group_slug after the rename.
async def test_projects_unique_key_is_on_group_id_and_slug(
    _seeded: None, db_factory: async_sessionmaker[Any]
) -> None:
    async with db_factory() as session:
        count = await _scalar(
            session,
            "SELECT count(*) FROM pg_indexes WHERE tablename='projects' "
            "AND indexdef ILIKE '%UNIQUE%' AND indexdef ~* '\\(group_id, slug\\)'",
        )
    assert count == 1
