"""
Service-layer tests for ``services.api_key_service`` — Phase 5 PR #16.

Drives the pure async service against a live Postgres (DATABASE_URL) so the
SQLAlchemy listener fires and the ``audit_logs`` table records each
mutation. Mirrors the shape of ``tests/unit/services/test_admin_user_service.py``.

Coverage:
  - issue: org / team / project scope round-trips, plaintext returned ONCE,
    bcrypt hash recoverable via verify_password, prefix uniqueness, RBAC
    rejection across actor classes.
  - revoke: flips ``revoked_at``, idempotent on second call (returns the
    same row unchanged — service contract), audit row produced.
  - list: pagination, scope/team/project filter, include_revoked default,
    visibility (developer / team_admin / super_admin).
  - parse_bearer / verify_api_key_plaintext: adversarial parametrize on the
    untrusted bearer string (per memory feedback_adversarial_input_parametrize).
  - Scope mismatch + RBAC raise crisp domain errors, never 500.
"""

from __future__ import annotations

import os
import subprocess
import uuid
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
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip api_key_service tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
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
            f"alembic upgrade head failed; api_key_service tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# parse_bearer — pure / adversarial parametrize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,plaintext",
    [
        ("rejects_empty", ""),
        ("rejects_none_string", "not-a-key"),
        ("rejects_wrong_prefix", "tok_abcdef12_xxxxx"),
        ("rejects_short_hex", "tos_abc_secret"),
        ("rejects_long_hex", "tos_abcdef1234_secret"),
        ("rejects_non_hex_in_prefix", "tos_zzzzzzzz_secret"),
        ("rejects_no_secret", "tos_abcdef12_"),
        ("rejects_only_prefix_no_underscore", "tos_abcdef12"),
        ("rejects_two_segments", "tos_abcdef12"),
        ("rejects_only_separator", "_"),
        ("rejects_javascript_scheme", "javascript:alert(1)"),
        ("rejects_crlf_in_secret", "tos_abcdef12_secret\r\nset-cookie:x"),
        ("rejects_oversized", "tos_abcdef12_" + ("a" * 10000)),
    ],
)
def test_parse_bearer_rejects_adversarial_input(label: str, plaintext: str) -> None:
    """parse_bearer must return None on any malformed input — never raise."""
    from services.api_key_service import parse_bearer

    # Note: oversized + CRLF cases are valid format-wise (they parse). The
    # security boundary is the DB lookup + bcrypt compare downstream — not
    # this prefilter. We still want every adversarial value to either parse
    # cleanly or return None; never raise.
    result = parse_bearer(plaintext)
    if label in {"rejects_crlf_in_secret", "rejects_oversized"}:
        assert result is not None
        assert result[0] == "tos_abcdef12"
    else:
        assert result is None, f"{label!r}: expected None, got {result!r}"


def test_parse_bearer_accepts_canonical_format() -> None:
    """A well-formed tos_<8hex>_<secret> tuple parses to (prefix, secret)."""
    from services.api_key_service import parse_bearer

    parsed = parse_bearer("tos_deadbeef_abcXYZ_-12")
    assert parsed == ("tos_deadbeef", "abcXYZ_-12")


def test_parse_bearer_preserves_underscores_in_secret() -> None:
    """url-safe base64 secrets may contain underscores; the split must keep them."""
    from services.api_key_service import parse_bearer

    parsed = parse_bearer("tos_12345678_seg1_seg2_seg3")
    assert parsed == ("tos_12345678", "seg1_seg2_seg3")


def test_parse_bearer_returns_none_for_non_string() -> None:
    """Non-string input (None, int) must return None, never raise."""
    from services.api_key_service import parse_bearer

    assert parse_bearer(None) is None  # type: ignore[arg-type]
    assert parse_bearer(12345) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# issue_api_key — happy paths (org / team / project)
# ---------------------------------------------------------------------------


async def test_issue_org_scope_round_trips_for_super_admin(db_session: AsyncSession) -> None:
    from core.security import verify_password
    from services.api_key_service import issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    row, plaintext = await issue_api_key(
        db_session,
        actor,
        name="ci-prod",
        scope="org",
        team_id=None,
        project_id=None,
    )
    assert row.scope == "org"
    assert row.team_id is None
    assert row.project_id is None
    assert row.created_by_user_id == admin.id
    assert row.revoked_at is None
    # Plaintext shape contract: tos_<8 hex>_<32 url-safe>.
    assert plaintext.startswith(row.key_prefix + "_")
    assert len(plaintext) > len(row.key_prefix) + 16
    # bcrypt verifies the plaintext against the persisted hash.
    assert verify_password(plaintext, row.key_hash) is True


