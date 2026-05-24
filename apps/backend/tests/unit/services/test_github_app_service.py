"""
Service-layer tests for ``services.github_app_service`` — v2.2-b1.

Drives the pure async service against a live Postgres (DATABASE_URL) so the
SQLAlchemy audit listener fires. Mirrors ``test_api_key_service.py``.

Coverage:
  - register: persists + ENCRYPTS (DB column is ciphertext, raw PEM never stored
    plaintext); audit row written with the key MASKED ("***"); RBAC (developer
    cannot register; non-member blocked); 409 on duplicate (team, app_id).
  - revoke: soft-deletes, idempotent, existence-hide for outsiders.
  - installations: link/opt-in + unlink (idempotent re-link); cross-team project
    opt-in blocked (P0 leak guard).
  - mint_installation_token: real RSA keypair → valid RS256 App JWT
    (alg=RS256, iss=app_id, exp ≤ 10min); GitHub access_tokens endpoint MOCKED
    (httpx.MockTransport) → installation token returned; NO real network call;
    undecryptable key → GitHubAppConfigError.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
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
        pytest.skip("DATABASE_URL not set — skip github_app_service tests")
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
            f"alembic upgrade head failed; github_app_service tests cannot run\n"
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


# A small but valid RSA private key PEM for the encrypt/persist tests (the JWT
# signing tests generate their own keypair so they can verify the signature).
def _make_rsa_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode("utf-8")


def _make_keypair() -> tuple[str, str]:
    """Return (private_pem, public_pem)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin a deterministic Fernet key for the encryption-at-rest assertions."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("GITHUB_APP_ENCRYPTION_KEY", Fernet.generate_key().decode())


# ---------------------------------------------------------------------------
# register_credential
# ---------------------------------------------------------------------------


async def test_register_persists_and_encrypts(db_session: AsyncSession) -> None:
    from core.crypto import decrypt_secret
    from services.github_app_service import register_credential

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="team_admin")
    actor = principal_for(user, team_ids=[team.id], role="team_admin")

    pem = _make_rsa_pem()
    row = await register_credential(
        db_session,
        actor,
        team_id=team.id,
        app_id="123456",
        app_slug="trustedoss-scanner",
        private_key=pem,
        webhook_secret="whsec",
    )
    assert row.team_id == team.id
    assert row.app_id == "123456"
    assert row.created_by_user_id == user.id
    assert row.revoked_at is None

    # The stored column must be CIPHERTEXT, not the raw PEM.
    stored = (
        await db_session.execute(
            text("SELECT private_key_encrypted FROM github_app_credentials WHERE id = :id"),
            {"id": str(row.id)},
        )
    ).scalar_one()
    assert "BEGIN" not in stored
    assert pem not in stored
    # And it must round-trip back to the original PEM.
    assert decrypt_secret(stored) == pem


async def test_register_writes_masked_audit_row(db_session: AsyncSession) -> None:
    from services.github_app_service import register_credential

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="team_admin")
    actor = principal_for(user, team_ids=[team.id], role="team_admin")

    pem = _make_rsa_pem()
    await register_credential(
        db_session,
        actor,
        team_id=team.id,
        app_id="222",
        app_slug=None,
        private_key=pem,
        webhook_secret="whsec",
    )
    diffs = (
        (
            await db_session.execute(
                text(
                    "SELECT diff::text FROM audit_logs "
                    "WHERE target_table = 'github_app_credentials' AND action = 'create' "
                    "ORDER BY created_at DESC LIMIT 5"
                )
            )
        )
        .scalars()
        .all()
    )
    assert diffs, "expected at least one audit row"
    for diff in diffs:
        body = diff or ""
        assert "BEGIN" not in body  # no PEM
        assert '"private_key_encrypted": "***"' in body
        assert '"webhook_secret_encrypted": "***"' in body
    # The audit row's team_id is bound (non-NULL).
    team_ids = (
        (
            await db_session.execute(
                text(
                    "SELECT team_id FROM audit_logs "
                    "WHERE target_table = 'github_app_credentials' AND action = 'create' "
                    "ORDER BY created_at DESC LIMIT 1"
                )
            )
        )
        .scalars()
        .all()
    )
    assert team_ids and str(team_ids[0]) == str(team.id)


