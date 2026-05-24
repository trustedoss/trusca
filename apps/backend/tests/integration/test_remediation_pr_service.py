"""
DB-backed integration tests for the b3 opt-in auto-PR service + endpoint —
v2.2 2.2-b3 (``services.remediation_pr_service`` + the new ``api.v1.remediation``
routes).

Driven against the live Postgres (CLAUDE.md core rule #1 — no SQLite, no mocking
our own infra) with a session bound to ``DATABASE_URL``; skipped when unset.

Every GitHub HTTP call is mocked (``httpx.MockTransport``) — these tests NEVER
hit the network. The installation-token mint (b1) is mocked at its GitHub
exchange too, via a fake RSA keypair generated at runtime, so the SECURITY
contract (token never logged, follow_redirects=False, no network) is exercised
end to end.

Covered (DoD):
  * opt-in enforcement — a project with NO opted-in installation is BLOCKED (409);
    a caller cannot target an arbitrary repo (no request field for it);
  * happy path — creates branch + commit + PR; asserts the mocked GitHub calls,
    the persisted row, and the audit row;
  * no-changes → no PR (204-shaped no-op result);
  * idempotency — same fingerprint returns the existing OPEN PR, no 2nd PR;
  * GitHub 4xx / 5xx → clean GitHubWriteError (status-only, row flipped 'failed');
  * RBAC — a developer (non-admin) cannot; a non-member is 404 (existence-hide);
  * follow_redirects=False asserted on the GitHub write calls;
  * GitHub returning a redirect → treated as an error (must NOT be followed);
  * installation token NEVER appears in a captured log line;
  * adversarial — malformed stored repository_full_name → 422 (defensive
    re-validation), oversized manifest rejected, branch-name is hex-derived.
  * endpoint — auth required (401 problem+json), 201 on create, 200 on idempotent
    hit, 409 problem on not-opted-in, RFC 7807 envelope.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
import structlog
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
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
        pytest.skip("DATABASE_URL not set — skip remediation-pr tests")
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
            "alembic upgrade head failed; remediation-pr tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin a deterministic Fernet key so the stored App PEM round-trips."""
    monkeypatch.setenv("GITHUB_APP_ENCRYPTION_KEY", Fernet.generate_key().decode())


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
# RSA + GitHub mock helpers
# ---------------------------------------------------------------------------


def _make_rsa_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


_INSTALL_TOKEN = "ghs_b3_installation_token_SUPERSECRET"