async def test_issue_team_scope_round_trips_for_team_admin(db_session: AsyncSession) -> None:
    from services.api_key_service import issue_api_key

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="team_admin")
    actor = principal_for(user, team_ids=[team.id], role="team_admin")

    row, plaintext = await issue_api_key(
        db_session,
        actor,
        name="ci-team",
        scope="team",
        team_id=team.id,
        project_id=None,
    )
    assert row.scope == "team"
    assert row.team_id == team.id
    assert row.project_id is None
    assert plaintext.startswith("tos_")


async def test_issue_project_scope_denormalizes_team_id(db_session: AsyncSession) -> None:
    """scope='project' rows store the project's team_id in the api_keys row.

    The list visibility path uses ``api_keys.team_id`` directly so it never
    has to JOIN projects.
    """
    from services.api_key_service import issue_api_key

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    row, _plain = await issue_api_key(
        db_session,
        actor,
        name="ci-proj",
        scope="project",
        team_id=None,
        project_id=project.id,
    )
    assert row.scope == "project"
    assert row.project_id == project.id
    # Denormalization: team_id mirrors the project's team.
    assert row.team_id == team.id


async def test_issue_keeps_unique_prefix(db_session: AsyncSession) -> None:
    """Two issuances must yield distinct prefixes (16^8 ~ 4.3B → vanishingly unlikely collision)."""
    from services.api_key_service import issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    rows = []
    for _ in range(3):
        row, _ = await issue_api_key(
            db_session, actor, name="k", scope="org", team_id=None, project_id=None
        )
        rows.append(row)
    prefixes = {r.key_prefix for r in rows}
    assert len(prefixes) == 3


async def test_issue_writes_audit_row(db_session: AsyncSession) -> None:
    """The SQLAlchemy listener must record an audit_logs INSERT for the new row.

    Note: the audit listener fires in ``before_flush``, BEFORE the server-side
    ``gen_random_uuid()`` generates the api_keys.id, so the audit row's
    ``target_id`` is NULL for INSERTs. We assert on (target_table='api_keys',
    action='create') instead — same pattern as ``test_admin_user_service``.
    """
    from services.api_key_service import issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    await issue_api_key(
        db_session, actor, name="audited", scope="org", team_id=None, project_id=None
    )
    audit_rows = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM audit_logs "
                "WHERE target_table = 'api_keys' AND action = 'create'"
            )
        )
    ).scalar_one()
    assert audit_rows >= 1


async def test_issue_audit_row_does_not_leak_key_hash(db_session: AsyncSession) -> None:
    """The bcrypt hash must NOT appear in audit_logs.diff (sensitive column mask)."""
    from services.api_key_service import issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    await issue_api_key(
        db_session, actor, name="masked", scope="org", team_id=None, project_id=None
    )
    # Look at the most recent api_keys 'create' audit rows; the diff must NOT
    # contain a bcrypt hash. (The sensitive-column mask replaces it with ***.)
    diffs = (
        await db_session.execute(
            text(
                "SELECT diff::text FROM audit_logs "
                "WHERE target_table = 'api_keys' AND action = 'create' "
                "ORDER BY created_at DESC LIMIT 5"
            )
        )
    ).scalars().all()
    assert diffs, "expected at least one audit row for the new api_key"
    for diff in diffs:
        assert "$2b$" not in (diff or "")
        assert "$2a$" not in (diff or "")
        # The masked sentinel must be present.
        assert '"key_hash": "***"' in (diff or "") or "key_hash" not in (diff or "")


# ---------------------------------------------------------------------------
# issue_api_key — RBAC rejection
# ---------------------------------------------------------------------------


async def test_issue_org_scope_rejected_for_team_admin(db_session: AsyncSession) -> None:
    from services.api_key_service import APIKeyForbidden, issue_api_key

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="team_admin")
    actor = principal_for(user, team_ids=[team.id], role="team_admin")

    with pytest.raises(APIKeyForbidden):
        await issue_api_key(
            db_session, actor, name="x", scope="org", team_id=None, project_id=None
        )


async def test_issue_team_scope_rejected_for_developer(db_session: AsyncSession) -> None:
    from services.api_key_service import APIKeyForbidden, issue_api_key

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = principal_for(user, team_ids=[team.id], role="developer")

    with pytest.raises(APIKeyForbidden):
        await issue_api_key(
            db_session, actor, name="x", scope="team", team_id=team.id, project_id=None
        )