async def test_register_rejected_for_developer(db_session: AsyncSession) -> None:
    from services.github_app_service import GitHubAppForbidden, register_credential

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = principal_for(user, team_ids=[team.id], role="developer")

    with pytest.raises(GitHubAppForbidden):
        await register_credential(
            db_session,
            actor,
            team_id=team.id,
            app_id="333",
            app_slug=None,
            private_key=_make_rsa_pem(),
            webhook_secret=None,
        )


async def test_register_rejected_for_non_member(db_session: AsyncSession) -> None:
    from services.github_app_service import GitHubAppForbidden, register_credential

    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team_b, role="team_admin")
    actor = principal_for(user, team_ids=[team_b.id], role="team_admin")

    with pytest.raises(GitHubAppForbidden):
        await register_credential(
            db_session,
            actor,
            team_id=team_a.id,  # not a member of team_a
            app_id="444",
            app_slug=None,
            private_key=_make_rsa_pem(),
            webhook_secret=None,
        )


async def test_register_duplicate_raises_conflict(db_session: AsyncSession) -> None:
    from services.github_app_service import GitHubAppConflict, register_credential

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="team_admin")
    actor = principal_for(user, team_ids=[team.id], role="team_admin")

    await register_credential(
        db_session,
        actor,
        team_id=team.id,
        app_id="555",
        app_slug=None,
        private_key=_make_rsa_pem(),
        webhook_secret=None,
    )
    with pytest.raises(GitHubAppConflict):
        await register_credential(
            db_session,
            actor,
            team_id=team.id,
            app_id="555",
            app_slug=None,
            private_key=_make_rsa_pem(),
            webhook_secret=None,
        )


async def test_register_by_super_admin(db_session: AsyncSession) -> None:
    from services.github_app_service import register_credential

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    row = await register_credential(
        db_session,
        actor,
        team_id=team.id,
        app_id="666",
        app_slug=None,
        private_key=_make_rsa_pem(),
        webhook_secret=None,
    )
    assert row.team_id == team.id


# ---------------------------------------------------------------------------
# get / list
# ---------------------------------------------------------------------------


async def test_get_existence_hide_for_outsider(db_session: AsyncSession) -> None:
    from services.github_app_service import (
        GitHubAppNotFound,
        get_credential,
        register_credential,
    )

    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    owner = await make_user(db_session)
    await make_membership(db_session, user=owner, team=team_a, role="team_admin")
    owner_actor = principal_for(owner, team_ids=[team_a.id], role="team_admin")
    row = await register_credential(
        db_session,
        owner_actor,
        team_id=team_a.id,
        app_id="777",
        app_slug=None,
        private_key=_make_rsa_pem(),
        webhook_secret=None,
    )

    outsider = await make_user(db_session)
    await make_membership(db_session, user=outsider, team=team_b, role="team_admin")
    outsider_actor = principal_for(outsider, team_ids=[team_b.id], role="team_admin")

    with pytest.raises(GitHubAppNotFound):
        await get_credential(db_session, outsider_actor, row.id)


async def test_list_excludes_other_team(db_session: AsyncSession) -> None:
    from services.github_app_service import list_credentials, register_credential

    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    admin = await make_user(db_session, is_superuser=True)
    admin_actor = principal_for(admin, role="super_admin")
    foreign = await register_credential(
        db_session,
        admin_actor,
        team_id=team_a.id,
        app_id="888",
        app_slug=None,
        private_key=_make_rsa_pem(),
        webhook_secret=None,
    )

    member = await make_user(db_session)
    await make_membership(db_session, user=member, team=team_b, role="developer")
    member_actor = principal_for(member, team_ids=[team_b.id], role="developer")
    rows, _ = await list_credentials(db_session, member_actor, page_size=200)
    assert foreign.id not in {r.id for r in rows}


