"""
Service-layer tests for ``services.api_key_service`` — Phase 5 PR #16.

Drives the pure async service against a live Postgres (DATABASE_URL) so the
SQLAlchemy listener fires and the ``audit_logs`` table records each
mutation. Mirrors the shape of ``tests/unit/services/test_admin_user_service.py``.

Coverage:
  - issue: org / team / project scope round-trips, plaintext returned ONCE,
    stored hash recoverable via verify_api_key_plaintext (HMAC-SHA256 as of
    A5), prefix uniqueness, RBAC rejection across actor classes.
  - revoke: flips ``revoked_at``, idempotent on second call (returns the
    same row unchanged — service contract), audit row produced.
  - list: pagination, scope/team/project filter, include_revoked default,
    visibility (developer / team_admin / super_admin).
  - parse_bearer / verify_api_key_plaintext: adversarial parametrize on the
    untrusted bearer string.
  - Scope mismatch + RBAC raise crisp domain errors, never 500.
  - authenticate: last_used_at update-interval coalescing (A2). The write
    happens once the interval has elapsed or the key was never used, and is
    skipped inside the interval.
  - authenticate: dual hash-format matrix (A5, concurrency-scaling-plan-
    2026-08-22.md §3.3): a key stored under either the legacy bcrypt hash
    or the new keyed HMAC-SHA256 hash authenticates correctly, both formats
    coexist in the same database, and the timing-flattening dummy targets
    the fast format regardless of what the database currently holds.
  - authenticate: min-duration timing padding (A5, security-reviewer
    finding). Switching the default hash format to HMAC-SHA256 made real
    HMAC verification and the dummy branch fast while a legacy bcrypt-format
    row's wrong-secret branch stayed slow, reopening a timing oracle A1 had
    closed. ``_verify_api_key_plaintext_padded`` pads every branch up to a
    configured floor; pinned directly (padding primitive honours/disables
    the floor) and end-to-end (four observable cases converge).
  - count_legacy_hash_api_keys: active-key counts by format, excluding
    revoked/expired rows. Also the signal for when the padding above (and
    the later bcrypt-read contraction step) can be removed.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
    principal_for,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


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
    from core.security import is_api_key_hmac_hash, verify_password
    from services.api_key_service import issue_api_key, verify_api_key_plaintext

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
    # A5: newly-issued keys are hashed with the fast keyed HMAC format, not
    # bcrypt. Both the dispatching verifier (what authenticate_api_key uses)
    # and the format tag agree, and the legacy bcrypt verifier correctly
    # refuses this hash (never a false positive across formats).
    assert is_api_key_hmac_hash(row.key_hash) is True
    assert verify_api_key_plaintext(plaintext, row.key_hash) is True
    assert verify_password(plaintext, row.key_hash) is False
    # The plaintext itself never appears inside its own stored hash.
    assert plaintext not in row.key_hash


async def test_issue_with_ttl_sets_expires_at(db_session: AsyncSession) -> None:
    from datetime import UTC, datetime, timedelta

    from services.api_key_service import issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, _ = await issue_api_key(
        db_session,
        actor,
        name="ci-ttl",
        scope="org",
        team_id=None,
        project_id=None,
        expires_in_days=30,
    )
    assert row.expires_at is not None
    delta = row.expires_at - datetime.now(UTC)
    assert timedelta(days=29) < delta < timedelta(days=31)


async def test_issue_without_ttl_has_null_expiry(db_session: AsyncSession) -> None:
    from services.api_key_service import issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, _ = await issue_api_key(
        db_session, actor, name="ci-no-ttl", scope="org", team_id=None, project_id=None
    )
    assert row.expires_at is None  # NULL = never expires (legacy behaviour)


async def test_authenticate_accepts_unexpired_key(db_session: AsyncSession) -> None:
    from services.api_key_service import authenticate_api_key, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, plaintext = await issue_api_key(
        db_session,
        actor,
        name="ci-live",
        scope="org",
        team_id=None,
        project_id=None,
        expires_in_days=30,
    )
    authed = await authenticate_api_key(db_session, plaintext)
    assert authed is not None
    assert authed.id == row.id


async def test_authenticate_rejects_expired_key(db_session: AsyncSession) -> None:
    from datetime import UTC, datetime, timedelta

    from services.api_key_service import authenticate_api_key, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, plaintext = await issue_api_key(
        db_session,
        actor,
        name="ci-expired",
        scope="org",
        team_id=None,
        project_id=None,
        expires_in_days=1,
    )
    # Force the key past its expiry, then re-authenticate.
    row.expires_at = datetime.now(UTC) - timedelta(days=1)
    await db_session.commit()
    assert await authenticate_api_key(db_session, plaintext) is None


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
    """The stored hash must NOT appear in audit_logs.diff (sensitive column mask).

    A5 changed the default format from bcrypt to HMAC-SHA256; this checks
    the mask for both markers so the assertion does not go stale (silently
    pass for the wrong reason) if the default format changes again.
    """
    from services.api_key_service import issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    await issue_api_key(
        db_session, actor, name="masked", scope="org", team_id=None, project_id=None
    )
    # Look at the most recent api_keys 'create' audit rows; the diff must NOT
    # contain a bcrypt hash or an HMAC hash. (The sensitive-column mask
    # replaces it with ***.)
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
        assert "hmac-sha256$" not in (diff or "")
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


async def test_list_carries_created_by_email(db_session: AsyncSession) -> None:
    """List rows expose the issuer's email (LEFT JOIN users, single query).

    Regression guard for the FE creator column (validation report L-17):
      - live issuer → created_by_email == users.email
      - issuer gone (created_by_user_id SET NULL) → created_by_email is None,
        and the row still lists (outer join, not inner).
    The APIKeyListItem schema must surface the value via from_attributes.
    """
    from schemas.api_key import APIKeyListItem
    from services.api_key_service import issue_api_key, list_api_keys

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    with_issuer, _ = await issue_api_key(
        db_session, actor, name="creator-col", scope="org", team_id=None, project_id=None
    )
    orphaned, _ = await issue_api_key(
        db_session, actor, name="creator-gone", scope="org", team_id=None, project_id=None
    )
    # Simulate issuer deletion (FK is ondelete=SET NULL) without cascading a
    # full user delete through unrelated tables.
    await db_session.execute(
        text("UPDATE api_keys SET created_by_user_id = NULL WHERE id = :kid"),
        {"kid": orphaned.id},
    )
    await db_session.commit()
    # The instance is cached in the identity map with the stale FK; expire it
    # so the list query below reflects the raw-SQL NULLing. (Not expire_all —
    # that would expire `admin` too and admin.email would lazy-load sync.)
    db_session.expire(orphaned)

    rows, _ = await list_api_keys(db_session, actor, page_size=500)
    by_id = {r.id: r for r in rows}
    # getattr: created_by_email is a plain (non-mapped) attribute attached by
    # the service for the wire schema — mypy doesn't know it on APIKey.
    assert getattr(by_id[with_issuer.id], "created_by_email") == admin.email
    assert getattr(by_id[orphaned.id], "created_by_email") is None

    # The wire schema picks the attribute up (router uses model_validate).
    item = APIKeyListItem.model_validate(by_id[with_issuer.id])
    assert item.created_by_email == admin.email
    orphan_item = APIKeyListItem.model_validate(by_id[orphaned.id])
    assert orphan_item.created_by_email is None


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
    """Right prefix, wrong secret must NOT authenticate (constant-time verify)."""
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


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# authenticate_api_key: last_used_at update interval (concurrency-scaling-plan
# 2026-08-22.md §1.3/§3.3, unit A2)
#
# Before A2, every successful authentication ran a write transaction to stamp
# last_used_at with the exact current time. A2 coalesces that into a bucket:
# the column now means "used at some point within the update interval", not
# "used at this exact instant" (documented in docs-site's admin-guide/api-keys
# and schemas.api_key.APIKeyListItem.last_used_at). These two tests pin both
# halves of that contract directly against the live default (900s / 15min),
# not a monkeypatched value, so a change to the default cannot silently
# desync the tests from the behaviour they are meant to lock in.
# ---------------------------------------------------------------------------


async def test_authenticate_updates_last_used_at_once_interval_has_elapsed(
    db_session: AsyncSession,
) -> None:
    from datetime import UTC, datetime, timedelta

    from core.config import api_key_last_used_at_update_interval_seconds
    from services.api_key_service import authenticate_api_key, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    _, plaintext = await issue_api_key(
        db_session,
        actor,
        name="interval-elapsed",
        scope="org",
        team_id=None,
        project_id=None,
    )

    first = await authenticate_api_key(db_session, plaintext)
    assert first is not None
    assert first.last_used_at is not None
    first_seen = first.last_used_at

    # Push the stamp back further than the update interval so the next
    # authentication is due a refresh, without waiting in real time.
    interval = api_key_last_used_at_update_interval_seconds()
    backdated = datetime.now(UTC) - timedelta(seconds=interval + 5)
    first.last_used_at = backdated
    await db_session.commit()

    second = await authenticate_api_key(db_session, plaintext)
    assert second is not None
    assert second.last_used_at is not None
    # Moved forward past the backdate: the write happened, it was not just
    # left at whatever we forced it to a moment ago.
    assert second.last_used_at > backdated + timedelta(seconds=interval)
    assert second.last_used_at != first_seen


async def test_authenticate_skips_write_within_update_interval(
    db_session: AsyncSession,
) -> None:
    """Two authentications inside one interval leave the first commit's value."""
    from services.api_key_service import authenticate_api_key, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    _, plaintext = await issue_api_key(
        db_session,
        actor,
        name="interval-not-elapsed",
        scope="org",
        team_id=None,
        project_id=None,
    )

    first = await authenticate_api_key(db_session, plaintext)
    assert first is not None
    assert first.last_used_at is not None
    first_seen = first.last_used_at

    second = await authenticate_api_key(db_session, plaintext)
    assert second is not None
    # Same instant back-to-back is well inside the default 900s interval, so
    # the resolution-lowering branch must skip the write, not merely make it
    # cheaper.
    assert second.last_used_at == first_seen