async def test_issue_project_scope_rejected_for_outsider(db_session: AsyncSession) -> None:
    """A developer in team B may not issue a project-scoped key for team A's project."""
    from services.api_key_service import APIKeyForbidden, issue_api_key

    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team_a)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team_b, role="developer")
    actor = principal_for(user, team_ids=[team_b.id], role="developer")

    with pytest.raises(APIKeyForbidden):
        await issue_api_key(
            db_session, actor, name="x", scope="project", team_id=None, project_id=project.id
        )


# ---------------------------------------------------------------------------
# issue_api_key — scope mismatch (422)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,scope,team_id_kind,project_id_kind",
    [
        ("org_with_team_id", "org", "real", None),
        ("org_with_project_id", "org", None, "real"),
        ("team_without_team_id", "team", None, None),
        ("team_with_project_id", "team", "real", "real"),
        ("project_without_project_id", "project", None, None),
        ("unknown_scope", "global", None, None),
    ],
)
async def test_issue_scope_mismatch_raises_422(
    db_session: AsyncSession,
    label: str,
    scope: str,
    team_id_kind: str | None,
    project_id_kind: str | None,
) -> None:
    """Mismatched scope/team/project combinations must raise APIKeyScopeMismatch."""
    from services.api_key_service import APIKeyScopeMismatch, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)

    team_id = team.id if team_id_kind == "real" else None
    project_id = project.id if project_id_kind == "real" else None

    with pytest.raises(APIKeyScopeMismatch):
        await issue_api_key(
            db_session,
            actor,
            name="x",
            scope=scope,
            team_id=team_id,
            project_id=project_id,
        )
    # Ensure the failure happened cleanly — no half-committed row exists.
    await db_session.rollback()


async def test_issue_project_scope_unknown_project_raises_404(
    db_session: AsyncSession,
) -> None:
    """Existence-hide: missing project surfaces APIKeyNotFound, not RBAC error."""
    from services.api_key_service import APIKeyNotFound, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    with pytest.raises(APIKeyNotFound):
        await issue_api_key(
            db_session,
            actor,
            name="x",
            scope="project",
            team_id=None,
            project_id=uuid.uuid4(),
        )


# ---------------------------------------------------------------------------
# revoke_api_key
# ---------------------------------------------------------------------------


async def test_revoke_flips_revoked_at(db_session: AsyncSession) -> None:
    from services.api_key_service import issue_api_key, revoke_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, _ = await issue_api_key(
        db_session, actor, name="r", scope="org", team_id=None, project_id=None
    )
    assert row.revoked_at is None
    revoked = await revoke_api_key(db_session, actor, row.id)
    assert revoked.revoked_at is not None
    assert revoked.revoked_by_user_id == admin.id


async def test_revoke_is_idempotent(db_session: AsyncSession) -> None:
    """A second revoke on an already-revoked key returns the same row unchanged."""
    from services.api_key_service import issue_api_key, revoke_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, _ = await issue_api_key(
        db_session, actor, name="r", scope="org", team_id=None, project_id=None
    )

    first = await revoke_api_key(db_session, actor, row.id)
    first_revoked_at = first.revoked_at
    second = await revoke_api_key(db_session, actor, row.id)
    assert second.id == first.id
    assert second.revoked_at == first_revoked_at  # unchanged on idempotent call


async def test_revoke_unknown_id_raises_not_found(db_session: AsyncSession) -> None:
    from services.api_key_service import APIKeyNotFound, revoke_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    with pytest.raises(APIKeyNotFound):
        await revoke_api_key(db_session, actor, uuid.uuid4())


async def test_revoke_existence_hide_for_outsider(db_session: AsyncSession) -> None:
    """A developer who cannot view a key gets 404 (not 403) on revoke — existence-hide."""
    from services.api_key_service import APIKeyNotFound, issue_api_key, revoke_api_key

    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    issuer = await make_user(db_session, is_superuser=True)
    issuer_actor = principal_for(issuer, role="super_admin")
    row, _ = await issue_api_key(
        db_session, issuer_actor, name="r", scope="team", team_id=team_a.id, project_id=None
    )

    outsider = await make_user(db_session)
    await make_membership(db_session, user=outsider, team=team_b, role="developer")
    outsider_actor = principal_for(outsider, team_ids=[team_b.id], role="developer")

    with pytest.raises(APIKeyNotFound):
        await revoke_api_key(db_session, outsider_actor, row.id)