async def test_list_excludes_revoked_by_default(db_session: AsyncSession) -> None:
    from services.github_app_service import (
        list_credentials,
        register_credential,
        revoke_credential,
    )

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row = await register_credential(
        db_session,
        actor,
        team_id=team.id,
        app_id="999",
        app_slug=None,
        private_key=_make_rsa_pem(),
        webhook_secret=None,
    )
    await revoke_credential(db_session, actor, row.id)
    rows, _ = await list_credentials(db_session, actor, team_id=team.id, page_size=200)
    assert row.id not in {r.id for r in rows}
    rows_all, _ = await list_credentials(
        db_session, actor, team_id=team.id, include_revoked=True, page_size=200
    )
    assert row.id in {r.id for r in rows_all}


# ---------------------------------------------------------------------------
# revoke_credential
# ---------------------------------------------------------------------------


async def test_revoke_soft_deletes(db_session: AsyncSession) -> None:
    from services.github_app_service import register_credential, revoke_credential

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="team_admin")
    actor = principal_for(user, team_ids=[team.id], role="team_admin")
    row = await register_credential(
        db_session,
        actor,
        team_id=team.id,
        app_id="1001",
        app_slug=None,
        private_key=_make_rsa_pem(),
        webhook_secret=None,
    )
    revoked = await revoke_credential(db_session, actor, row.id)
    assert revoked.revoked_at is not None
    assert revoked.revoked_by_user_id == user.id


async def test_revoke_is_idempotent(db_session: AsyncSession) -> None:
    from services.github_app_service import register_credential, revoke_credential

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row = await register_credential(
        db_session,
        actor,
        team_id=team.id,
        app_id="1002",
        app_slug=None,
        private_key=_make_rsa_pem(),
        webhook_secret=None,
    )
    first = await revoke_credential(db_session, actor, row.id)
    second = await revoke_credential(db_session, actor, row.id)
    assert second.revoked_at == first.revoked_at


async def test_revoke_member_non_admin_forbidden(db_session: AsyncSession) -> None:
    from services.github_app_service import (
        GitHubAppForbidden,
        register_credential,
        revoke_credential,
    )

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin = await make_user(db_session, is_superuser=True)
    admin_actor = principal_for(admin, role="super_admin")
    row = await register_credential(
        db_session,
        admin_actor,
        team_id=team.id,
        app_id="1003",
        app_slug=None,
        private_key=_make_rsa_pem(),
        webhook_secret=None,
    )

    dev = await make_user(db_session)
    await make_membership(db_session, user=dev, team=team, role="developer")
    dev_actor = principal_for(dev, team_ids=[team.id], role="developer")
    with pytest.raises(GitHubAppForbidden):
        await revoke_credential(db_session, dev_actor, row.id)


async def test_revoke_outsider_existence_hide(db_session: AsyncSession) -> None:
    from services.github_app_service import (
        GitHubAppNotFound,
        register_credential,
        revoke_credential,
    )

    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    admin = await make_user(db_session, is_superuser=True)
    admin_actor = principal_for(admin, role="super_admin")
    row = await register_credential(
        db_session,
        admin_actor,
        team_id=team_a.id,
        app_id="1004",
        app_slug=None,
        private_key=_make_rsa_pem(),
        webhook_secret=None,
    )
    outsider = await make_user(db_session)
    await make_membership(db_session, user=outsider, team=team_b, role="team_admin")
    outsider_actor = principal_for(outsider, team_ids=[team_b.id], role="team_admin")
    with pytest.raises(GitHubAppNotFound):
        await revoke_credential(db_session, outsider_actor, row.id)


# ---------------------------------------------------------------------------
# installations: link / unlink / opt-in
# ---------------------------------------------------------------------------


async def _make_credential(
    db_session: AsyncSession,
    *,
    role: str = "team_admin",
    app_id: str = "2000",
):
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role=role)
    actor = principal_for(user, team_ids=[team.id], role=role)
    from services.github_app_service import register_credential

    cred = await register_credential(
        db_session,
        actor,
        team_id=team.id,
        app_id=app_id,
        app_slug=None,
        private_key=_make_rsa_pem(),
        webhook_secret=None,
    )
    return team, actor, cred


