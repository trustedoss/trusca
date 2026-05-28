"""
Integration tests for ``GET /v1/scans/{scan_id}/log`` (scan-log download).

Endpoint contract (apps/backend/api/v1/scans.py::download_scan_log_endpoint):

  - 200 streams the file as ``text/plain; charset=utf-8`` with
    ``Content-Disposition: attachment; filename="scan-<uuid>.log"`` and
    ``X-Content-Type-Options: nosniff``.
  - 401 if unauthenticated.
  - 404 (existence-hide) for ALL miss paths — scan-not-found, cross-team
    access, log-file-not-on-disk, and the path-traversal defense — with the
    SAME byte-identical Problem Details body so a scripted attacker cannot
    use the envelope to enumerate scan ids across teams (security-reviewer
    HIGH finding fixed by commit 90813b8).
  - 422 if ``scan_id`` is not a valid UUID (FastAPI Pydantic validation —
    URL-escape attempts at path traversal trip this before they reach the
    handler).

Tests share the per-team seed pattern with ``test_scans_api.py`` and write
the on-disk log file directly under the configured workspace root so we
exercise the streaming + headers without needing to drive a real scan.
"""

from __future__ import annotations

import os
import subprocess
import uuid
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
        pytest.skip("DATABASE_URL not set — skip scan-log download tests")
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
            f"alembic upgrade head failed; scan-log download tests cannot run\n"
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