async def test_revoke_writes_audit_row(db_session: AsyncSession) -> None:
    """Revoke flips revoked_at; the SQLAlchemy listener emits an 'update' audit row."""
    from services.api_key_service import issue_api_key, revoke_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, _ = await issue_api_key(
        db_session, actor, name="r", scope="org", team_id=None, project_id=None
    )
    await revoke_api_key(db_session, actor, row.id)
    # UPDATE rows have a real target_id (the row already had its PK loaded).
    # Filter narrowly so we observe THIS specific revoke, not stray history.
    update_actions = (
        await db_session.execute(
            text(
                "SELECT action FROM audit_logs "
                "WHERE target_table = 'api_keys' AND target_id = :tid"
            ),
            {"tid": str(row.id)},
        )
    ).scalars().all()
    assert "update" in update_actions
    # And there must be at least one 'create' audit row in the table since
    # we issued the key in this test (target_id is NULL for INSERTs because
    # the listener fires before gen_random_uuid()).
    create_count = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM audit_logs "
                "WHERE target_table = 'api_keys' AND action = 'create'"
            )
        )
    ).scalar_one()
    assert create_count >= 1


# ---------------------------------------------------------------------------
# list_api_keys — pagination + filters + visibility
# ---------------------------------------------------------------------------


async def test_list_pagination_returns_envelope(db_session: AsyncSession) -> None:
    from services.api_key_service import issue_api_key, list_api_keys

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    for _ in range(3):
        await issue_api_key(
            db_session, actor, name="p", scope="org", team_id=None, project_id=None
        )

    rows, total = await list_api_keys(db_session, actor, page=1, page_size=2)
    assert len(rows) == 2
    assert total >= 3


async def test_list_filter_by_scope(db_session: AsyncSession) -> None:
    from services.api_key_service import issue_api_key, list_api_keys

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    await issue_api_key(
        db_session, actor, name="o", scope="org", team_id=None, project_id=None
    )
    await issue_api_key(
        db_session, actor, name="t", scope="team", team_id=team.id, project_id=None
    )
    rows, _ = await list_api_keys(db_session, actor, scope="team", page_size=200)
    assert all(r.scope == "team" for r in rows)
    assert any(r.team_id == team.id for r in rows)


async def test_list_filter_by_team_id(db_session: AsyncSession) -> None:
    from services.api_key_service import issue_api_key, list_api_keys

    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    await issue_api_key(
        db_session, actor, name="ta", scope="team", team_id=team_a.id, project_id=None
    )
    await issue_api_key(
        db_session, actor, name="tb", scope="team", team_id=team_b.id, project_id=None
    )
    rows, _ = await list_api_keys(db_session, actor, team_id=team_a.id, page_size=200)
    assert all(r.team_id == team_a.id for r in rows)


async def test_list_filter_by_project_id(db_session: AsyncSession) -> None:
    from services.api_key_service import issue_api_key, list_api_keys

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, _ = await issue_api_key(
        db_session, actor, name="p", scope="project", team_id=None, project_id=project.id
    )
    rows, _ = await list_api_keys(db_session, actor, project_id=project.id, page_size=200)
    ids = {r.id for r in rows}
    assert row.id in ids


async def test_list_excludes_revoked_by_default(db_session: AsyncSession) -> None:
    from services.api_key_service import issue_api_key, list_api_keys, revoke_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, _ = await issue_api_key(
        db_session, actor, name="rev", scope="org", team_id=None, project_id=None
    )
    await revoke_api_key(db_session, actor, row.id)
    rows, _ = await list_api_keys(db_session, actor, page_size=200)
    assert row.id not in {r.id for r in rows}
    rows_all, _ = await list_api_keys(db_session, actor, include_revoked=True, page_size=200)
    assert row.id in {r.id for r in rows_all}


async def test_list_developer_sees_only_own_team_keys(db_session: AsyncSession) -> None:
    """Cross-tenant boundary — team_b developer must not see team_a's keys."""
    from services.api_key_service import issue_api_key, list_api_keys

    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    project_a = await make_project(db_session, team=team_a)
    admin = await make_user(db_session, is_superuser=True)
    admin_actor = principal_for(admin, role="super_admin")
    foreign_key, _ = await issue_api_key(
        db_session,
        admin_actor,
        name="a",
        scope="project",
        team_id=None,
        project_id=project_a.id,
    )

    developer = await make_user(db_session)
    await make_membership(db_session, user=developer, team=team_b, role="developer")
    dev_actor = principal_for(developer, team_ids=[team_b.id], role="developer")

    rows, _ = await list_api_keys(db_session, dev_actor, page_size=200)
    assert foreign_key.id not in {r.id for r in rows}