async def test_link_installation_and_optin(db_session: AsyncSession) -> None:
    from services.github_app_service import link_installation

    team, actor, cred = await _make_credential(db_session, app_id="2001")
    project = await make_project(db_session, team=team)
    row = await link_installation(
        db_session,
        actor,
        cred.id,
        installation_id="555000",
        account_login="acme",
        repository_full_name="acme/widgets",
        project_id=project.id,
    )
    assert row.credential_id == cred.id
    assert row.installation_id == "555000"
    assert row.project_id == project.id


async def test_relink_is_idempotent(db_session: AsyncSession) -> None:
    from services.github_app_service import link_installation, list_installations

    team, actor, cred = await _make_credential(db_session, app_id="2002")
    project = await make_project(db_session, team=team)
    first = await link_installation(
        db_session,
        actor,
        cred.id,
        installation_id="600000",
        account_login="acme",
        repository_full_name="acme/widgets",
        project_id=None,
    )
    second = await link_installation(
        db_session,
        actor,
        cred.id,
        installation_id="600000",
        account_login="acme-renamed",
        repository_full_name="acme/widgets",
        project_id=project.id,
    )
    assert second.id == first.id  # same row, not a duplicate
    assert second.project_id == project.id  # opt-in refreshed
    rows, total = await list_installations(db_session, actor, cred.id, page_size=200)
    assert total == 1
    assert len(rows) == 1


async def test_link_cross_team_project_forbidden(db_session: AsyncSession) -> None:
    """A credential must never be opt-in-linked to another team's project (P0)."""
    from services.github_app_service import GitHubAppForbidden, link_installation

    team, actor, cred = await _make_credential(db_session, app_id="2003")
    # A project owned by a DIFFERENT team.
    other_org = await make_organization(db_session)
    other_team = await make_team(db_session, organization=other_org)
    foreign_project = await make_project(db_session, team=other_team)

    with pytest.raises(GitHubAppForbidden):
        await link_installation(
            db_session,
            actor,
            cred.id,
            installation_id="700000",
            account_login="acme",
            repository_full_name="acme/widgets",
            project_id=foreign_project.id,
        )


async def test_link_unknown_project_not_found(db_session: AsyncSession) -> None:
    from services.github_app_service import GitHubAppNotFound, link_installation

    _team, actor, cred = await _make_credential(db_session, app_id="2004")
    with pytest.raises(GitHubAppNotFound):
        await link_installation(
            db_session,
            actor,
            cred.id,
            installation_id="700001",
            account_login=None,
            repository_full_name=None,
            project_id=uuid.uuid4(),
        )


async def test_link_developer_forbidden(db_session: AsyncSession) -> None:
    from services.github_app_service import GitHubAppForbidden, link_installation

    # Credential owned by an admin; a developer of the same team may not link.
    team, _admin_actor, cred = await _make_credential(db_session, app_id="2005")
    dev = await make_user(db_session)
    await make_membership(db_session, user=dev, team=team, role="developer")
    dev_actor = principal_for(dev, team_ids=[team.id], role="developer")
    with pytest.raises(GitHubAppForbidden):
        await link_installation(
            db_session,
            dev_actor,
            cred.id,
            installation_id="700002",
            account_login=None,
            repository_full_name=None,
            project_id=None,
        )


async def test_unlink_installation(db_session: AsyncSession) -> None:
    from services.github_app_service import (
        link_installation,
        list_installations,
        unlink_installation,
    )

    team, actor, cred = await _make_credential(db_session, app_id="2006")
    row = await link_installation(
        db_session,
        actor,
        cred.id,
        installation_id="800000",
        account_login=None,
        repository_full_name=None,
        project_id=None,
    )
    await unlink_installation(db_session, actor, cred.id, row.id)
    rows, total = await list_installations(db_session, actor, cred.id, page_size=200)
    assert total == 0
    assert rows == []
    # Idempotent: a second unlink is a no-op (no raise).
    await unlink_installation(db_session, actor, cred.id, row.id)


# ---------------------------------------------------------------------------
# mint_installation_token (SECURITY-CRITICAL)
# ---------------------------------------------------------------------------