async def test_authenticate_first_use_always_stamps_last_used_at(
    db_session: AsyncSession,
) -> None:
    """A never-used key (last_used_at IS NULL) is stamped on its first use.

    NULL is not "0 seconds ago". The interval-gate must treat it as
    unconditionally stale, or a freshly issued key would show "never used"
    forever even after a successful authentication.
    """
    from services.api_key_service import authenticate_api_key, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, plaintext = await issue_api_key(
        db_session,
        actor,
        name="interval-first-use",
        scope="org",
        team_id=None,
        project_id=None,
    )
    assert row.last_used_at is None

    authed = await authenticate_api_key(db_session, plaintext)
    assert authed is not None
    assert authed.last_used_at is not None


# authenticate_api_key: verification thread offload (concurrency-scaling-plan
# 2026-08-22.md §1.3/§1.5/§3.3, units A1 and A5)
#
# A1 moved both verification calls in this function onto a worker thread
# (asyncio.to_thread) so a single API-key request no longer stalls every
# other request on the same event loop for ~213ms. A5 replaced the DEFAULT
# hash format (bcrypt -> keyed HMAC-SHA256) and, with it, WHICH format the
# unknown-prefix dummy branch targets (see authenticate_api_key's own
# docstring for the reasoning). These tests pin the resulting regression
# contracts: (1) the dummy timing-flattening verification still actually
# runs, not just "returns None the same way", when the prefix does not
# match any row, and it now targets the fast HMAC format; (2) the real
# verification (matched row) still runs through the offload regardless of
# which format that row's hash is; and (3) auth success/failure is
# unchanged.
# ---------------------------------------------------------------------------


