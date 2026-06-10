"""
Service-layer tests for `services/project_service.py` — Phase 2 PR #7.

These run against the live Postgres (DATABASE_URL must be set) because the
service is essentially "translate validated payloads into SQL" — mocking the
DB would test the mock, not the contract.

We exercise the service functions directly (no HTTP) to keep the assertions
focused on the domain rules:

    - team-scoped reads (list_projects)
    - IDOR guards (get_project across teams)
    - role gating (update / archive require team_admin)
    - super_admin bypasses team membership
    - slug uniqueness (409)
    - audit log entries on every mutation

File lives under tests/unit/ to match the project convention even though
"unit" here means "service-level" — the integration suite (tests/integration/)
is reserved for HTTP-level coverage.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
    principal_for,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip project service tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    """Ensure the schema is at head before any service test runs."""
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            f"alembic upgrade head failed; project service tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A fresh async session bound to the configured DATABASE_URL."""
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # Install the audit listener so service-layer mutations produce audit_logs
    # rows — this matches the production wiring.
    from core.audit import install_audit_listeners

    install_audit_listeners(factory)

    async with factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# create_project
# ---------------------------------------------------------------------------


async def test_create_project_persists_row_and_writes_audit_log(
    db_session: AsyncSession,
) -> None:
    from schemas.scan import ProjectCreate
    from services.project_service import create_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = principal_for(user, team_ids=[team.id], role="developer")

    payload = ProjectCreate(
        team_id=team.id,
        name="My Project",
        slug=f"my-project-{unique_suffix()}",
        description="hello",
    )
    project = await create_project(db_session, payload=payload, actor=actor)

    assert project.id is not None
    assert project.team_id == team.id
    assert project.created_by_user_id == user.id
    assert project.archived_at is None
    assert project.visibility == "team"

    # Audit log row exists for the project create. We search by diff
    # containment on the slug — a stable unique handle for this test's row.
    # (M-4: target_id is also populated for creates now; the dedicated
    # guard below asserts that.)
    rows = (
        await db_session.execute(
            text(
                "SELECT action, target_table, diff "
                "FROM audit_logs "
                "WHERE target_table = 'projects' "
                "  AND diff @> CAST(:match AS jsonb)"
            ),
            {"match": f'{{"slug": "{project.slug}"}}'},
        )
    ).all()
    assert rows, "expected an audit_logs row for the project create"
    assert any(r.action == "create" for r in rows)


async def test_create_project_duplicate_slug_raises_conflict(
    db_session: AsyncSession,
) -> None:
    from schemas.scan import ProjectCreate
    from services.project_service import ProjectSlugConflict, create_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = principal_for(user, team_ids=[team.id], role="developer")

    slug = f"dup-{unique_suffix()}"
    await create_project(
        db_session,
        payload=ProjectCreate(team_id=team.id, name="A", slug=slug),
        actor=actor,
    )

    with pytest.raises(ProjectSlugConflict):
        await create_project(
            db_session,
            payload=ProjectCreate(team_id=team.id, name="B", slug=slug),
            actor=actor,
        )


async def test_create_project_outsider_is_forbidden(
    db_session: AsyncSession,
) -> None:
    from schemas.scan import ProjectCreate
    from services.project_service import ProjectForbidden, create_project

    org = await make_organization(db_session)
    target_team = await make_team(db_session, organization=org)
    other_team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=other_team, role="developer")
    actor = principal_for(user, team_ids=[other_team.id], role="developer")

    with pytest.raises(ProjectForbidden):
        await create_project(
            db_session,
            payload=ProjectCreate(
                team_id=target_team.id,
                name="X",
                slug=f"x-{unique_suffix()}",
            ),
            actor=actor,
        )


async def test_create_project_super_admin_can_target_any_team(
    db_session: AsyncSession,
) -> None:
    from schemas.scan import ProjectCreate
    from services.project_service import create_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, team_ids=[], role="super_admin")

    project = await create_project(
        db_session,
        payload=ProjectCreate(team_id=team.id, name="Admin", slug=f"admin-{unique_suffix()}"),
        actor=actor,
    )
    assert project.team_id == team.id


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


async def test_list_projects_filters_to_actor_team_set(
    db_session: AsyncSession,
) -> None:
    from services.project_service import list_projects

    org = await make_organization(db_session)
    my_team = await make_team(db_session, organization=org)
    other_team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=my_team, role="developer")

    mine = await make_project(db_session, team=my_team)
    theirs = await make_project(db_session, team=other_team)

    actor = principal_for(user, team_ids=[my_team.id], role="developer")

    rows, total = await list_projects(db_session, actor=actor)
    ids = {row.id for row in rows}
    assert mine.id in ids
    assert theirs.id not in ids
    assert total >= 1