def _github_handler(
    *,
    on_request: Callable[[httpx.Request], None] | None = None,
    pr_number: int = 7,
    overrides: dict[str, httpx.Response] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a MockTransport handler simulating the b3 GitHub write sequence.

    Routes by (method, path-suffix):
      - POST .../app/installations/<id>/access_tokens → 201 token (b1 mint)
      - GET  .../git/ref/heads/<base>                  → 200 base ref sha
      - POST .../git/refs                               → 201 branch created
      - GET  .../contents/package.json                  → 200 existing blob sha
      - PUT  .../contents/package.json                  → 201 committed
      - POST .../pulls                                  → 201 PR opened

    ``overrides`` maps a step key to a canned Response to force a failure path.
    ``on_request`` is called for every request (used to assert headers / token /
    follow_redirects).
    """
    overrides = overrides or {}

    def _handler(request: httpx.Request) -> httpx.Response:
        if on_request is not None:
            on_request(request)
        url = str(request.url)
        method = request.method

        if method == "POST" and "/access_tokens" in url:
            if "token" in overrides:
                return overrides["token"]
            return httpx.Response(
                201,
                json={"token": _INSTALL_TOKEN, "expires_at": "2026-05-25T12:00:00Z"},
            )
        if method == "GET" and "/git/ref/heads/" in url:
            if "get_base_ref" in overrides:
                return overrides["get_base_ref"]
            return httpx.Response(200, json={"object": {"sha": "basesha123"}})
        if method == "POST" and url.endswith("/git/refs"):
            if "create_branch" in overrides:
                return overrides["create_branch"]
            return httpx.Response(201, json={"ref": "refs/heads/x"})
        if method == "GET" and "/contents/package.json" in url:
            if "get_file_sha" in overrides:
                return overrides["get_file_sha"]
            return httpx.Response(200, json={"sha": "blobsha456"})
        if method == "PUT" and "/contents/package.json" in url:
            if "put_manifest" in overrides:
                return overrides["put_manifest"]
            return httpx.Response(201, json={"commit": {"sha": "commitsha789"}})
        if method == "POST" and url.endswith("/pulls"):
            if "open_pr" in overrides:
                return overrides["open_pr"]
            return httpx.Response(
                201,
                json={
                    "number": pr_number,
                    "html_url": f"https://github.com/acme/widget/pull/{pr_number}",
                },
            )
        raise AssertionError(f"unexpected GitHub call: {method} {url}")  # pragma: no cover

    return _handler


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_opted_in_project(
    session: AsyncSession,
    *,
    repository_full_name: str | None = "acme/widget",
    admin_role: str = "team_admin",
):
    """Seed org/team/admin/project + a vulnerable npm dep + an opt-in installation.

    Returns (team, admin_user, project, scan, package_name).
    """
    from services.github_app_service import link_installation, register_credential

    org = await make_organization(session)
    team = await make_team(session, organization=org)
    admin = await make_user(session)
    await make_membership(session, user=admin, team=team, role=admin_role)
    admin_actor = principal_for(admin, team_ids=[team.id], role=admin_role)
    project = await make_project(session, team=team, created_by=admin)
    project.default_branch = "main"
    scan = await make_scan(session, project=project, status="succeeded")
    project.latest_scan_id = scan.id
    await session.commit()
    await session.refresh(project)

    suffix = unique_suffix()
    pkg = f"lodash-{suffix}"
    cv = await _make_npm_cv(session, name=pkg, version="4.17.20")
    await _attach_scan_component(session, scan_id=scan.id, cv_id=cv.id)
    v = await _make_vuln(session, cve_id=f"CVE-{suffix}", severity="critical")
    await _attach_finding(
        session, scan_id=scan.id, cv_id=cv.id, vuln_id=v.id, fixed_version="4.17.21"
    )

    # Register a credential + opt-in installation linked to THIS project.
    cred = await register_credential(
        session,
        admin_actor,
        team_id=team.id,
        app_id=f"app-{suffix}",
        app_slug=None,
        private_key=_make_rsa_pem(),
        webhook_secret=None,
    )
    if repository_full_name is not None:
        await link_installation(
            session,
            admin_actor,
            cred.id,
            installation_id="99887766",
            account_login="acme",
            repository_full_name=repository_full_name,
            project_id=project.id,
        )
    return team, admin, project, scan, pkg


async def _make_npm_cv(session: AsyncSession, *, name: str, version: str):
    from models import Component, ComponentVersion

    purl = f"pkg:npm/{name}"
    component = Component(purl=purl, package_type="npm", name=name)
    session.add(component)
    await session.commit()
    await session.refresh(component)
    cv = ComponentVersion(
        component_id=component.id,
        version=version,
        purl_with_version=f"{purl}@{version}",
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)
    return cv


async def _make_vuln(session: AsyncSession, *, cve_id: str, severity: str = "high"):
    from models import Vulnerability

    v = Vulnerability(external_id=cve_id, source="NVD", severity=severity, summary=cve_id)
    session.add(v)
    await session.commit()
    await session.refresh(v)
    return v


async def _attach_finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    vuln_id: uuid.UUID,
    fixed_version: str | None,
    status: str = "new",
):
    from models import VulnerabilityFinding

    vf = VulnerabilityFinding(
        scan_id=scan_id,
        component_version_id=cv_id,
        vulnerability_id=vuln_id,
        status=status,
        fixed_version=fixed_version,
    )
    session.add(vf)
    await session.commit()
    await session.refresh(vf)
    return vf


async def _attach_scan_component(session: AsyncSession, *, scan_id, cv_id, direct=True, depth=1):
    from models import ScanComponent

    sc = ScanComponent(scan_id=scan_id, component_version_id=cv_id, direct=direct, depth=depth)
    session.add(sc)
    await session.commit()
    await session.refresh(sc)
    return sc


def _manifest_for(pkg: str, version: str = "^4.17.20") -> str:
    return json.dumps({"dependencies": {pkg: version}}, indent=2) + "\n"


# ---------------------------------------------------------------------------
# Opt-in enforcement (security gate)
# ---------------------------------------------------------------------------


async def test_not_opted_in_is_blocked_409(db_session: AsyncSession) -> None:
    """A project with NO opted-in installation cannot open a PR (409)."""
    from services.remediation_pr_service import (
        ProjectNotOptedIn,
        create_npm_remediation_pr,
    )

    team, admin, project, _scan, pkg = await _seed_opted_in_project(
        db_session, repository_full_name=None  # credential exists, but NO link
    )
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    # A handler that would FAIL the test if any GitHub call were made.
    transport = httpx.MockTransport(
        lambda _r: (_ for _ in ()).throw(AssertionError("no GitHub call expected"))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ProjectNotOptedIn):
            await create_npm_remediation_pr(
                db_session,
                actor,
                project.id,
                manifest_override=_manifest_for(pkg),
                http_client=client,
            )

    # No record persisted for a blocked attempt.
    rows = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM remediation_pull_requests WHERE project_id = :p"
            ),
            {"p": str(project.id)},
        )
    ).scalar_one()
    assert rows == 0


async def test_revoked_credential_blocks(db_session: AsyncSession) -> None:
    """An installation whose credential is revoked is not a usable opt-in (409)."""
    from models import GitHubAppCredential
    from services.remediation_pr_service import (
        ProjectNotOptedIn,
        create_npm_remediation_pr,
    )

    team, admin, project, _scan, pkg = await _seed_opted_in_project(db_session)
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    # Revoke the credential out-of-band.
    cred = (
        await db_session.execute(
            select(GitHubAppCredential).where(GitHubAppCredential.team_id == team.id)
        )
    ).scalar_one()
    from datetime import UTC, datetime

    cred.revoked_at = datetime.now(tz=UTC)
    await db_session.commit()

    transport = httpx.MockTransport(
        lambda _r: (_ for _ in ()).throw(AssertionError("no GitHub call expected"))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ProjectNotOptedIn):
            await create_npm_remediation_pr(
                db_session, actor, project.id,
                manifest_override=_manifest_for(pkg), http_client=client,
            )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_happy_path_creates_branch_commit_pr(db_session: AsyncSession) -> None:
    from models import RemediationPullRequest
    from services.remediation_pr_service import create_npm_remediation_pr

    team, admin, project, _scan, pkg = await _seed_opted_in_project(db_session)
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    seen: list[tuple[str, str]] = []

    def _on_request(request: httpx.Request) -> None:
        seen.append((request.method, str(request.url)))

    # A recording client that wraps a MockTransport so we can assert every GitHub
    # call passes follow_redirects=False (the token must never follow a 3xx).
    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(_github_handler(on_request=_on_request, pr_number=42))
    )

    class _RedirectRecordingClient:
        def __init__(self) -> None:
            self.follow_redirects_args: list[object] = []

        async def request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
            self.follow_redirects_args.append(kwargs.get("follow_redirects", "UNSET"))
            return await inner.request(method, url, **kwargs)

        async def post(self, url, **kwargs):  # type: ignore[no-untyped-def]
            # b1's mint uses .post(); record + delegate.
            self.follow_redirects_args.append(kwargs.get("follow_redirects", "UNSET"))
            return await inner.post(url, **kwargs)

    recording = _RedirectRecordingClient()
    result = await create_npm_remediation_pr(
        db_session, actor, project.id,
        manifest_override=_manifest_for(pkg),
        http_client=recording,  # type: ignore[arg-type]
    )
    await inner.aclose()

    # Every GitHub call (mint + writes) was made with follow_redirects=False.
    assert recording.follow_redirects_args
    assert all(v is False for v in recording.follow_redirects_args)

    assert result.created is True
    assert result.record is not None
    rec = result.record
    assert rec.status == "open"
    assert rec.pr_number == 42
    assert rec.pr_url == "https://github.com/acme/widget/pull/42"
    assert rec.repository_full_name == "acme/widget"
    assert rec.head_branch.startswith("trustedoss/remediation-")
    assert rec.base_branch == "main"
    assert rec.ecosystem == "npm"
    assert any(c["package"] == pkg and c["to"] == "4.17.21" for c in rec.package_changes)

    # The GitHub write sequence happened in order.
    methods_paths = [m for m, _ in seen]
    assert "POST" in methods_paths  # access_tokens + refs + pulls
    assert any("/access_tokens" in u for _m, u in seen)
    assert any("/git/refs" in u for _m, u in seen)
    assert any("/contents/package.json" in u for _m, u in seen)
    assert any(u.endswith("/pulls") for _m, u in seen)

    # The persisted row is readable back.
    fetched = (
        await db_session.execute(
            select(RemediationPullRequest).where(RemediationPullRequest.id == rec.id)
        )
    ).scalar_one()
    assert fetched.status == "open"

    # An audit row was written for the mutation (team bound).
    audit_count = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM audit_logs "
                "WHERE target_table = 'remediation_pull_requests' "
                "AND team_id = :t"
            ),
            {"t": str(team.id)},
        )
    ).scalar_one()
    assert audit_count >= 1


async def test_token_never_logged(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """The installation token must never appear in any emitted log line."""
    from services.remediation_pr_service import create_npm_remediation_pr

    # Route structlog through stdlib so caplog captures it.
    structlog.configure(
        processors=[structlog.stdlib.render_to_log_kwargs],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

    team, admin, project, _scan, pkg = await _seed_opted_in_project(db_session)
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    transport = httpx.MockTransport(_github_handler())
    with caplog.at_level("INFO"):
        async with httpx.AsyncClient(transport=transport) as client:
            await create_npm_remediation_pr(
                db_session, actor, project.id,
                manifest_override=_manifest_for(pkg), http_client=client,
            )

    structlog.reset_defaults()
    assert _INSTALL_TOKEN not in caplog.text


# ---------------------------------------------------------------------------
# No-op / idempotency
# ---------------------------------------------------------------------------


async def test_no_changes_no_pr(db_session: AsyncSession) -> None:
    """When the manifest already satisfies the targets, no PR is opened."""
    from services.remediation_pr_service import create_npm_remediation_pr

    team, admin, project, _scan, pkg = await _seed_opted_in_project(db_session)
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    transport = httpx.MockTransport(
        lambda _r: (_ for _ in ()).throw(AssertionError("no GitHub call expected"))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        # The dep is already at/above the fixed range → nothing to bump.
        result = await create_npm_remediation_pr(
            db_session, actor, project.id,
            manifest_override=_manifest_for(pkg, "^4.17.21"), http_client=client,
        )
    assert result.record is None
    assert result.created is False
    assert result.no_op_reason == "no_manifest_change"


async def test_idempotent_same_fingerprint_returns_existing(
    db_session: AsyncSession,
) -> None:
    """A second identical request returns the existing open PR — no 2nd PR."""
    from models import RemediationPullRequest
    from services.remediation_pr_service import create_npm_remediation_pr

    team, admin, project, _scan, pkg = await _seed_opted_in_project(db_session)
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")
    manifest = _manifest_for(pkg)

    call_count = {"pulls": 0}

    def _on_request(request: httpx.Request) -> None:
        if str(request.url).endswith("/pulls"):
            call_count["pulls"] += 1

    transport = httpx.MockTransport(_github_handler(on_request=_on_request, pr_number=11))
    async with httpx.AsyncClient(transport=transport) as client:
        first = await create_npm_remediation_pr(
            db_session, actor, project.id, manifest_override=manifest, http_client=client
        )
        second = await create_npm_remediation_pr(
            db_session, actor, project.id, manifest_override=manifest, http_client=client
        )

    assert first.created is True
    assert second.created is False
    assert second.record is not None and first.record is not None
    assert second.record.id == first.record.id
    # The PR-open endpoint was called exactly once across both requests.
    assert call_count["pulls"] == 1

    total = (
        await db_session.execute(
            select(RemediationPullRequest).where(RemediationPullRequest.project_id == project.id)
        )
    ).scalars().all()
    assert len(total) == 1


# ---------------------------------------------------------------------------
# GitHub failure handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fail_step,status_code",
    [
        ("get_base_ref", 404),
        ("create_branch", 422),
        ("put_manifest", 409),
        ("open_pr", 500),
    ],
)
async def test_github_failure_flips_row_to_failed(
    db_session: AsyncSession, fail_step: str, status_code: int
) -> None:
    from models import RemediationPullRequest
    from services.remediation_pr_service import (
        GitHubWriteError,
        create_npm_remediation_pr,
    )

    team, admin, project, _scan, pkg = await _seed_opted_in_project(db_session)
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    overrides = {fail_step: httpx.Response(status_code, json={"message": "nope"})}
    transport = httpx.MockTransport(_github_handler(overrides=overrides))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(GitHubWriteError) as ei:
            await create_npm_remediation_pr(
                db_session, actor, project.id,
                manifest_override=_manifest_for(pkg), http_client=client,
            )
    # The error carries the STATUS only — never a token / response body.
    assert str(status_code) in str(ei.value)
    assert _INSTALL_TOKEN not in str(ei.value)

    # The attempt row was flipped to 'failed' (auditable, no silent partial).
    rec = (
        await db_session.execute(
            select(RemediationPullRequest).where(RemediationPullRequest.project_id == project.id)
        )
    ).scalars().one()
    assert rec.status == "failed"


async def test_github_redirect_not_followed(db_session: AsyncSession) -> None:
    """A 3xx from GitHub must be treated as an error, never followed."""
    from services.remediation_pr_service import (
        GitHubWriteError,
        create_npm_remediation_pr,
    )

    team, admin, project, _scan, pkg = await _seed_opted_in_project(db_session)
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    redirect = httpx.Response(302, headers={"Location": "https://evil.example/steal"})
    overrides = {"get_base_ref": redirect}
    transport = httpx.MockTransport(_github_handler(overrides=overrides))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(GitHubWriteError):
            await create_npm_remediation_pr(
                db_session, actor, project.id,
                manifest_override=_manifest_for(pkg), http_client=client,
            )


async def test_token_mint_failure_surfaces_write_error(db_session: AsyncSession) -> None:
    """If the b1 token mint fails (GitHub 401), b3 surfaces GitHubWriteError."""
    from services.remediation_pr_service import (
        GitHubWriteError,
        create_npm_remediation_pr,
    )

    team, admin, project, _scan, pkg = await _seed_opted_in_project(db_session)
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    overrides = {"token": httpx.Response(401, json={"message": "Bad credentials"})}
    transport = httpx.MockTransport(_github_handler(overrides=overrides))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(GitHubWriteError):
            await create_npm_remediation_pr(
                db_session, actor, project.id,
                manifest_override=_manifest_for(pkg), http_client=client,
            )


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


async def test_developer_cannot_open_pr(db_session: AsyncSession) -> None:
    from services.remediation_pr_service import (
        RemediationForbidden,
        create_npm_remediation_pr,
    )

    team, _admin, project, _scan, pkg = await _seed_opted_in_project(db_session)
    # A different user who is only a DEVELOPER on the team.
    dev = await make_user(db_session)
    await make_membership(db_session, user=dev, team=team, role="developer")
    dev_actor = principal_for(dev, team_ids=[team.id], role="developer")

    transport = httpx.MockTransport(
        lambda _r: (_ for _ in ()).throw(AssertionError("no GitHub call expected"))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RemediationForbidden):
            await create_npm_remediation_pr(
                db_session, dev_actor, project.id,
                manifest_override=_manifest_for(pkg), http_client=client,
            )


async def test_non_member_blocked_404(db_session: AsyncSession) -> None:
    from services.remediation_pr_service import create_npm_remediation_pr
    from services.remediation_service import ProjectNotAccessible

    _team, _admin, project, _scan, pkg = await _seed_opted_in_project(db_session)
    outsider = await make_user(db_session)
    actor = principal_for(outsider, team_ids=[], role="developer")

    transport = httpx.MockTransport(
        lambda _r: (_ for _ in ()).throw(AssertionError("no GitHub call expected"))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ProjectNotAccessible):
            await create_npm_remediation_pr(
                db_session, actor, project.id,
                manifest_override=_manifest_for(pkg), http_client=client,
            )


# ---------------------------------------------------------------------------
# Adversarial: stored repository_full_name / oversized manifest
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_repo",
    [
        "../../etc/passwd",
        "acme/widget/../../secret",
        "acme",  # missing the repo segment
        "acme/widget\r\nHost: evil",  # CRLF smuggle
        "acme/wid get",  # space
        "/leading",  # empty owner
    ],
)
async def test_malformed_stored_repo_rejected(
    db_session: AsyncSession, bad_repo: str
) -> None:
    """A corrupted/crafted stored repository_full_name → 422, no GitHub call.

    We bypass link_installation (which validates input) by writing the bad value
    directly onto the installation row — modelling a corrupted row / a future
    code path that stored a bad value. The service MUST re-validate at the trust
    boundary before any URL interpolation.
    """
    from models import GitHubAppInstallation
    from services.remediation_pr_service import (
        RemediationConfigError,
        create_npm_remediation_pr,
    )

    team, admin, project, _scan, pkg = await _seed_opted_in_project(db_session)
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    inst = (
        await db_session.execute(
            select(GitHubAppInstallation).where(GitHubAppInstallation.project_id == project.id)
        )
    ).scalar_one()
    inst.repository_full_name = bad_repo
    await db_session.commit()

    transport = httpx.MockTransport(
        lambda _r: (_ for _ in ()).throw(AssertionError("no GitHub call expected"))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RemediationConfigError):
            await create_npm_remediation_pr(
                db_session, actor, project.id,
                manifest_override=_manifest_for(pkg), http_client=client,
            )


async def test_oversized_manifest_rejected(db_session: AsyncSession) -> None:
    """An oversized override manifest is rejected by the b2 adapter (422)."""
    from services.remediation_pr_service import create_npm_remediation_pr
    from services.remediation_service import ManifestRejected

    team, admin, project, _scan, pkg = await _seed_opted_in_project(db_session)
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    # Over the 1 MiB default cap.
    huge = '{"dependencies": {"x": "' + ("9" * (1_100_000)) + '"}}'

    transport = httpx.MockTransport(
        lambda _r: (_ for _ in ()).throw(AssertionError("no GitHub call expected"))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ManifestRejected):
            await create_npm_remediation_pr(
                db_session, actor, project.id, manifest_override=huge, http_client=client
            )


async def test_branch_name_is_hex_derived(db_session: AsyncSession) -> None:
    """The created branch name is built from the hex fingerprint we control."""
    from services.remediation_pr_service import create_npm_remediation_pr

    team, admin, project, _scan, pkg = await _seed_opted_in_project(db_session)
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")

    transport = httpx.MockTransport(_github_handler())
    async with httpx.AsyncClient(transport=transport) as client:
        result = await create_npm_remediation_pr(
            db_session, actor, project.id,
            manifest_override=_manifest_for(pkg), http_client=client,
        )
    assert result.record is not None
    branch = result.record.head_branch
    assert branch.startswith("trustedoss/remediation-")
    short = branch.rsplit("-", 1)[-1]
    assert len(short) == 8
    assert all(c in "0123456789abcdef" for c in short)


# ---------------------------------------------------------------------------
# list_remediation_prs
# ---------------------------------------------------------------------------


async def test_list_returns_records_for_member(db_session: AsyncSession) -> None:
    from services.remediation_pr_service import (
        create_npm_remediation_pr,
        list_remediation_prs,
    )

    team, admin, project, _scan, pkg = await _seed_opted_in_project(db_session)
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")
    transport = httpx.MockTransport(_github_handler())
    async with httpx.AsyncClient(transport=transport) as client:
        await create_npm_remediation_pr(
            db_session, actor, project.id,
            manifest_override=_manifest_for(pkg), http_client=client,
        )

    # A plain developer member can READ the list.
    dev = await make_user(db_session)
    await make_membership(db_session, user=dev, team=team, role="developer")
    dev_actor = principal_for(dev, team_ids=[team.id], role="developer")
    rows, total = await list_remediation_prs(db_session, dev_actor, project.id)
    assert total == 1
    assert rows[0].project_id == project.id


async def test_list_non_member_blocked(db_session: AsyncSession) -> None:
    from services.remediation_pr_service import list_remediation_prs
    from services.remediation_service import ProjectNotAccessible

    _team, _admin, project, _scan, _pkg = await _seed_opted_in_project(db_session)
    outsider = await make_user(db_session)
    actor = principal_for(outsider, team_ids=[], role="developer")
    with pytest.raises(ProjectNotAccessible):
        await list_remediation_prs(db_session, actor, project.id)


# ---------------------------------------------------------------------------
# Endpoint (RFC 7807 + auth + status codes)
# ---------------------------------------------------------------------------


def _bearer_for(user) -> dict[str, str]:
    from core.security import create_access_token

    role = "super_admin" if user.is_superuser else None
    token = create_access_token(subject=str(user.id), role=role)
    return {"Authorization": f"Bearer {token}"}


async def test_endpoint_requires_auth(db_session: AsyncSession) -> None:
    from main import app

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/v1/projects/{uuid.uuid4()}/remediation/npm/pull-request",
            json={"manifest": '{"dependencies": {}}'},
        )
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


async def test_endpoint_not_opted_in_problem(db_session: AsyncSession) -> None:
    """A team_admin hitting a not-opted-in project gets a 409 RFC 7807 problem."""
    from main import app

    team, admin, project, _scan, _pkg = await _seed_opted_in_project(
        db_session, repository_full_name=None
    )
    # Build a real bearer; the endpoint loads the admin's memberships from the DB.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/v1/projects/{project.id}/remediation/npm/pull-request",
            json={"manifest": '{"dependencies": {}}'},
            headers=_bearer_for(admin),
        )
    assert resp.status_code == 409
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    for key in ("type", "title", "status", "detail", "instance"):
        assert key in body
    assert body["status"] == 409


async def test_endpoint_list_unknown_project_problem(db_session: AsyncSession) -> None:
    from main import app

    user = await make_user(db_session)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/v1/projects/{uuid.uuid4()}/remediation/pull-requests",
            headers=_bearer_for(user),
        )
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["status"] == 404


async def test_endpoint_list_returns_seeded_record(db_session: AsyncSession) -> None:
    """Full HTTP path for the list endpoint — exercises _record_to_out."""
    from main import app
    from services.remediation_pr_service import create_npm_remediation_pr

    team, admin, project, _scan, pkg = await _seed_opted_in_project(db_session)
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")
    transport = httpx.MockTransport(_github_handler(pr_number=99))
    async with httpx.AsyncClient(transport=transport) as gh:
        await create_npm_remediation_pr(
            db_session, actor, project.id,
            manifest_override=_manifest_for(pkg), http_client=gh,
        )

    asgi = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=asgi, base_url="http://test") as client:
        resp = await client.get(
            f"/v1/projects/{project.id}/remediation/pull-requests",
            headers=_bearer_for(admin),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["status"] == "open"
    assert item["pr_number"] == 99
    # The JSONB from/to keys round-trip through the aliased schema.
    assert item["package_changes"][0]["to"] == "4.17.21"
    assert item["package_changes"][0]["package"] == pkg


async def test_endpoint_create_201_200_204(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP-layer create branches: 201 (new), 200 (idempotent), 204 (no-op).

    The GitHub write is not reachable through the ASGI app (the endpoint does not
    take an http_client), so we patch the service to return canned results and
    assert the router maps each to the right status + envelope.
    """
    import api.v1.remediation as remediation_api
    from main import app
    from models import RemediationPullRequest
    from services.remediation_pr_service import RemediationPRResult

    team, admin, project, _scan, _pkg = await _seed_opted_in_project(db_session)

    record = RemediationPullRequest(
        id=uuid.uuid4(),
        project_id=project.id,
        ecosystem="npm",
        repository_full_name="acme/widget",
        head_branch="trustedoss/remediation-deadbeef",
        base_branch="main",
        pr_number=5,
        pr_url="https://github.com/acme/widget/pull/5",
        status="open",
        package_changes=[{"package": "lodash", "from": "4.17.20", "to": "4.17.21"}],
        change_fingerprint="deadbeef",
    )
    from datetime import UTC, datetime

    record.created_at = datetime.now(tz=UTC)
    record.updated_at = datetime.now(tz=UTC)

    canned = {"result": RemediationPRResult(record=record, created=True)}

    async def _fake_create(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return canned["result"]

    monkeypatch.setattr(remediation_api, "create_npm_remediation_pr", _fake_create)

    asgi = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=asgi, base_url="http://test") as client:
        # 201 — newly created.
        r1 = await client.post(
            f"/v1/projects/{project.id}/remediation/npm/pull-request",
            json={"manifest": "{}"},
            headers=_bearer_for(admin),
        )
        assert r1.status_code == 201, r1.text
        body1 = r1.json()
        assert body1["pr_number"] == 5
        # package_changes uses the `from`/`to` wire keys (by-alias), consistent
        # with the GET list endpoint.
        assert body1["package_changes"][0]["to"] == "4.17.21"
        assert body1["package_changes"][0]["from"] == "4.17.20"

        # 200 — idempotent hit (created=False, record present).
        canned["result"] = RemediationPRResult(record=record, created=False)
        r2 = await client.post(
            f"/v1/projects/{project.id}/remediation/npm/pull-request",
            json={"manifest": "{}"},
            headers=_bearer_for(admin),
        )
        assert r2.status_code == 200, r2.text

        # 204 — nothing to remediate.
        canned["result"] = RemediationPRResult(
            record=None, created=False, no_op_reason="no_manifest_change"
        )
        r3 = await client.post(
            f"/v1/projects/{project.id}/remediation/npm/pull-request",
            json={"manifest": "{}"},
            headers=_bearer_for(admin),
        )
        assert r3.status_code == 204