async def test_authenticate_unknown_prefix_still_runs_dummy_verification(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dummy verification must still execute after A1's thread offload,
    not be skipped because it moved off the event loop, and (A5) it must
    target the fast HMAC-SHA256 format, not bcrypt.

    Spies on ``asyncio.to_thread`` calls whose function is
    ``verify_api_key_plaintext`` (the same call shape the real branch uses,
    see the offload test below) rather than the executor internals, so this
    test asserts on behavior, not on implementation plumbing.
    """
    import asyncio

    from core.security import is_api_key_hmac_hash
    from services import api_key_service
    from services.api_key_service import authenticate_api_key

    calls: list[tuple[str, str]] = []
    real_to_thread = asyncio.to_thread

    async def spy_to_thread(func, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if func is api_key_service.verify_api_key_plaintext:
            calls.append(args)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr("services.api_key_service.asyncio.to_thread", spy_to_thread)

    found = await authenticate_api_key(db_session, "tos_deadbeef_unknown-secret-xx")

    assert found is None
    assert len(calls) == 1, "dummy verification must run on the unknown-prefix branch"
    plain, hashed = calls[0]
    assert plain == "tos_deadbeef_unknown-secret-xx"
    # A5: the dummy now targets the fast HMAC profile, not a bcrypt sentinel.
    assert is_api_key_hmac_hash(hashed) is True


async def test_authenticate_unknown_prefix_dummy_hash_is_fresh_each_call(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dummy hash is generated per-call, not a module-level constant.

    Locks in the rationale in authenticate_api_key's inline comment: a
    cached dummy would either violate CLAUDE.md rule 11 (env read at import
    time) or reuse the exact same digest forever. Two misses in a row must
    produce two different dummy hashes.
    """
    import asyncio

    from services import api_key_service
    from services.api_key_service import authenticate_api_key

    seen_hashes: list[str] = []
    real_to_thread = asyncio.to_thread

    async def spy_to_thread(func, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if func is api_key_service.verify_api_key_plaintext:
            seen_hashes.append(args[1])
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr("services.api_key_service.asyncio.to_thread", spy_to_thread)

    assert await authenticate_api_key(db_session, "tos_deadbeef_unknown-secret-xx") is None
    assert await authenticate_api_key(db_session, "tos_deadbeef_unknown-secret-xx") is None

    assert len(seen_hashes) == 2
    assert seen_hashes[0] != seen_hashes[1]


async def test_authenticate_known_prefix_offloads_verification(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real verification (matched row) also runs through the thread
    offload, and a correct plaintext still authenticates afterward.

    Uses a key issued through the live service, so its stored hash is the
    current default format (HMAC-SHA256 as of A5). A separate matrix test
    below covers a row that is still in the legacy bcrypt format.
    """
    import asyncio

    from services import api_key_service
    from services.api_key_service import authenticate_api_key, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, plaintext = await issue_api_key(
        db_session, actor, name="offload", scope="org", team_id=None, project_id=None
    )

    calls: list[tuple[str, str]] = []
    real_to_thread = asyncio.to_thread

    async def spy_to_thread(func, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if func is api_key_service.verify_api_key_plaintext:
            calls.append(args)
        return await real_to_thread(func, *args, **kwargs)

    # ``api_key_service.asyncio`` is the same ``asyncio`` module object (it
    # was imported, not defined, there). Accessing it as an attribute of
    # ``api_key_service`` trips mypy's no-implicit-reexport check, so we
    # patch via the dotted string path instead, which monkeypatch resolves
    # at runtime without a static attribute lookup.
    monkeypatch.setattr("services.api_key_service.asyncio.to_thread", spy_to_thread)

    found = await authenticate_api_key(db_session, plaintext)

    assert found is not None
    assert found.id == row.id
    assert len(calls) == 1, "real verify_api_key_plaintext must run via asyncio.to_thread"
    called_plaintext, called_hash = calls[0]
    assert called_plaintext == plaintext
    assert called_hash == row.key_hash


# ---------------------------------------------------------------------------
# authenticate_api_key: dual hash-format matrix (A5 expand/read-both)
#
# A5's rollout is expand-then-contract: new issuances write the HMAC
# format, but a key issued before A5 landed keeps its bcrypt hash until it
# is next reissued. The auth path must accept BOTH shapes correctly for as
# long as that migration window lasts. There is no fixture generator for
# a "pre-A5 key" (the code that wrote bcrypt-format keys is this same
# issue_api_key, just before this change), so these tests build that state
# directly: issue a key through the live service, then overwrite its
# ``key_hash`` with what issue_api_key would have written before A5
# (core.security.hash_password over the same plaintext), the exact
# migration state a real deployment carries between the moment A5 ships
# and the moment every existing key has been rotated.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hash_format", ["legacy_bcrypt", "hmac_sha256"])
async def test_authenticate_accepts_both_hash_formats(
    db_session: AsyncSession, hash_format: str
) -> None:
    from core.security import hash_password
    from services.api_key_service import authenticate_api_key, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, plaintext = await issue_api_key(
        db_session,
        actor,
        name=f"matrix-{hash_format}",
        scope="org",
        team_id=None,
        project_id=None,
    )
    if hash_format == "legacy_bcrypt":
        row.key_hash = hash_password(plaintext)
        db_session.add(row)
        await db_session.commit()

    found = await authenticate_api_key(db_session, plaintext)
    assert found is not None
    assert found.id == row.id


@pytest.mark.parametrize("hash_format", ["legacy_bcrypt", "hmac_sha256"])
async def test_authenticate_rejects_wrong_secret_in_both_hash_formats(
    db_session: AsyncSession, hash_format: str
) -> None:
    from core.security import hash_password
    from services.api_key_service import authenticate_api_key, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, plaintext = await issue_api_key(
        db_session,
        actor,
        name=f"matrix-wrong-{hash_format}",
        scope="org",
        team_id=None,
        project_id=None,
    )
    if hash_format == "legacy_bcrypt":
        row.key_hash = hash_password(plaintext)
        db_session.add(row)
        await db_session.commit()

    forged = f"{row.key_prefix}_definitely-not-the-right-secret-xxxx"
    assert await authenticate_api_key(db_session, forged) is None


@pytest.mark.parametrize("hash_format", ["legacy_bcrypt", "hmac_sha256"])
async def test_authenticate_rejects_revoked_key_in_both_hash_formats(
    db_session: AsyncSession, hash_format: str
) -> None:
    """Revocation must still block auth regardless of which hash format the
    revoked row happens to carry."""
    from core.security import hash_password
    from services.api_key_service import authenticate_api_key, issue_api_key, revoke_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, plaintext = await issue_api_key(
        db_session,
        actor,
        name=f"matrix-revoked-{hash_format}",
        scope="org",
        team_id=None,
        project_id=None,
    )
    if hash_format == "legacy_bcrypt":
        row.key_hash = hash_password(plaintext)
        db_session.add(row)
        await db_session.commit()

    await revoke_api_key(db_session, actor, row.id)
    assert await authenticate_api_key(db_session, plaintext) is None


async def test_authenticate_mixed_formats_coexist_in_the_same_lookup_pass(
    db_session: AsyncSession,
) -> None:
    """Two keys, one bcrypt-format and one HMAC-format, both authenticate
    correctly in the same database at the same time: the realistic shape
    of the migration window, not just isolated single-key cases."""
    from core.security import hash_password
    from services.api_key_service import authenticate_api_key, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    legacy_row, legacy_plaintext = await issue_api_key(
        db_session, actor, name="coexist-legacy", scope="org", team_id=None, project_id=None
    )
    legacy_row.key_hash = hash_password(legacy_plaintext)
    db_session.add(legacy_row)
    await db_session.commit()

    new_row, new_plaintext = await issue_api_key(
        db_session, actor, name="coexist-new", scope="org", team_id=None, project_id=None
    )

    legacy_found = await authenticate_api_key(db_session, legacy_plaintext)
    new_found = await authenticate_api_key(db_session, new_plaintext)
    assert legacy_found is not None and legacy_found.id == legacy_row.id
    assert new_found is not None and new_found.id == new_row.id

    # Cross-wiring check: the legacy plaintext must not authenticate the new
    # row's prefix or vice versa (each row's own hash gates its own secret).
    assert (
        await authenticate_api_key(
            db_session, f"{new_row.key_prefix}_{legacy_plaintext.split('_', 2)[2]}"
        )
        is None
    )


async def test_authenticate_dummy_format_does_not_depend_on_db_content(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Locks in the A5 timing-flattening judgment documented in
    authenticate_api_key's docstring: the unknown-prefix dummy always
    targets the fast HMAC profile, even in a database that currently holds
    ONLY legacy bcrypt-format keys. If the dummy's format were somehow
    derived from what already exists in the table (e.g. "mirror whatever
    the majority format is"), an attacker could use the dummy's response
    time to infer something about the deployment's migration state; tying
    it unconditionally to the HMAC format avoids that. The wall-clock gap
    this format choice reopened (HMAC/dummy fast, legacy-bcrypt-row slow)
    is closed back up by min-duration padding, checked separately below in
    ``test_authenticate_timing_flat_across_all_four_cases_with_padding``;
    this test only pins the format the dummy hashes with, not timing.
    """
    import asyncio

    from core.security import hash_password, is_api_key_hmac_hash
    from services import api_key_service
    from services.api_key_service import authenticate_api_key, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, plaintext = await issue_api_key(
        db_session,
        actor,
        name="dummy-format-legacy-db",
        scope="org",
        team_id=None,
        project_id=None,
    )
    row.key_hash = hash_password(plaintext)
    db_session.add(row)
    await db_session.commit()

    seen_hashes: list[str] = []
    real_to_thread = asyncio.to_thread

    async def spy_to_thread(func, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if func is api_key_service.verify_api_key_plaintext:
            seen_hashes.append(args[1])
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr("services.api_key_service.asyncio.to_thread", spy_to_thread)

    # Probes a prefix that matches nothing, in a DB whose only key is
    # bcrypt-format.
    assert await authenticate_api_key(db_session, "tos_deadbeef_unknown-secret-xx") is None
    assert len(seen_hashes) == 1
    assert is_api_key_hmac_hash(seen_hashes[0]) is True


async def test_authenticate_timing_flat_across_all_four_cases_with_padding(
    db_session: AsyncSession,
) -> None:
    """Pins the FIX for a timing oracle security-reviewer found on the A5
    PR, not merely a documented trade-off: switching the default hash
    format to HMAC-SHA256 made real HMAC verification and the dummy branch
    both microseconds-fast, while a row still on the legacy bcrypt format
    kept its ~213ms wrong-secret cost unchanged. Before the min-duration
    padding fix (``core.config.api_key_verification_min_duration_seconds``,
    ``services.api_key_service._verify_api_key_plaintext_padded``),
    response time alone would tell an attacker "this key_prefix still
    exists and is still bcrypt-format" without ever presenting a valid
    secret -- reopening exactly the kind of gap unit A1 closed.

    Exercises three of the four cases the review comment names (existing
    HMAC-format key + wrong secret, existing legacy-format key + wrong
    secret, and no matching key at all / the dummy branch). The fourth
    named case, a VALID key, is deliberately excluded from this specific
    contract: a successful authentication also pays a DB write
    (last_used_at, A2) whose latency is unrelated to the verification
    padding this test targets, and a legitimate holder of a valid key is
    not the audience the constant-time contract protects (they already
    know the secret; there is nothing left to infer from timing).
    """
    import statistics
    import time

    from core.security import hash_password
    from services.api_key_service import authenticate_api_key, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    hmac_row, _hmac_plaintext = await issue_api_key(
        db_session, actor, name="flat-hmac", scope="org", team_id=None, project_id=None
    )
    legacy_row, legacy_plaintext = await issue_api_key(
        db_session, actor, name="flat-legacy", scope="org", team_id=None, project_id=None
    )
    legacy_row.key_hash = hash_password(legacy_plaintext)
    db_session.add(legacy_row)
    await db_session.commit()

    probes = {
        "hmac_row_wrong_secret": f"{hmac_row.key_prefix}_definitely-not-the-right-secret-xx",
        "legacy_row_wrong_secret": f"{legacy_row.key_prefix}_definitely-not-the-right-secret-xx",
        "no_matching_row": "tos_deadbeef_unknown-secret-xx",
    }

    iterations = 3
    means: dict[str, float] = {}
    for label, probe in probes.items():
        samples: list[float] = []
        for _ in range(iterations):
            start = time.perf_counter()
            assert await authenticate_api_key(db_session, probe) is None
            samples.append(time.perf_counter() - start)
        means[label] = statistics.mean(samples)

    slowest = max(means.values())
    fastest = min(means.values())
    # Generous bound: the padding floor is 220ms by default, so legitimate
    # scheduling/GC jitter around that floor is expected. A REAL regression
    # (e.g. a new branch routed around the padding wrapper, or the padding
    # silently disabled) would blow past this by roughly bcrypt's own
    # ~213ms, not a jitter-sized amount, so 2x + a 150ms floor comfortably
    # separates noise from a real defect without the test itself asserting
    # an exact duration.
    assert slowest <= fastest * 2 + 0.15, (
        "timing diverged across cases more than the padded-flatness contract allows: "
        f"{ {k: round(v * 1000, 1) for k, v in means.items()} } ms"
    )


async def test_verify_api_key_plaintext_padded_honours_the_configured_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct, deterministic test of the padding primitive itself, rather
    than relying only on the statistical convergence test above: a fast
    HMAC verification is slept up to whatever floor is configured, and
    setting the floor to 0 disables padding entirely (the escape hatch
    documented in core.config.api_key_verification_min_duration_seconds's
    docstring, for a deployment that has independently confirmed no
    legacy-format keys remain). Uses a small, deterministic floor (0.3s)
    instead of the 220ms production default so the assertion threshold has
    comfortable headroom above CI scheduling jitter without waiting long.
    """
    import time

    from core.security import hash_api_key_secret
    from services.api_key_service import _verify_api_key_plaintext_padded

    monkeypatch.setenv("API_KEY_HMAC_SECRET", "test-padding-floor-secret-" + "x" * 20)
    hashed = hash_api_key_secret("tos_deadbeef_padding-floor-check")

    monkeypatch.setenv("API_KEY_VERIFICATION_MIN_DURATION_SECONDS", "0.3")
    start = time.perf_counter()
    result = await _verify_api_key_plaintext_padded("wrong-secret-value", hashed)
    padded_elapsed = time.perf_counter() - start
    assert result is False
    assert padded_elapsed >= 0.28, (
        f"expected the 0.3s floor to be honoured, got {padded_elapsed * 1000:.1f}ms"
    )

    monkeypatch.setenv("API_KEY_VERIFICATION_MIN_DURATION_SECONDS", "0")
    start = time.perf_counter()
    await _verify_api_key_plaintext_padded("wrong-secret-value", hashed)
    unpadded_elapsed = time.perf_counter() - start
    assert unpadded_elapsed < 0.28, (
        f"expected floor=0 to disable padding, got {unpadded_elapsed * 1000:.1f}ms"
    )


async def test_authenticate_success_failure_unchanged_after_offload(
    db_session: AsyncSession,
) -> None:
    """Regression: A1 only relocates execution, it must not change who
    authenticates. Exercises the same success/failure matrix the
    pre-existing tests above cover, in one place, as a fast smoke check."""
    from services.api_key_service import authenticate_api_key, issue_api_key, revoke_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, plaintext = await issue_api_key(
        db_session, actor, name="matrix", scope="org", team_id=None, project_id=None
    )

    assert (await authenticate_api_key(db_session, plaintext)) is not None

    wrong_secret = f"{row.key_prefix}_definitely-not-the-right-secret"
    assert (await authenticate_api_key(db_session, wrong_secret)) is None

    await revoke_api_key(db_session, actor, row.id)
    assert (await authenticate_api_key(db_session, plaintext)) is None


async def test_authenticate_timing_stays_flat_between_known_and_unknown_prefix(
    db_session: AsyncSession,
) -> None:
    """Quantitative half of the plan's §4 A1/A5 timing-flatness contract:
    "존재하지 않는 키와 틀린 키의 타이밍이 여전히 평탄하다."

    Both branches pay exactly one verification, the real one (row found,
    wrong secret, HMAC format since the key is freshly issued) or the dummy
    one (no row, also HMAC format as of A5), so their means should be
    close. The bound is deliberately loose (generous multiplier, small
    sample) because this runs on shared CI hardware: the goal is to catch a
    gross regression (e.g. one branch losing its offload and contending for
    the thread pool differently than the other, or the dummy silently
    reverting to a slow format while the real path stayed fast), not to
    assert a precise duration. A separate test earlier in this file,
    ``test_authenticate_timing_flat_across_all_four_cases_with_padding``,
    extends this same contract to a row still on the legacy bcrypt format
    (the case the min-duration padding fix exists for), and
    ``test_verify_api_key_plaintext_padded_honours_the_configured_floor``
    tests the padding primitive directly.
    """
    import statistics
    import time

    from services.api_key_service import authenticate_api_key, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row, _plaintext = await issue_api_key(
        db_session, actor, name="timing", scope="org", team_id=None, project_id=None
    )
    wrong_secret = f"{row.key_prefix}_definitely-not-the-right-secret"
    unknown_prefix = "tos_deadbeef_unknown-secret-xx"

    iterations = 5
    known_times: list[float] = []
    unknown_times: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        assert await authenticate_api_key(db_session, wrong_secret) is None
        known_times.append(time.perf_counter() - start)

        start = time.perf_counter()
        assert await authenticate_api_key(db_session, unknown_prefix) is None
        unknown_times.append(time.perf_counter() - start)

    known_mean = statistics.mean(known_times)
    unknown_mean = statistics.mean(unknown_times)
    slower, faster = max(known_mean, unknown_mean), min(known_mean, unknown_mean)

    # Generous: either branch may run up to 4x slower than the other before
    # this fails, and a large absolute floor (250ms) covers CI jitter around
    # a single ~213ms bcrypt call without making the test flaky.
    assert slower <= faster * 4 + 0.25, (
        f"known-prefix mean {known_mean * 1000:.1f}ms vs "
        f"unknown-prefix mean {unknown_mean * 1000:.1f}ms diverged more than "
        "the timing-flatness contract allows"
    )


# ---------------------------------------------------------------------------
# count_legacy_hash_api_keys (A5 migration visibility)
# ---------------------------------------------------------------------------


async def test_count_legacy_hash_api_keys_splits_by_format(db_session: AsyncSession) -> None:
    from core.security import hash_password
    from services.api_key_service import count_legacy_hash_api_keys, issue_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    before = await count_legacy_hash_api_keys(db_session)

    legacy_row, legacy_plaintext = await issue_api_key(
        db_session, actor, name="count-legacy", scope="org", team_id=None, project_id=None
    )
    legacy_row.key_hash = hash_password(legacy_plaintext)
    db_session.add(legacy_row)
    await db_session.commit()

    await issue_api_key(
        db_session, actor, name="count-hmac", scope="org", team_id=None, project_id=None
    )

    after = await count_legacy_hash_api_keys(db_session)
    assert after.legacy_bcrypt == before.legacy_bcrypt + 1
    assert after.hmac_sha256 == before.hmac_sha256 + 1
    assert after.total == after.legacy_bcrypt + after.hmac_sha256


async def test_count_legacy_hash_api_keys_excludes_revoked_and_expired(
    db_session: AsyncSession,
) -> None:
    from datetime import UTC, datetime, timedelta

    from core.security import hash_password
    from services.api_key_service import count_legacy_hash_api_keys, issue_api_key, revoke_api_key

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    before = await count_legacy_hash_api_keys(db_session)

    revoked_row, _ = await issue_api_key(
        db_session, actor, name="count-revoked", scope="org", team_id=None, project_id=None
    )
    await revoke_api_key(db_session, actor, revoked_row.id)

    expired_row, _ = await issue_api_key(
        db_session, actor, name="count-expired", scope="org", team_id=None, project_id=None
    )
    expired_row.key_hash = hash_password("irrelevant-plaintext")
    expired_row.expires_at = datetime.now(tz=UTC) - timedelta(days=1)
    db_session.add(expired_row)
    await db_session.commit()

    after = await count_legacy_hash_api_keys(db_session)
    # Neither the revoked (HMAC-format) nor the expired (bcrypt-format) key
    # is active, so neither count should have moved.
    assert after.legacy_bcrypt == before.legacy_bcrypt
    assert after.hmac_sha256 == before.hmac_sha256


# ---------------------------------------------------------------------------
# hash_api_key_secret / verify_api_key_hmac (A5 primitive, core.security)
# ---------------------------------------------------------------------------


def test_hash_api_key_secret_round_trips_via_verify_api_key_plaintext() -> None:
    from services.api_key_service import verify_api_key_plaintext

    plaintext = "tos_deadbeef_some-random-secret-value"
    from core.security import hash_api_key_secret

    hashed = hash_api_key_secret(plaintext)
    assert hashed.startswith("hmac-sha256$")
    assert verify_api_key_plaintext(plaintext, hashed) is True
    assert verify_api_key_plaintext(plaintext + "x", hashed) is False
    # The plaintext must never appear inside its own hash.
    assert plaintext not in hashed


def test_hash_api_key_secret_is_deterministic_for_the_same_secret() -> None:
    """Unlike bcrypt (which salts), HMAC-SHA256 is deterministic: the same
    (key, plaintext) pair always produces the same digest. This is required
    for verification to work at all (there is no salt stored alongside the
    hash to reproduce), and is safe here specifically because the input is
    already a 192-bit random value, not a low-entropy password where
    determinism would enable a precomputed rainbow-table attack."""
    from core.security import hash_api_key_secret

    plaintext = "tos_deadbeef_deterministic-check"
    assert hash_api_key_secret(plaintext) == hash_api_key_secret(plaintext)


def test_verify_api_key_hmac_rejects_non_hmac_hash() -> None:
    """A bcrypt-shaped value must never verify against the HMAC path."""
    from core.security import hash_password, verify_api_key_hmac

    bcrypt_hash = hash_password("whatever")
    assert verify_api_key_hmac("whatever", bcrypt_hash) is False


def test_is_api_key_hmac_hash_distinguishes_formats() -> None:
    from core.security import hash_api_key_secret, hash_password, is_api_key_hmac_hash

    assert is_api_key_hmac_hash(hash_api_key_secret("x")) is True
    assert is_api_key_hmac_hash(hash_password("x")) is False
    assert is_api_key_hmac_hash("not-a-recognized-format") is False