async def test_list_projects_excludes_archived_by_default(
    db_session: AsyncSession,
) -> None:
    from services.project_service import list_projects

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = principal_for(user, team_ids=[team.id], role="developer")

    live = await make_project(db_session, team=team)
    archived = await make_project(db_session, team=team, archived=True)

    rows, _ = await list_projects(db_session, actor=actor, team_id=team.id)
    ids = {row.id for row in rows}
    assert live.id in ids
    assert archived.id not in ids

    rows_with_archived, _ = await list_projects(
        db_session, actor=actor, team_id=team.id, include_archived=True
    )
    ids_with = {row.id for row in rows_with_archived}
    assert archived.id in ids_with


async def test_list_projects_with_no_team_membership_returns_empty(
    db_session: AsyncSession,
) -> None:
    from services.project_service import list_projects

    user = await make_user(db_session)
    actor = principal_for(user, team_ids=[], role="developer")

    rows, total = await list_projects(db_session, actor=actor)
    assert rows == []
    assert total == 0


async def test_list_projects_team_id_outside_actor_set_is_forbidden(
    db_session: AsyncSession,
) -> None:
    from services.project_service import ProjectForbidden, list_projects

    org = await make_organization(db_session)
    my_team = await make_team(db_session, organization=org)
    other_team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=my_team, role="developer")
    actor = principal_for(user, team_ids=[my_team.id], role="developer")

    with pytest.raises(ProjectForbidden):
        await list_projects(db_session, actor=actor, team_id=other_team.id)


async def test_list_projects_pagination_caps_size_and_returns_total(
    db_session: AsyncSession,
) -> None:
    from services.project_service import list_projects

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = principal_for(user, team_ids=[team.id], role="developer")

    for _ in range(3):
        await make_project(db_session, team=team)

    rows, total = await list_projects(db_session, actor=actor, team_id=team.id, page=1, size=2)
    assert len(rows) == 2
    assert total >= 3


async def test_list_projects_q_filter_substring_matches_name(
    db_session: AsyncSession,
) -> None:
    from services.project_service import list_projects

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = principal_for(user, team_ids=[team.id], role="developer")

    needle = unique_suffix()
    target = await make_project(db_session, team=team, name=f"haystack {needle} target")
    decoy = await make_project(db_session, team=team, name="something else")

    rows, _ = await list_projects(db_session, actor=actor, team_id=team.id, q=needle)
    ids = {row.id for row in rows}
    assert target.id in ids
    assert decoy.id not in ids


# ---------------------------------------------------------------------------
# get_project
# ---------------------------------------------------------------------------


async def test_get_project_other_team_is_forbidden(
    db_session: AsyncSession,
) -> None:
    from services.project_service import ProjectForbidden, get_project

    org = await make_organization(db_session)
    target_team = await make_team(db_session, organization=org)
    other_team = await make_team(db_session, organization=org)
    target_project = await make_project(db_session, team=target_team)

    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=other_team, role="developer")
    actor = principal_for(user, team_ids=[other_team.id], role="developer")

    with pytest.raises(ProjectForbidden):
        await get_project(db_session, project_id=target_project.id, actor=actor)


async def test_get_project_super_admin_bypasses_team_check(
    db_session: AsyncSession,
) -> None:
    from services.project_service import get_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, team_ids=[], role="super_admin")

    fetched = await get_project(db_session, project_id=project.id, actor=actor)
    assert fetched.id == project.id


async def test_get_project_unknown_id_raises_not_found(
    db_session: AsyncSession,
) -> None:
    import uuid as _uuid

    from services.project_service import ProjectNotFound, get_project

    user = await make_user(db_session, is_superuser=True)
    actor = principal_for(user, role="super_admin")

    with pytest.raises(ProjectNotFound):
        await get_project(db_session, project_id=_uuid.uuid4(), actor=actor)


# ---------------------------------------------------------------------------
# update_project
# ---------------------------------------------------------------------------


async def test_update_project_developer_is_forbidden(
    db_session: AsyncSession,
) -> None:
    from schemas.scan import ProjectUpdate
    from services.project_service import ProjectForbidden, update_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = principal_for(user, team_ids=[team.id], role="developer")

    with pytest.raises(ProjectForbidden):
        await update_project(
            db_session,
            project_id=project.id,
            payload=ProjectUpdate(name="renamed"),
            actor=actor,
        )


