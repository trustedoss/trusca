"""
Integration tests for the SBOM signature download HTTP surface — v2.3-s3.

Endpoints (all under ``/v1/projects/{project_id}/sbom/...``):
  - signature, certificate, attestation, public-key, signature-bundle

Pins:
  - Each download returns 200 with the right Content-Type + attachment header
    when the artifact exists, 404 (problem+json) when it does not.
  - The public-key endpoint serves ONLY the .pub bytes — never the private key.
  - Outsiders see 404 (existence-hide) on the project, never 403.
  - Anonymous → 401.
  - The bundle is a valid zip carrying SBOM + signature + public key + README,
    and never contains the private key bytes.
"""

from __future__ import annotations

import io
import os
import subprocess
import uuid
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import ScanArtifact, User
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip sbom signature API tests")
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
            f"alembic upgrade head failed; sbom signature API tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _bearer_for(user: User) -> dict[str, str]:
    role = "super_admin" if user.is_superuser else None
    token = create_access_token(subject=str(user.id), role=role)
    return {"Authorization": f"Bearer {token}"}


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _seed_signed(
    client: AsyncClient,
    workspace: Path,
    *,
    role: str = "developer",
    is_superuser: bool = False,
    with_signature: bool = True,
    with_attestation: bool = False,
    with_attest_cert: bool = False,
):
    """Seed org/team/user/project + a succeeded scan with on-disk signing artifacts.

    Writes the SBOM + signature (+ optional attestation) files under ``workspace``
    (so the trust-boundary read inside ``WORKSPACE_HOST_PATH`` succeeds) and
    persists matching ``ScanArtifact`` rows.
    """
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session, is_superuser=is_superuser)
        if not is_superuser:
            await make_membership(session, user=user, team=team, role=role)
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="succeeded")
        project.latest_scan_id = scan.id
        project.updated_at = datetime.now(tz=UTC)

        scan_dir = workspace / str(scan.id)
        scan_dir.mkdir(parents=True, exist_ok=True)

        sbom_file = scan_dir / "cdxgen.cdx.json"
        sbom_file.write_bytes(b'{"bomFormat":"CycloneDX","specVersion":"1.5"}')
        session.add(
            ScanArtifact(
                scan_id=scan.id,
                kind="sbom_cyclonedx",
                storage_path=str(sbom_file),
                byte_size=sbom_file.stat().st_size,
            )
        )

        if with_signature:
            sig_file = scan_dir / "sbom.cdx.json.sig"
            sig_file.write_bytes(b"MOCK-SIGNATURE-BYTES")
            session.add(
                ScanArtifact(
                    scan_id=scan.id,
                    kind="sbom_cyclonedx_sig",
                    storage_path=str(sig_file),
                    byte_size=sig_file.stat().st_size,
                    sha256="deadbeef",
                )
            )

        if with_attestation:
            att_file = scan_dir / "sbom.intoto.jsonl"
            att_file.write_bytes(b'{"payloadType":"application/vnd.in-toto+json"}')
            session.add(
                ScanArtifact(
                    scan_id=scan.id,
                    kind="sbom_attestation",
                    storage_path=str(att_file),
                    byte_size=att_file.stat().st_size,
                    sha256="deadbeef",
                )
            )

        if with_attest_cert:
            ac_file = scan_dir / "sbom.attest.cert"
            ac_file.write_bytes(b"-----BEGIN CERTIFICATE-----\nAC\n-----END CERTIFICATE-----\n")
            session.add(
                ScanArtifact(
                    scan_id=scan.id,
                    kind="sbom_attest_cert",
                    storage_path=str(ac_file),
                    byte_size=ac_file.stat().st_size,
                )
            )

        await session.commit()
        await session.refresh(project)
    return team, user, project


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