@pytest.fixture
def isolated_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point ``WORKSPACE_HOST_PATH`` at an empty per-test directory.

    Persistence is ON; the route reads ``workspace_root()`` per request so
    the env change takes effect immediately.
    """
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setenv("SCAN_LOG_PERSIST_ENABLED", "true")
    return tmp_path


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


async def _seed(
    client: AsyncClient,
    *,
    role: str = "developer",
    is_superuser: bool = False,
):
    """Seed organization + team + user (+ membership) + project. Returns ids."""
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session, is_superuser=is_superuser)
        if not is_superuser:
            await make_membership(session, user=user, team=team, role=role)
        project = await make_project(session, team=team)
    return team, user, project


async def _seed_scan(
    client: AsyncClient, *, project_id: uuid.UUID, status: str = "succeeded"
) -> uuid.UUID:
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Project

        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one()
        scan = await make_scan(session, project=project, status=status)
        return scan.id


def _write_log_file(workspace: Path, scan_id: uuid.UUID, content: bytes) -> Path:
    """Stage a ``<workspace>/<scan_id>/scan.log`` so the route has a file to serve."""
    scan_dir = workspace / str(scan_id)
    scan_dir.mkdir(parents=True, exist_ok=True)
    path = scan_dir / "scan.log"
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------------------
# 200 happy path
# ---------------------------------------------------------------------------


async def test_authorised_team_member_can_download_persisted_log(
    client: AsyncClient, isolated_workspace: Path
) -> None:
    """A team member sees 200 + the file content + the expected headers."""
    team, user, project = await _seed(client, role="developer")
    scan_id = await _seed_scan(client, project_id=project.id)
    headers = _bearer_for(user)

    body = (
        b"2026-05-28T01:02:03.000Z [cdxgen/stdout] resolving packages\n"
        b"2026-05-28T01:02:04.000Z [trivy/stdout] 2 vulnerabilities found\n"
    )
    _write_log_file(isolated_workspace, scan_id, body)

    response = await client.get(f"/v1/scans/{scan_id}/log", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/plain")
    assert "charset=utf-8" in response.headers["content-type"]
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="scan-{scan_id}.log"'
    )
    # Defense-in-depth header against MIME sniffing of attacker-controlled
    # stdout (e.g. a tool that emits HTML to its stderr).
    assert response.headers.get("x-content-type-options") == "nosniff"
    # Body matches byte-for-byte.
    assert response.content == body


async def test_filename_is_full_uuid_no_truncation(
    client: AsyncClient, isolated_workspace: Path
) -> None:
    """``Content-Disposition`` filename is ``scan-<full-uuid>.log`` verbatim.

    No path components, no truncation, no quote mangling. The scan id is
    UUID-validated at the route level so this assertion also covers the
    "what if a user-controlled string ever leaked into the filename" case.
    """
    team, user, project = await _seed(client, role="developer")
    scan_id = await _seed_scan(client, project_id=project.id)
    _write_log_file(isolated_workspace, scan_id, b"x")
    headers = _bearer_for(user)

    response = await client.get(f"/v1/scans/{scan_id}/log", headers=headers)
    assert response.status_code == 200

    disposition = response.headers["content-disposition"]
    expected = f'attachment; filename="scan-{scan_id}.log"'
    assert disposition == expected
    # No directory separators leaked into the filename.
    assert "/" not in disposition.split("filename=")[1]
    assert "\\" not in disposition.split("filename=")[1]


async def test_empty_log_file_returns_200_with_empty_body(
    client: AsyncClient, isolated_workspace: Path
) -> None:
    """A zero-byte ``scan.log`` (e.g. early-stage scan, no lines yet) is 200, not 404.

    The not-yet-written branch is 404 only when the FILE does not exist; a
    file that exists but has no content is a valid "the scan started but no
    tool has emitted a line" state.
    """
    team, user, project = await _seed(client, role="developer")
    scan_id = await _seed_scan(client, project_id=project.id, status="running")
    _write_log_file(isolated_workspace, scan_id, b"")
    headers = _bearer_for(user)

    response = await client.get(f"/v1/scans/{scan_id}/log", headers=headers)
    assert response.status_code == 200
    assert response.content == b""
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="scan-{scan_id}.log"'
    )


async def test_large_log_file_streams_intact(
    client: AsyncClient, isolated_workspace: Path
) -> None:
    """A multi-MB log streams end-to-end without truncation.

    The route uses StreamingResponse with a 64 KiB chunk size; httpx's test
    client materialises the full body, so we assert on body length to confirm
    the chunked read drains the whole file.
    """
    team, user, project = await _seed(client, role="developer")
    scan_id = await _seed_scan(client, project_id=project.id)
    payload = b"x" * 5_000_000  # 5 MB, well over the chunk size
    _write_log_file(isolated_workspace, scan_id, payload)
    headers = _bearer_for(user)

    response = await client.get(f"/v1/scans/{scan_id}/log", headers=headers)
    assert response.status_code == 200
    assert len(response.content) == len(payload)
    # First + last bytes match — the chunk boundary did not eat anything.
    assert response.content[:64] == payload[:64]
    assert response.content[-64:] == payload[-64:]


# ---------------------------------------------------------------------------
# 401 — unauthenticated
# ---------------------------------------------------------------------------


async def test_unauthenticated_caller_gets_401(
    client: AsyncClient, isolated_workspace: Path
) -> None:
    """No JWT → 401, regardless of whether the scan / file exists."""
    response = await client.get(f"/v1/scans/{uuid.uuid4()}/log")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# 404 — existence-hide IDOR critical test
# ---------------------------------------------------------------------------


async def test_404_envelope_is_byte_identical_across_miss_paths(
    client: AsyncClient, isolated_workspace: Path
) -> None:
    """Three distinct 404 branches MUST return BYTE-IDENTICAL bodies.

    This is the regression gate for the security-reviewer HIGH finding fixed
    by commit 90813b8: before the unification, the route emitted two
    different Problem Details envelopes (``urn:trustedoss:problem:scan_not_
    found`` vs ``…scan_log_not_yet_available``) and a scripted attacker
    enumerating UUIDs across teams could perfectly distinguish "valid scan
    in another team" from "non-existent scan", defeating the existence-hide
    gate the endpoint is built to enforce.

    The three branches:
      (a) scan exists, caller is in a DIFFERENT team (no access).
      (b) scan exists, caller is in the SAME team, but ``scan.log`` is not
          yet on disk (early stage / persistence disabled / cleaner reaped).
      (c) scan_id is a random UUID that does NOT exist anywhere.

    Assertion: all three return status 404 AND the response body bytes are
    identical across scenarios. We compare on ``response.content`` (raw
    bytes) not ``response.json()`` — JSON key order from another framework
    could differ even when the semantics match, and the byte-level equality
    is what an enumeration attack would see on the wire.
    """
    # --- Set up two teams: target_user owns target_scan; outsider does not.
    _, target_user, target_project = await _seed(client, role="developer")
    scan_id_a = await _seed_scan(client, project_id=target_project.id)

    _, outsider, outsider_project = await _seed(client, role="developer")
    scan_id_b = await _seed_scan(client, project_id=outsider_project.id)
    # Stage the FILE for scenario (b) — same-team caller, but with the scan
    # accessible, and crucially WITHOUT writing the on-disk log. The route
    # will resolve auth, then hit the "file not present" branch.
    # (No _write_log_file call here on purpose.)

    outsider_headers = _bearer_for(outsider)
    same_team_headers = _bearer_for(outsider)  # owner of scan_id_b

    # Random non-existent id — scenario (c).
    scan_id_c = uuid.uuid4()

    # (a) cross-team: outsider asking for target_user's scan. Even though the
    # scan exists, the team check fails and we collapse to 404.
    r_a = await client.get(f"/v1/scans/{scan_id_a}/log", headers=outsider_headers)
    # (b) same team, no file on disk yet.
    r_b = await client.get(f"/v1/scans/{scan_id_b}/log", headers=same_team_headers)
    # (c) random id, super-admin caller (rules out cross-team confusion).
    _, admin, _ = await _seed(client, role="developer", is_superuser=True)
    r_c = await client.get(
        f"/v1/scans/{scan_id_c}/log", headers=_bearer_for(admin)
    )

    # All three are 404.
    assert r_a.status_code == 404, r_a.text
    assert r_b.status_code == 404, r_b.text
    assert r_c.status_code == 404, r_c.text

    # All three carry the Problem Details content type.
    for resp in (r_a, r_b, r_c):
        assert resp.headers["content-type"].startswith(PROBLEM_JSON)

    # The envelopes are byte-for-byte identical. This is the existence-hide
    # contract — the security-reviewer's regression gate.
    assert r_a.content == r_b.content == r_c.content, (
        f"404 envelope drift detected\n"
        f"(a) cross-team  → {r_a.content!r}\n"
        f"(b) file-missing → {r_b.content!r}\n"
        f"(c) random-uuid → {r_c.content!r}"
    )


# ---------------------------------------------------------------------------
# 422 — path traversal defense (defense-in-depth)
# ---------------------------------------------------------------------------


async def test_path_traversal_attempt_returns_422_not_200(
    client: AsyncClient, isolated_workspace: Path
) -> None:
    """A url-encoded path-traversal attempt on the ``scan_id`` slot trips Pydantic.

    The route's ``scan_id: uuid.UUID`` annotation means FastAPI / Pydantic
    rejects any non-UUID at validation time with 422 — long before the
    request reaches the handler that builds the on-disk path. This test
    pins that fact so a future refactor that loosens the type (e.g. to
    ``str``) cannot silently introduce a traversal vector.
    """
    _, admin, _ = await _seed(client, role="developer", is_superuser=True)
    headers = _bearer_for(admin)

    # The literal request path that a naive concatenation bug would let
    # escape the workspace root.
    response = await client.get(
        "/v1/scans/..%2F..%2Fetc%2Fpasswd/log", headers=headers
    )
    assert response.status_code == 422, response.text
    # NOT 200 (we did not serve /etc/passwd) and NOT 500 (we did not crash
    # the handler on the path resolution).
    assert response.status_code != 200
    assert response.status_code != 500