async def test_update_project_team_admin_can_update(
    db_session: AsyncSession,
) -> None:
    from schemas.scan import ProjectUpdate
    from services.project_service import update_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    admin = await make_user(db_session)
    await make_membership(db_session, user=admin, team=team, role="team_admin")
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    updated = await update_project(
        db_session,
        project_id=project.id,
        payload=ProjectUpdate(name="renamed", description="new"),
        actor=actor,
    )
    assert updated.name == "renamed"
    assert updated.description == "new"


async def test_update_project_super_admin_can_update_any_team(
    db_session: AsyncSession,
) -> None:
    from schemas.scan import ProjectUpdate
    from services.project_service import update_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, team_ids=[], role="super_admin")

    updated = await update_project(
        db_session,
        project_id=project.id,
        payload=ProjectUpdate(default_branch="develop"),
        actor=actor,
    )
    assert updated.default_branch == "develop"


async def test_update_project_clears_optional_field_when_set_to_none(
    db_session: AsyncSession,
) -> None:
    from schemas.scan import ProjectUpdate
    from services.project_service import update_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    project.description = "initial"
    await db_session.commit()
    await db_session.refresh(project)
    assert project.description == "initial"

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    updated = await update_project(
        db_session,
        project_id=project.id,
        payload=ProjectUpdate(description=None),
        actor=actor,
    )
    assert updated.description is None


# ---------------------------------------------------------------------------
# update_project — git credential (feature #18 Part B)
# ---------------------------------------------------------------------------


async def test_update_project_sets_git_credential_as_ciphertext(
    db_session: AsyncSession,
) -> None:
    """Setting git_credential stores a Fernet ciphertext (NOT the plaintext) and
    decrypt_secret recovers it; has_git_credential becomes True; no response
    field leaks the secret."""
    from core.crypto import decrypt_secret
    from schemas.scan import ProjectPublic, ProjectUpdate
    from services.project_service import update_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    admin = await make_user(db_session)
    await make_membership(db_session, user=admin, team=team, role="team_admin")
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    plaintext = "ghp_super_secret_pat_value_123"
    updated = await update_project(
        db_session,
        project_id=project.id,
        payload=ProjectUpdate(git_credential=plaintext),
        actor=actor,
    )

    # Column holds ciphertext, never the plaintext.
    assert updated.git_credential_encrypted is not None
    assert updated.git_credential_encrypted != plaintext
    assert plaintext not in updated.git_credential_encrypted
    # ...and it round-trips back to the plaintext.
    assert decrypt_secret(updated.git_credential_encrypted) == plaintext
    # The model property + the public read model report "configured".
    assert updated.has_git_credential is True

    public = ProjectPublic.model_validate(updated)
    assert public.has_git_credential is True
    dumped = public.model_dump()
    # The response NEVER carries the plaintext or the ciphertext.
    assert "git_credential" not in dumped
    assert "git_credential_encrypted" not in dumped
    assert plaintext not in str(dumped)
    assert updated.git_credential_encrypted not in str(dumped)


async def test_update_project_clears_git_credential(
    db_session: AsyncSession,
) -> None:
    """clear_git_credential=true sets the column back to NULL; has_git_credential
    becomes False."""
    from schemas.scan import ProjectUpdate
    from services.project_service import update_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    admin = await make_user(db_session)
    await make_membership(db_session, user=admin, team=team, role="team_admin")
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    # Arrange: a credential is set.
    await update_project(
        db_session,
        project_id=project.id,
        payload=ProjectUpdate(git_credential="ghp_to_be_removed"),
        actor=actor,
    )

    cleared = await update_project(
        db_session,
        project_id=project.id,
        payload=ProjectUpdate(clear_git_credential=True),
        actor=actor,
    )
    assert cleared.git_credential_encrypted is None
    assert cleared.has_git_credential is False


async def test_update_project_blank_credential_is_a_noop(
    db_session: AsyncSession,
) -> None:
    """A blank git_credential (and no clear flag) leaves the column unchanged."""
    from schemas.scan import ProjectUpdate
    from services.project_service import update_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    admin = await make_user(db_session)
    await make_membership(db_session, user=admin, team=team, role="team_admin")
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    # Set one first.
    await update_project(
        db_session,
        project_id=project.id,
        payload=ProjectUpdate(git_credential="ghp_keep_me"),
        actor=actor,
    )

    # A blank credential without clear must NOT wipe it.
    same = await update_project(
        db_session,
        project_id=project.id,
        payload=ProjectUpdate(git_credential="", name="renamed-x"),
        actor=actor,
    )
    assert same.has_git_credential is True
    assert same.name == "renamed-x"