async def test_signature_without_auth_returns_401(client: AsyncClient) -> None:
    response = await client.get(f"/v1/projects/{uuid.uuid4()}/sbom/signature")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_download_signature_returns_bytes(client: AsyncClient, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _, user, project = await _seed_signed(client, tmp_path)
    headers = _bearer_for(user)

    response = await client.get(f"/v1/projects/{project.id}/sbom/signature", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="sbom-{project.slug}.cdx.json.sig"'
    )
    assert response.content == b"MOCK-SIGNATURE-BYTES"


async def test_download_attestation_returns_bytes(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _, user, project = await _seed_signed(client, tmp_path, with_attestation=True)
    headers = _bearer_for(user)

    response = await client.get(f"/v1/projects/{project.id}/sbom/attestation", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/octet-stream")


async def test_download_public_key_serves_only_pub(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    # Configure a key pair: the PRIVATE key holds secret bytes that must NEVER
    # be served; the endpoint must serve only the .pub.
    private = tmp_path / "cosign.key"
    private.write_bytes(b"PRIVATE-KEY-SECRET-NEVER-EXPOSE")
    pub = tmp_path / "cosign.pub"
    pub.write_bytes(b"-----BEGIN PUBLIC KEY-----\nPUBLIC\n-----END PUBLIC KEY-----\n")
    monkeypatch.setenv("COSIGN_KEY_PATH", str(private))
    monkeypatch.delenv("COSIGN_PUBLIC_KEY_PATH", raising=False)

    _, user, project = await _seed_signed(client, tmp_path)
    headers = _bearer_for(user)

    response = await client.get(f"/v1/projects/{project.id}/sbom/public-key", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == 'attachment; filename="cosign.pub"'
    assert response.content == pub.read_bytes()
    assert b"PRIVATE-KEY-SECRET" not in response.content


async def test_download_bundle_is_valid_zip_without_private_key(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    private = tmp_path / "cosign.key"
    private.write_bytes(b"PRIVATE-KEY-SECRET-NEVER-EXPOSE")
    pub = tmp_path / "cosign.pub"
    # Valid PEM PUBLIC KEY header so the v2.3-s3 public-key guard accepts it.
    pub.write_bytes(b"-----BEGIN PUBLIC KEY-----\nPUBKEYBYTES\n-----END PUBLIC KEY-----\n")
    monkeypatch.setenv("COSIGN_KEY_PATH", str(private))
    monkeypatch.delenv("COSIGN_PUBLIC_KEY_PATH", raising=False)

    _, user, project = await _seed_signed(client, tmp_path, with_attestation=True)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/sbom/signature-bundle", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="sbom-signature-{project.slug}.zip"'
    )

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        names = set(zf.namelist())
    assert any(n.endswith(".cdx.json") for n in names)
    assert any(n.endswith(".cdx.json.sig") for n in names)
    assert "cosign.pub" in names
    assert "VERIFY.md" in names
    # The private key bytes must NEVER appear in the bundle.
    assert b"PRIVATE-KEY-SECRET" not in response.content


# ---------------------------------------------------------------------------
# Not-found paths (unsigned scan, missing cert, no public key)
# ---------------------------------------------------------------------------


async def test_signature_404_when_scan_unsigned(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _, user, project = await _seed_signed(client, tmp_path, with_signature=False)
    headers = _bearer_for(user)

    response = await client.get(f"/v1/projects/{project.id}/sbom/signature", headers=headers)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["title"] == "Signature Artifact Not Found"


async def test_certificate_404_when_key_based(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    # Key-based signing emits no certificate -> 404 with guidance to use the key.
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _, user, project = await _seed_signed(client, tmp_path)
    headers = _bearer_for(user)

    response = await client.get(f"/v1/projects/{project.id}/sbom/certificate", headers=headers)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_public_key_404_when_unconfigured(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.delenv("COSIGN_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.delenv("COSIGN_KEY_PATH", raising=False)
    _, user, project = await _seed_signed(client, tmp_path)
    headers = _bearer_for(user)

    response = await client.get(f"/v1/projects/{project.id}/sbom/public-key", headers=headers)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_bundle_404_when_unsigned(client: AsyncClient, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _, user, project = await _seed_signed(client, tmp_path, with_signature=False)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/sbom/signature-bundle", headers=headers
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# v2.3-s3 fix-first: public-key private-key refusal, size cap (413), attest cert
# ---------------------------------------------------------------------------


async def test_public_key_endpoint_refuses_private_key(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    # [Medium fix] COSIGN_PUBLIC_KEY_PATH is MISCONFIGURED to point at a PRIVATE
    # key (or a .pub symlink to it). The endpoint must NOT leak it: 404, and the
    # private bytes never appear in the response.
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    bad_pub = tmp_path / "cosign.pub"
    bad_pub.write_bytes(
        b"-----BEGIN PRIVATE KEY-----\nSECRET-PRIVATE-NEVER-SERVE\n-----END PRIVATE KEY-----\n"
    )
    monkeypatch.setenv("COSIGN_PUBLIC_KEY_PATH", str(bad_pub))

    _, user, project = await _seed_signed(client, tmp_path)
    headers = _bearer_for(user)

    response = await client.get(f"/v1/projects/{project.id}/sbom/public-key", headers=headers)
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert b"SECRET-PRIVATE" not in response.content


async def test_signature_413_when_artifact_over_cap(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    # [Low fix] An artifact whose persisted byte_size exceeds the cap returns 413
    # (RFC 7807), never streaming a huge body.
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setenv("SBOM_DOWNLOAD_MAX_BYTES", "5")
    _, user, project = await _seed_signed(client, tmp_path)  # sig file is 20 bytes
    headers = _bearer_for(user)

    response = await client.get(f"/v1/projects/{project.id}/sbom/signature", headers=headers)
    assert response.status_code == 413, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["title"] == "SBOM Artifact Too Large"


async def test_attestation_certificate_happy_path(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    # [Info fix] keyless attestation cert is downloadable on its own endpoint.
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _, user, project = await _seed_signed(
        client, tmp_path, with_attestation=True, with_attest_cert=True
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/sbom/attestation-certificate", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-pem-file")
    assert b"BEGIN CERTIFICATE" in response.content


async def test_attestation_certificate_404_when_key_based(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _, user, project = await _seed_signed(client, tmp_path)  # no attest cert
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/sbom/attestation-certificate", headers=headers
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_bundle_includes_keyless_attestation_cert(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    # [Info fix] keyless: bundle carries the attestation Fulcio cert so a consumer
    # can run cosign verify-blob-attestation from the bundle alone.
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.delenv("COSIGN_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.delenv("COSIGN_KEY_PATH", raising=False)  # keyless
    _, user, project = await _seed_signed(
        client, tmp_path, with_attestation=True, with_attest_cert=True
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/sbom/signature-bundle", headers=headers
    )
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        names = set(zf.namelist())
        readme = zf.read("VERIFY.md").decode()
    assert any(n.endswith(".attest.cert.pem") for n in names)
    assert "cosign verify-blob-attestation" in readme


# ---------------------------------------------------------------------------
# IDOR / RBAC — outsiders see 404 (existence-hide), never 403
# ---------------------------------------------------------------------------


async def test_signature_other_team_returns_404(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _, _, target = await _seed_signed(client, tmp_path)
    _, outsider, _ = await _seed_signed(client, tmp_path)
    headers = _bearer_for(outsider)

    response = await client.get(f"/v1/projects/{target.id}/sbom/signature", headers=headers)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    # Project existence-hide title (not the artifact-not-found title): an
    # outsider must not be able to tell the project exists.
    assert body["title"] == "Project Not Found"


async def test_bundle_other_team_returns_404(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _, _, target = await _seed_signed(client, tmp_path)
    _, outsider, _ = await _seed_signed(client, tmp_path)
    headers = _bearer_for(outsider)

    response = await client.get(
        f"/v1/projects/{target.id}/sbom/signature-bundle", headers=headers
    )
    assert response.status_code == 404
    body = response.json()
    assert body["title"] == "Project Not Found"


async def test_signature_unknown_project_returns_404(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _, admin, _ = await _seed_signed(client, tmp_path, is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(f"/v1/projects/{uuid.uuid4()}/sbom/signature", headers=headers)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_signature_super_admin_bypasses_team_check(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _, _, target = await _seed_signed(client, tmp_path)
    _, admin, _ = await _seed_signed(client, tmp_path, is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(f"/v1/projects/{target.id}/sbom/signature", headers=headers)
    assert response.status_code == 200, response.text