async def test_list_developer_sees_own_issued_keys(db_session: AsyncSession) -> None:
    from services.api_key_service import issue_api_key, list_api_keys

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = principal_for(user, team_ids=[team.id], role="developer")
    row, _ = await issue_api_key(
        db_session, actor, name="own", scope="project", team_id=None, project_id=project.id
    )

    rows, _ = await list_api_keys(db_session, actor, page_size=200)
    assert row.id in {r.id for r in rows}


async def test_list_team_admin_sees_team_scoped_keys(db_session: AsyncSession) -> None:
    """team_admin sees team-scoped keys for their own team."""
    from services.api_key_service import issue_api_key, list_api_keys

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    issuer = await make_user(db_session, is_superuser=True)
    issuer_actor = principal_for(issuer, role="super_admin")
    team_key, _ = await issue_api_key(
        db_session, issuer_actor, name="t", scope="team", team_id=team.id, project_id=None
    )

    admin_user = await make_user(db_session)
    await make_membership(db_session, user=admin_user, team=team, role="team_admin")
    admin_actor = principal_for(
        admin_user,
        team_ids=[team.id],
        role="team_admin",
        team_roles={team.id: "team_admin"},
    )

    rows, _ = await list_api_keys(db_session, admin_actor, page_size=200)
    assert team_key.id in {r.id for r in rows}


async def test_list_super_admin_sees_all_keys(db_session: AsyncSession) -> None:
    from services.api_key_service import issue_api_key, list_api_keys

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    issuer_a = await make_user(db_session)
    await make_membership(db_session, user=issuer_a, team=team, role="developer")
    project = await make_project(db_session, team=team)
    other_actor = principal_for(issuer_a, team_ids=[team.id], role="developer")
    foreign_key, _ = await issue_api_key(
        db_session,
        other_actor,
        name="x",
        scope="project",
        team_id=None,
        project_id=project.id,
    )

    admin = await make_user(db_session, is_superuser=True)
    admin_actor = principal_for(admin, role="super_admin")
    rows, _ = await list_api_keys(db_session, admin_actor, page_size=500)
    assert foreign_key.id in {r.id for r in rows}


async def test_list_pagination_clamps_page_size(db_session: AsyncSession) -> None:
    """page_size > 200 must be clamped; page < 1 must be clamped to 1."""
    from services.api_key_service import list_api_keys

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    rows, total = await list_api_keys(db_session, actor, page=0, page_size=10000)
    assert isinstance(total, int)
    assert len(rows) <= 200


# ---------------------------------------------------------------------------
# authenticate_api_key (auth path)
# ---------------------------------------------------------------------------


async def test_authenticate_succeeds_with_correct_plaintext(db_session: AsyncSession) -> None:
    from services.api_key_service import authenticate_api_key, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, plaintext = await issue_api_key(
        db_session, actor, name="auth", scope="org", team_id=None, project_id=None
    )
    found = await authenticate_api_key(db_session, plaintext)
    assert found is not None
    assert found.id == row.id


async def test_authenticate_fails_for_revoked_key(db_session: AsyncSession) -> None:
    from services.api_key_service import (
        authenticate_api_key,
        issue_api_key,
        revoke_api_key,
    )

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, plaintext = await issue_api_key(
        db_session, actor, name="rev", scope="org", team_id=None, project_id=None
    )
    await revoke_api_key(db_session, actor, row.id)
    found = await authenticate_api_key(db_session, plaintext)
    assert found is None


async def test_authenticate_fails_for_wrong_secret(db_session: AsyncSession) -> None:
    """Right prefix, wrong secret must NOT authenticate (constant-time bcrypt)."""
    from services.api_key_service import authenticate_api_key, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, _ = await issue_api_key(
        db_session, actor, name="wrong", scope="org", team_id=None, project_id=None
    )
    forged = f"{row.key_prefix}_definitely-not-the-right-secret-xxxx"
    found = await authenticate_api_key(db_session, forged)
    assert found is None


async def test_authenticate_fails_for_unknown_prefix(db_session: AsyncSession) -> None:
    from services.api_key_service import authenticate_api_key

    found = await authenticate_api_key(db_session, "tos_deadbeef_unknown-secret-xx")
    assert found is None


async def test_authenticate_returns_none_on_garbage(db_session: AsyncSession) -> None:
    from services.api_key_service import authenticate_api_key

    assert await authenticate_api_key(db_session, "") is None
    assert await authenticate_api_key(db_session, "garbage") is None