async def test_update_project_credential_developer_is_forbidden(
    db_session: AsyncSession,
) -> None:
    """A developer (non-admin) cannot set the credential — role gate (403)."""
    from schemas.scan import ProjectUpdate
    from services.project_service import ProjectForbidden, update_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = principal_for(user, team_ids=[team.id], role="developer")

    with pytest.raises(ProjectForbidden):
        await update_project(
            db_session,
            project_id=project.id,
            payload=ProjectUpdate(git_credential="ghp_x"),
            actor=actor,
        )
    # And the column was never written.
    await db_session.refresh(project)
    assert project.git_credential_encrypted is None


async def test_update_project_credential_outsider_cannot_set_other_team(
    db_session: AsyncSession,
) -> None:
    """A non-member (even team_admin elsewhere) cannot set another team's
    credential — cross-team escalation guard (P0)."""
    from schemas.scan import ProjectUpdate
    from services.project_service import ProjectForbidden, update_project

    org = await make_organization(db_session)
    target_team = await make_team(db_session, organization=org)
    other_team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=target_team)

    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=other_team, role="team_admin")
    actor = principal_for(
        user,
        team_ids=[other_team.id],
        role="team_admin",
        team_roles={other_team.id: "team_admin"},
    )

    with pytest.raises(ProjectForbidden):
        await update_project(
            db_session,
            project_id=project.id,
            payload=ProjectUpdate(git_credential="ghp_cross_team"),
            actor=actor,
        )
    await db_session.refresh(project)
    assert project.git_credential_encrypted is None


async def test_update_project_credential_masked_in_audit_diff(
    db_session: AsyncSession,
) -> None:
    """The audit_logs diff for a credential change masks the ciphertext (***)."""
    from schemas.scan import ProjectUpdate
    from services.project_service import update_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    admin = await make_user(db_session)
    await make_membership(db_session, user=admin, team=team, role="team_admin")
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    plaintext = "ghp_audit_secret_value"
    updated = await update_project(
        db_session,
        project_id=project.id,
        payload=ProjectUpdate(git_credential=plaintext),
        actor=actor,
    )
    ciphertext = updated.git_credential_encrypted
    assert ciphertext is not None

    rows = (
        await db_session.execute(
            text(
                "SELECT diff FROM audit_logs "
                "WHERE target_table = 'projects' AND target_id = :pid "
                "  AND action = 'update'"
            ),
            {"pid": str(project.id)},
        )
    ).all()
    assert rows, "expected an update audit row for the credential change"
    # No audit diff may carry the plaintext or the ciphertext; when the column is
    # present in a diff it must be masked to '***'.
    for row in rows:
        diff = row.diff or {}
        serialized = str(diff)
        assert plaintext not in serialized
        assert ciphertext not in serialized
        if "git_credential_encrypted" in diff:
            assert diff["git_credential_encrypted"] == "***"


async def test_create_project_audit_row_backfills_target_id(
    db_session: AsyncSession,
) -> None:
    """M-4: create audit rows carry the server-generated PK as target_id.

    The PK is assigned by Postgres (gen_random_uuid()) during the flush, so
    the listener backfills target_id in after_flush — previously every
    ``action=create`` row landed with target_id NULL and "find the audit row
    for the object I just created" was impossible by id.
    """
    from schemas.scan import ProjectCreate
    from services.project_service import create_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = principal_for(user, team_ids=[team.id], role="developer")

    payload = ProjectCreate(
        team_id=team.id,
        name="Audit Target",
        slug=f"audit-target-{unique_suffix()}",
    )
    project = await create_project(db_session, payload=payload, actor=actor)

    rows = (
        await db_session.execute(
            text(
                "SELECT action, target_id, diff FROM audit_logs "
                "WHERE target_table = 'projects' AND action = 'create' "
                "  AND target_id = :pid"
            ),
            {"pid": str(project.id)},
        )
    ).all()
    assert len(rows) == 1, "expected exactly one create audit row addressable by id"
    row = rows[0]
    # The diff mirrors the backfilled PK so it stays self-consistent.
    assert (row.diff or {}).get("id") == str(project.id)