async def test_mint_token_builds_valid_app_jwt_and_returns_token(
    db_session: AsyncSession,
) -> None:
    from services.github_app_service import mint_installation_token, register_credential

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    private_pem, public_pem = _make_keypair()
    cred = await register_credential(
        db_session,
        actor,
        team_id=team.id,
        app_id="3001",
        app_slug=None,
        private_key=private_pem,
        webhook_secret=None,
    )

    captured: dict[str, object] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        # Capture + verify the App JWT the service signed.
        auth = request.headers.get("Authorization", "")
        assert auth.startswith("Bearer ")
        app_jwt = auth.split(" ", 1)[1]
        captured["jwt"] = app_jwt
        captured["url"] = str(request.url)
        # The endpoint path must be the installation access_tokens exchange.
        assert "/app/installations/99887766/access_tokens" in str(request.url)
        return httpx.Response(
            201,
            json={
                "token": "ghs_installationtoken_xyz",
                "expires_at": "2026-05-24T12:00:00Z",
            },
        )

    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await mint_installation_token(
            db_session,
            cred.id,
            "99887766",
            http_client=client,
        )

    assert result["token"] == "ghs_installationtoken_xyz"
    assert result["expires_at"] == "2026-05-24T12:00:00Z"

    # The signed App JWT must be a valid RS256 token with iss=app_id, exp ≤ 10min.
    app_jwt = captured["jwt"]
    assert isinstance(app_jwt, str)
    header = jwt.get_unverified_header(app_jwt)
    assert header["alg"] == "RS256"
    claims = jwt.decode(app_jwt, public_pem, algorithms=["RS256"])
    assert claims["iss"] == "3001"
    # exp - iat must be ≤ 10 minutes (600s); we mint 9 min after a 60s skew pad.
    assert 0 < (claims["exp"] - claims["iat"]) <= 600


async def test_mint_token_non_201_raises_token_error(db_session: AsyncSession) -> None:
    from services.github_app_service import (
        GitHubAppTokenError,
        mint_installation_token,
        register_credential,
    )

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    private_pem, _ = _make_keypair()
    cred = await register_credential(
        db_session,
        actor,
        team_id=team.id,
        app_id="3002",
        app_slug=None,
        private_key=private_pem,
        webhook_secret=None,
    )

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(GitHubAppTokenError):
            await mint_installation_token(db_session, cred.id, "111", http_client=client)


async def test_mint_token_undecryptable_key_raises_config_error(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    from services.github_app_service import (
        GitHubAppConfigError,
        mint_installation_token,
        register_credential,
    )

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    private_pem, _ = _make_keypair()
    cred = await register_credential(
        db_session,
        actor,
        team_id=team.id,
        app_id="3003",
        app_slug=None,
        private_key=private_pem,
        webhook_secret=None,
    )

    # Rotate the encryption key so the stored ciphertext can no longer decrypt.
    monkeypatch.setenv("GITHUB_APP_ENCRYPTION_KEY", Fernet.generate_key().decode())

    # A handler that, if ever called, would fail the "no network on bad key" intent.
    def _handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not reach GitHub when the key is undecryptable")

    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(GitHubAppConfigError):
            await mint_installation_token(db_session, cred.id, "222", http_client=client)


async def test_mint_token_unknown_credential_not_found(db_session: AsyncSession) -> None:
    from services.github_app_service import GitHubAppNotFound, mint_installation_token

    transport = httpx.MockTransport(
        lambda _r: httpx.Response(201, json={"token": "x", "expires_at": "y"})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(GitHubAppNotFound):
            await mint_installation_token(db_session, uuid.uuid4(), "333", http_client=client)


def test_build_app_jwt_signature_and_claims() -> None:
    """Pure: build_app_jwt produces a verifiable RS256 token (no DB / network)."""
    from services.github_app_service import build_app_jwt

    private_pem, public_pem = _make_keypair()
    token = build_app_jwt(app_id="4242", private_key_pem=private_pem)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256"
    claims = jwt.decode(token, public_pem, algorithms=["RS256"])
    assert claims["iss"] == "4242"
    assert claims["iat"] < claims["exp"]
    assert (claims["exp"] - claims["iat"]) <= 600
