"""
Integration tests for POST /v1/projects/{project_id}/source-archive — feat/zip-upload.

Pins the HTTP contract over the real ASGI app + Postgres:
  - 201 + {"archive_id": ...} on a valid zip upload by a team developer.
  - 401 when unauthenticated.
  - 404 (existence-hide, NOT 403) when the project is in another team.
  - 415 RFC 7807 on a non-zip body (magic-byte forgery).
  - 413 RFC 7807 when the body exceeds SOURCE_ARCHIVE_MAX_BYTES.
  - The returned archive_id is usable to trigger an upload-source scan (202).

All error responses must be application/problem+json with the standard fields.
"""

from __future__ import annotations

import io
import os
import subprocess
import uuid
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip source archive API tests")
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
            f"alembic upgrade head failed; source archive API tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    return tmp_path


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


async def _seed(client: AsyncClient, *, role: str = "developer"):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role=role)
        project = await make_project(session, team=team)
    return team, user, project


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _zip_part(body: bytes, *, name: str = "source.zip", ctype: str = "application/zip"):
    return {"upload": (name, body, ctype)}


# ---------------------------------------------------------------------------
# Happy path + auth
# ---------------------------------------------------------------------------


async def test_developer_can_upload_archive(client) -> None:
    _team, user, project = await _seed(client, role="developer")
    resp = await client.post(
        f"/v1/projects/{project.id}/source-archive",
        headers=_bearer_for(user),
        files=_zip_part(_zip_bytes({"src/app.py": b"x = 1\n"})),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    uuid.UUID(body["archive_id"])  # opaque but UUID-shaped


async def test_upload_requires_authentication(client) -> None:
    _team, _user, project = await _seed(client, role="developer")
    resp = await client.post(
        f"/v1/projects/{project.id}/source-archive",
        files=_zip_part(_zip_bytes({"a.txt": b"x"})),
    )
    assert resp.status_code == 401


async def test_upload_other_team_is_404_existence_hide(client) -> None:
    _team, _owner, target_project = await _seed(client, role="developer")
    _team2, outsider, _p2 = await _seed(client, role="developer")
    resp = await client.post(
        f"/v1/projects/{target_project.id}/source-archive",
        headers=_bearer_for(outsider),
        files=_zip_part(_zip_bytes({"a.txt": b"x"})),
    )
    assert resp.status_code == 404  # NOT 403 — no cross-team enumeration
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# RFC 7807 error envelopes
# ---------------------------------------------------------------------------


async def test_upload_non_zip_returns_415_problem(client) -> None:
    _team, user, project = await _seed(client, role="developer")
    resp = await client.post(
        f"/v1/projects/{project.id}/source-archive",
        headers=_bearer_for(user),
        files=_zip_part(b"GIF89a not a zip at all", ctype="application/zip"),
    )
    assert resp.status_code == 415
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)
    body = resp.json()
    for field in ("type", "title", "status", "detail", "instance"):
        assert field in body
    assert body["status"] == 415
    assert body["type"].startswith("https://docs.trustedoss.io/errors/source-archive")


async def test_upload_oversized_returns_413_problem(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_ARCHIVE_MAX_BYTES", "256")
    _team, user, project = await _seed(client, role="developer")
    big = _zip_bytes({"big.bin": b"A" * 8192})
    assert len(big) > 256
    resp = await client.post(
        f"/v1/projects/{project.id}/source-archive",
        headers=_bearer_for(user),
        files=_zip_part(big),
    )
    assert resp.status_code == 413
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)
    assert resp.json()["status"] == 413


async def test_upload_oversized_content_length_rejected_early(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M2-fix: a declared Content-Length over the cap is rejected up front (413).

    httpx sets Content-Length for the multipart body, which exceeds the 256-byte
    cap, so the endpoint short-circuits before the streamed-bytes guard.
    """
    monkeypatch.setenv("SOURCE_ARCHIVE_MAX_BYTES", "256")
    _team, user, project = await _seed(client, role="developer")
    big = _zip_bytes({"big.bin": b"A" * 8192})
    resp = await client.post(
        f"/v1/projects/{project.id}/source-archive",
        headers=_bearer_for(user),
        files=_zip_part(big),
    )
    assert resp.status_code == 413
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)
    body = resp.json()
    assert body["status"] == 413
    assert body["type"].endswith("source-archive-too-large")


async def test_upload_over_project_quota_returns_507_problem(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-fix (part b): once the per-project quota is exhausted, uploads 507."""
    # Generous single-upload cap; tiny per-project quota so the second upload
    # is refused by the cumulative guard, not the per-file cap.
    monkeypatch.setenv("SOURCE_ARCHIVE_MAX_BYTES", str(10 * 1024 * 1024))
    monkeypatch.setenv("SOURCE_ARCHIVE_PROJECT_QUOTA_BYTES", "300")
    _team, user, project = await _seed(client, role="developer")
    headers = _bearer_for(user)

    first = await client.post(
        f"/v1/projects/{project.id}/source-archive",
        headers=headers,
        files=_zip_part(_zip_bytes({"a.py": b"x = 1\n"})),
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        f"/v1/projects/{project.id}/source-archive",
        headers=headers,
        files=_zip_part(_zip_bytes({"b.py": b"y = 2\n"})),
    )
    assert second.status_code == 507
    assert second.headers["content-type"].startswith(PROBLEM_JSON)
    body = second.json()
    assert body["status"] == 507
    assert body["type"].endswith("source-archive-quota-exceeded")


# ---------------------------------------------------------------------------
# End-to-end: upload then trigger an upload-source scan
# ---------------------------------------------------------------------------


async def test_upload_then_trigger_upload_scan(client) -> None:
    _team, user, project = await _seed(client, role="developer")
    headers = _bearer_for(user)

    up = await client.post(
        f"/v1/projects/{project.id}/source-archive",
        headers=headers,
        files=_zip_part(_zip_bytes({"src/app.py": b"x = 1\n"})),
    )
    assert up.status_code == 201, up.text
    archive_id = up.json()["archive_id"]

    scan = await client.post(
        f"/v1/projects/{project.id}/scans",
        headers=headers,
        json={
            "kind": "source",
            "metadata": {"source_type": "upload", "archive_id": archive_id},
        },
    )
    assert scan.status_code == 202, scan.text
    body = scan.json()
    assert body["status"] == "queued"
    assert body["metadata"]["source_type"] == "upload"
    assert body["metadata"]["archive_id"] == archive_id


async def test_trigger_upload_scan_missing_archive_returns_404_problem(client) -> None:
    _team, user, project = await _seed(client, role="developer")
    resp = await client.post(
        f"/v1/projects/{project.id}/scans",
        headers=_bearer_for(user),
        json={
            "kind": "source",
            "metadata": {"source_type": "upload", "archive_id": str(uuid.uuid4())},
        },
    )
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)


async def test_trigger_upload_scan_without_archive_id_returns_422(client) -> None:
    """Schema-level: source_type='upload' with no archive_id is a 422."""
    _team, user, project = await _seed(client, role="developer")
    resp = await client.post(
        f"/v1/projects/{project.id}/scans",
        headers=_bearer_for(user),
        json={"kind": "source", "metadata": {"source_type": "upload"}},
    )
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)