# ---------------------------------------------------------------------------
# archive_project
# ---------------------------------------------------------------------------


async def test_archive_project_sets_archived_at_and_writes_audit_log(
    db_session: AsyncSession,
) -> None:
    from services.project_service import archive_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    admin = await make_user(db_session)
    await make_membership(db_session, user=admin, team=team, role="team_admin")
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    archived = await archive_project(db_session, project_id=project.id, actor=actor)
    assert archived.archived_at is not None

    # Audit log row exists for the archive. M-5: a soft delete (archived_at
    # NULL -> ts) is recorded as ``action=archive`` (not a generic update) so
    # the "who deleted this project" audit query has a row to find.
    rows = (
        await db_session.execute(
            text(
                "SELECT action FROM audit_logs "
                "WHERE target_table = 'projects' AND target_id = :pid"
            ),
            {"pid": str(project.id)},
        )
    ).all()
    actions = {row.action for row in rows}
    assert "archive" in actions, f"expected an archive audit row, got actions={actions}"


async def test_archive_project_is_idempotent(db_session: AsyncSession) -> None:
    from services.project_service import archive_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team, archived=True)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    first_archived_at = project.archived_at
    again = await archive_project(db_session, project_id=project.id, actor=actor)
    assert again.archived_at == first_archived_at  # unchanged


async def test_archive_project_developer_is_forbidden(
    db_session: AsyncSession,
) -> None:
    from services.project_service import ProjectForbidden, archive_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = principal_for(user, team_ids=[team.id], role="developer")

    with pytest.raises(ProjectForbidden):
        await archive_project(db_session, project_id=project.id, actor=actor)


# ---------------------------------------------------------------------------
# H-1 regression — cross-team role escalation
# ---------------------------------------------------------------------------
#
# A user who is `team_admin` in team_a and `developer` in team_b must NOT be
# able to mutate team_b projects. Before the fix, `CurrentUser.role` was the
# highest role across memberships and `_can_write_project` only checked
# `project.team_id in actor.team_ids` + `actor.role == 'team_admin'`, which
# silently allowed the escalation. The fix consults `actor.team_roles` keyed
# by the project's own team_id.


async def test_team_admin_in_other_team_cannot_patch_this_team_project(
    db_session: AsyncSession,
) -> None:
    """Split-membership user must not patch project in team where they are only developer."""
    from schemas.scan import ProjectUpdate
    from services.project_service import ProjectForbidden, update_project

    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    project_a = await make_project(db_session, team=team_a)
    project_b = await make_project(db_session, team=team_b)

    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team_a, role="team_admin")
    await make_membership(db_session, user=user, team=team_b, role="developer")

    # Mirror what `_load_current_user` produces: per-team role mapping +
    # `role` = highest across memberships (= "team_admin").
    actor = principal_for(
        user,
        team_ids=[team_a.id, team_b.id],
        role="team_admin",
        team_roles={team_a.id: "team_admin", team_b.id: "developer"},
    )

    # Negative path: PATCH on team_b's project must be forbidden — the actor
    # is only a developer there, regardless of their team_a admin role.
    with pytest.raises(ProjectForbidden):
        await update_project(
            db_session,
            project_id=project_b.id,
            payload=ProjectUpdate(name="renamed-by-cross-team"),
            actor=actor,
        )

    # Positive control: PATCH on team_a's project still works — they really
    # are team_admin there.
    updated = await update_project(
        db_session,
        project_id=project_a.id,
        payload=ProjectUpdate(name="renamed-legit"),
        actor=actor,
    )
    assert updated.name == "renamed-legit"


async def test_team_admin_in_other_team_cannot_archive_this_team_project(
    db_session: AsyncSession,
) -> None:
    """Same split-membership setup, archive path."""
    from services.project_service import ProjectForbidden, archive_project

    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    project_a = await make_project(db_session, team=team_a)
    project_b = await make_project(db_session, team=team_b)

    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team_a, role="team_admin")
    await make_membership(db_session, user=user, team=team_b, role="developer")

    actor = principal_for(
        user,
        team_ids=[team_a.id, team_b.id],
        role="team_admin",
        team_roles={team_a.id: "team_admin", team_b.id: "developer"},
    )

    with pytest.raises(ProjectForbidden):
        await archive_project(db_session, project_id=project_b.id, actor=actor)

    # Positive control: archive on team_a's project succeeds.
    archived = await archive_project(db_session, project_id=project_a.id, actor=actor)
    assert archived.archived_at is not None
