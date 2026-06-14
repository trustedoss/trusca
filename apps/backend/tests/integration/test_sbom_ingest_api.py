"""
Integration tests for POST /v1/projects/{project_id}/sbom-ingest — feat/sbom-ingest-endpoint.

Pins the HTTP contract over the real ASGI app + Postgres. This is the
synchronous front-half of the SBOM-ingest feature: it validates an uploaded
CycloneDX JSON document, persists a ``kind="sbom"`` queued scan row, writes the
SBOM to a durable on-disk path, and enqueues the Celery task (which we
short-circuit here via a monkeypatched ``enqueue_scan``).

Guard-order contract (CLAUDE.md §2 rule 1 — authz/existence ALWAYS before
state): a cross-team caller hits the permission gate (403, matching the
scan-trigger endpoint's existing contract — see note below) BEFORE any
state-derived 409, even when the target project has an active scan. The
existence/state cross-product for the new 409 surfaces lives in
``test_existence_hide_state_matrix.py``.

NOTE on 403-vs-404: the sbom-ingest path reuses ``prepare_scan_target`` and
maps ``ScanForbidden`` → 403 (NOT existence-hiding 404), identical to
``POST /v1/projects/{id}/scans`` (``test_scans_api.py::
test_trigger_scan_other_team_returns_403``). The feature spec's "non-member →
404" wording does not match this domain's actual contract; we assert 403 here
and the matrix file asserts the ``ScanForbidden`` permission-beats-state
ordering at the service layer.

The autouse ``_stub_enqueue_scan`` conftest fixture patches
``services.scan_service.enqueue_scan`` — but ``sbom_ingest_service`` imports
``enqueue_scan`` from ``tasks`` directly, so we patch
``services.sbom_ingest_service.enqueue_scan`` ourselves in an autouse fixture
below to keep these tests off the real broker.
"""

from __future__ import annotations

import json
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
FIXTURES = BACKEND_ROOT / "tests" / "fixtures" / "sbom_ingest"

pytestmark = pytest.mark.integration

# A static, valid CycloneDX document the happy-path tests upload. Realistic
# density (multiple components, multiple ecosystems, nested + dependencies)
# lives in tests/fixtures/sbom_ingest/realistic.cdx.json; this inline minimal
# doc keeps the HTTP-contract tests fast where density is irrelevant.
_VALID_SBOM = json.dumps(
    {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "components": [
            {
                "type": "library",
                "name": "lodash",
                "version": "4.17.19",
                "purl": "pkg:npm/lodash@4.17.19",
            }
        ],
    }
).encode("utf-8")

_STUB_TASK_ID = "11111111-2222-3333-4444-555555555555"


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip sbom-ingest API tests")
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
            f"alembic upgrade head failed; sbom-ingest API tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point WORKSPACE_HOST_PATH at a per-test tmp dir so the durable SBOM file
    write lands somewhere isolated and inspectable."""
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _stub_ingest_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Short-circuit the Celery dispatch on the SBOM-ingest path.

    ``sbom_ingest_service`` does ``from tasks import enqueue_scan``, so the
    conftest stub (which patches ``services.scan_service.enqueue_scan``) does
    NOT cover it. Patch the name bound in the ingest service module.
    """
    import services.sbom_ingest_service as svc

    monkeypatch.setattr(svc, "enqueue_scan", lambda scan: _STUB_TASK_ID)


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


async def _seed(client: AsyncClient, *, role: str = "developer", is_superuser: bool = False):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session, is_superuser=is_superuser)
        if not is_superuser:
            await make_membership(session, user=user, team=team, role=role)
        project = await make_project(session, team=team)
    return team, user, project


async def _seed_extra_project(client: AsyncClient, *, team_id: uuid.UUID):
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Team

        team = (
            await session.execute(select(Team).where(Team.id == team_id))
        ).scalar_one()
        return await make_project(session, team=team)


async def _seed_active_scan(client: AsyncClient, *, project_id: uuid.UUID, status: str):
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Project

        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one()
        scan = await make_scan(session, project=project, status=status)
        return scan.id


def _sbom_part(
    body: bytes = _VALID_SBOM,
    *,
    name: str = "bom.cdx.json",
    ctype: str = "application/json",
):
    return {"sbom": (name, body, ctype)}


async def _issue_project_api_key(
    client: AsyncClient, *, user: User, project_id: uuid.UUID
) -> str:
    resp = await client.post(
        "/v1/api-keys",
        json={"name": "ci-ingest", "scope": "project", "project_id": str(project_id)},
        headers=_bearer_for(user),
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["raw_key"])


def _ingest_dir(workspace: Path, project_id: uuid.UUID) -> Path:
    return workspace / "sbom-ingest" / str(project_id)


# ---------------------------------------------------------------------------
# Happy path — 202 + queued sbom scan row, durable file, metadata stamped
# ---------------------------------------------------------------------------


async def test_developer_ingest_returns_202_queued_sbom_scan(
    client, _workspace: Path
) -> None:
    team, user, project = await _seed(client, role="developer")

    resp = await client.post(
        f"/v1/projects/{project.id}/sbom-ingest",
        headers=_bearer_for(user),
        files=_sbom_part(name="my-bom.cdx.json"),
        data={"ref": "refs/heads/main", "release": "v1.2.3"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["project_id"] == str(project.id)
    assert body["kind"] == "sbom"
    assert body["status"] == "queued"
    assert body["progress_percent"] == 0
    # enqueue stub returned the deterministic task id.
    assert body["celery_task_id"] == _STUB_TASK_ID
    # ScanPublic surfaces `metadata` (alias), not `scan_metadata`.
    assert "scan_metadata" not in body
    meta = body["metadata"]
    assert meta["source_type"] == "sbom"
    assert meta["release"] == "v1.2.3"
    assert meta["original_filename"] == "my-bom.cdx.json"
    scan_id = body["id"]
    assert meta["sbom_path"] == str(
        _ingest_dir(_workspace, project.id) / f"{scan_id}.cdx.json"
    )

    # The durable SBOM file was written at the stamped path with the uploaded bytes.
    durable = _ingest_dir(_workspace, project.id) / f"{scan_id}.cdx.json"
    assert durable.is_file()
    assert durable.read_bytes() == _VALID_SBOM


async def test_ingest_without_ref_or_release_stamps_nulls(
    client, _workspace: Path
) -> None:
    _team, user, project = await _seed(client, role="developer")
    resp = await client.post(
        f"/v1/projects/{project.id}/sbom-ingest",
        headers=_bearer_for(user),
        files=_sbom_part(),
    )
    assert resp.status_code == 202, resp.text
    meta = resp.json()["metadata"]
    assert meta["release"] is None
    assert meta["source_type"] == "sbom"


async def test_ingest_accepts_realistic_density_fixture(
    client, _workspace: Path
) -> None:
    """Upload the real-density CycloneDX fixture (multiple ecosystems, nested
    components, dependencies). The synchronous validator accepts it (the deep
    parse is the worker's job) and returns 202."""
    _team, user, project = await _seed(client, role="developer")
    body = (FIXTURES / "realistic.cdx.json").read_bytes()
    resp = await client.post(
        f"/v1/projects/{project.id}/sbom-ingest",
        headers=_bearer_for(user),
        files=_sbom_part(body, name="realistic.cdx.json"),
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "queued"


# ---------------------------------------------------------------------------
# API-key auth (CI pushes SBOMs with a tos_ key)
# ---------------------------------------------------------------------------


async def test_ingest_accepts_project_scoped_api_key(client) -> None:
    _team, user, project = await _seed(client, role="developer")
    raw_key = await _issue_project_api_key(client, user=user, project_id=project.id)
    resp = await client.post(
        f"/v1/projects/{project.id}/sbom-ingest",
        headers={"Authorization": f"Bearer {raw_key}"},
        files=_sbom_part(),
    )
    assert resp.status_code == 202, resp.text


async def test_ingest_rejects_anonymous(client) -> None:
    _team, _user, project = await _seed(client, role="developer")
    resp = await client.post(
        f"/v1/projects/{project.id}/sbom-ingest", files=_sbom_part()
    )
    assert resp.status_code == 401


async def test_api_key_cannot_ingest_other_teams_project(client) -> None:
    _teamA, userA, projectA = await _seed(client, role="developer")
    _teamB, _userB, projectB = await _seed(client, role="developer")
    raw_key = await _issue_project_api_key(client, user=userA, project_id=projectA.id)
    resp = await client.post(
        f"/v1/projects/{projectB.id}/sbom-ingest",
        headers={"Authorization": f"Bearer {raw_key}"},
        files=_sbom_part(),
    )
    # Project-scope boundary → 403 (ScanForbidden), like the scan-trigger key test.
    assert resp.status_code == 403, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Authz / existence / state — guard ordering (CLAUDE.md §2 rule 1)
# ---------------------------------------------------------------------------


async def test_ingest_other_team_returns_403(client) -> None:
    _team, _owner, target_project = await _seed(client, role="developer")
    _team2, outsider, _p2 = await _seed(client, role="developer")
    resp = await client.post(
        f"/v1/projects/{target_project.id}/sbom-ingest",
        headers=_bearer_for(outsider),
        files=_sbom_part(),
    )
    # Domain contract mirrors scan-trigger: cross-team is 403, not existence-hide.
    assert resp.status_code == 403, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)


async def test_ingest_other_team_with_active_scan_is_403_not_409(client) -> None:
    """Permission BEATS state: a non-member uploading to a project that already
    has an active scan must get the 403 permission denial, NEVER the 409
    scan-already-in-progress (which would confirm the project + its busy state).
    """
    _team, _owner, target_project = await _seed(client, role="developer")
    await _seed_active_scan(client, project_id=target_project.id, status="running")
    _team2, outsider, _p2 = await _seed(client, role="developer")

    resp = await client.post(
        f"/v1/projects/{target_project.id}/sbom-ingest",
        headers=_bearer_for(outsider),
        files=_sbom_part(),
    )
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert "scan_already_in_progress" not in body


async def test_ingest_unknown_project_returns_404(client) -> None:
    _team, admin, _project = await _seed(
        client, role="developer", is_superuser=True
    )
    resp = await client.post(
        f"/v1/projects/{uuid.uuid4()}/sbom-ingest",
        headers=_bearer_for(admin),
        files=_sbom_part(),
    )
    assert resp.status_code == 404, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)


async def test_ingest_on_archived_project_returns_409(client) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import update as sa_update

    from models import Project as ProjectModel

    _team, user, project = await _seed(client, role="developer")
    factory = await _factory(client)
    async with factory() as session:
        await session.execute(
            sa_update(ProjectModel)
            .where(ProjectModel.id == project.id)
            .values(archived_at=datetime.now(tz=UTC))
        )
        await session.commit()

    resp = await client.post(
        f"/v1/projects/{project.id}/sbom-ingest",
        headers=_bearer_for(user),
        files=_sbom_part(),
    )
    assert resp.status_code == 409, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)
    assert resp.json()["title"] == "Project Archived"


async def test_ingest_with_active_scan_returns_409_in_progress(
    client, _workspace: Path
) -> None:
    """A member uploading while a scan is already queued/running for the project
    gets the 409 scan_already_in_progress contract — AND no loser SBOM file is
    written for the rejected upload (atomicity)."""
    _team, user, project = await _seed(client, role="developer")
    await _seed_active_scan(client, project_id=project.id, status="running")

    resp = await client.post(
        f"/v1/projects/{project.id}/sbom-ingest",
        headers=_bearer_for(user),
        files=_sbom_part(),
    )
    assert resp.status_code == 409, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)
    body = resp.json()
    assert body["title"] == "Scan Already In Progress"
    assert body.get("scan_already_in_progress") is True

    # Atomicity: the 409 loser never reached the file-write step (the scan_id is
    # only minted after winning the active-scan race), so the project's
    # sbom-ingest dir holds no file for this rejected attempt. The dir may not
    # even exist; if it does, it must be empty.
    ingest_dir = _ingest_dir(_workspace, project.id)
    if ingest_dir.exists():
        assert list(ingest_dir.iterdir()) == []


async def test_ingest_concurrency_cap_returns_429(client, monkeypatch) -> None:
    """B1 per-team concurrency cap applies to SBOM ingest too: 429 + Retry-After
    + machine-checkable `limit` extension."""
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "1")
    monkeypatch.setenv("SCAN_TRIGGER_RATE_LIMIT", "100/minute")

    team, user, project1 = await _seed(client, role="developer")
    project2 = await _seed_extra_project(client, team_id=team.id)
    headers = _bearer_for(user)

    r1 = await client.post(
        f"/v1/projects/{project1.id}/sbom-ingest", headers=headers, files=_sbom_part()
    )
    assert r1.status_code == 202, r1.text

    r2 = await client.post(
        f"/v1/projects/{project2.id}/sbom-ingest", headers=headers, files=_sbom_part()
    )
    assert r2.status_code == 429, r2.text
    assert r2.headers["content-type"].startswith(PROBLEM_JSON)
    assert "Retry-After" in r2.headers
    body = r2.json()
    assert body["type"] == "urn:trustedoss:problem:concurrent_scan_limit"
    assert body["limit"] == 1
    assert "running_scans" not in body


# ---------------------------------------------------------------------------
# Rate limit — SHARED scan_trigger bucket (project spray cannot bypass)
# ---------------------------------------------------------------------------


async def test_ingest_shares_scan_trigger_rate_limit_bucket(
    client, monkeypatch
) -> None:
    """The ingest endpoint draws from the SAME per-user `scan_trigger` bucket as
    POST /scans. Spreading uploads across DISTINCT projects must NOT bypass the
    cap (the limiter keys by user, not by {project_id})."""
    monkeypatch.setenv("SCAN_TRIGGER_RATE_LIMIT", "2/minute")
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "0")  # isolate the limiter

    team, user, project1 = await _seed(client, role="developer")
    project2 = await _seed_extra_project(client, team_id=team.id)
    project3 = await _seed_extra_project(client, team_id=team.id)
    headers = _bearer_for(user)

    r1 = await client.post(
        f"/v1/projects/{project1.id}/sbom-ingest", headers=headers, files=_sbom_part()
    )
    r2 = await client.post(
        f"/v1/projects/{project2.id}/sbom-ingest", headers=headers, files=_sbom_part()
    )
    assert r1.status_code == 202, r1.text
    assert r2.status_code == 202, r2.text

    # Third upload (distinct project) within the same minute exceeds 2/minute.
    r3 = await client.post(
        f"/v1/projects/{project3.id}/sbom-ingest", headers=headers, files=_sbom_part()
    )
    assert r3.status_code == 429, r3.text
    assert "Retry-After" in r3.headers


async def test_ingest_and_scan_trigger_share_one_bucket(client, monkeypatch) -> None:
    """Cross-surface: one POST /scans + one POST /sbom-ingest by the same user
    exhausts a 2/minute shared budget; a third creation on either surface is
    429. Proves the two scan-creating endpoints draw from ONE bucket, not two."""
    monkeypatch.setenv("SCAN_TRIGGER_RATE_LIMIT", "2/minute")
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "0")

    team, user, project1 = await _seed(client, role="developer")
    project2 = await _seed_extra_project(client, team_id=team.id)
    project3 = await _seed_extra_project(client, team_id=team.id)
    headers = _bearer_for(user)

    a = await client.post(
        f"/v1/projects/{project1.id}/scans", headers=headers, json={"kind": "source"}
    )
    b = await client.post(
        f"/v1/projects/{project2.id}/sbom-ingest", headers=headers, files=_sbom_part()
    )
    assert a.status_code == 202, a.text
    assert b.status_code == 202, b.text

    c = await client.post(
        f"/v1/projects/{project3.id}/sbom-ingest", headers=headers, files=_sbom_part()
    )
    assert c.status_code == 429, c.text


# ---------------------------------------------------------------------------
# Request validation — 413 / 415 / 422 at the HTTP layer
# ---------------------------------------------------------------------------


async def test_ingest_oversized_returns_413(client, monkeypatch) -> None:
    monkeypatch.setenv("SBOM_INGEST_MAX_BYTES", "256")
    _team, user, project = await _seed(client, role="developer")
    big = json.dumps(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "components": [{"type": "library", "name": "x" * 4096}],
        }
    ).encode("utf-8")
    assert len(big) > 256
    resp = await client.post(
        f"/v1/projects/{project.id}/sbom-ingest",
        headers=_bearer_for(user),
        files=_sbom_part(big),
    )
    assert resp.status_code == 413, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)
    body = resp.json()
    assert body["status"] == 413
    assert body["type"].endswith("sbom-ingest-too-large")


async def test_ingest_wrong_type_and_extension_returns_415(client) -> None:
    _team, user, project = await _seed(client, role="developer")
    resp = await client.post(
        f"/v1/projects/{project.id}/sbom-ingest",
        headers=_bearer_for(user),
        files=_sbom_part(_VALID_SBOM, name="evil.html", ctype="text/html"),
    )
    assert resp.status_code == 415, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)
    body = resp.json()
    assert body["status"] == 415
    assert body["type"].endswith("sbom-ingest-unsupported-type")


@pytest.mark.parametrize(
    ("payload", "label"),
    [
        (b"this is not json", "non-json"),
        (b"[]", "top-level-array"),
        (json.dumps({"bomFormat": "SPDX", "specVersion": "1.5"}).encode(), "wrong-format"),
        (json.dumps({"bomFormat": "CycloneDX", "specVersion": "2.0"}).encode(), "bad-version"),
        (
            json.dumps(
                {"bomFormat": "CycloneDX", "specVersion": "1.5", "components": {}}
            ).encode(),
            "components-not-list",
        ),
    ],
)
async def test_ingest_invalid_document_returns_422(
    client, payload: bytes, label: str
) -> None:
    _team, user, project = await _seed(client, role="developer")
    resp = await client.post(
        f"/v1/projects/{project.id}/sbom-ingest",
        headers=_bearer_for(user),
        files=_sbom_part(payload, name="bom.json"),
    )
    assert resp.status_code == 422, f"{label}: {resp.text}"
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)
    body = resp.json()
    assert body["status"] == 422
    assert body["type"].endswith("sbom-ingest-invalid")


async def test_ingest_too_many_components_returns_422(client, monkeypatch) -> None:
    monkeypatch.setenv("SBOM_INGEST_MAX_COMPONENTS", "2")
    _team, user, project = await _seed(client, role="developer")
    payload = json.dumps(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "components": [{"type": "library", "name": f"c{i}"} for i in range(3)],
        }
    ).encode("utf-8")
    resp = await client.post(
        f"/v1/projects/{project.id}/sbom-ingest",
        headers=_bearer_for(user),
        files=_sbom_part(payload, name="bom.json"),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["type"].endswith("sbom-ingest-invalid")


# ---------------------------------------------------------------------------
# Lifecycle / atomicity — enqueue failure marks scan failed + 503
# ---------------------------------------------------------------------------


async def test_ingest_enqueue_failure_marks_scan_failed_and_returns_503(
    client, monkeypatch, _workspace: Path
) -> None:
    """If the Celery dispatch raises, the row is flipped to failed with the
    deterministic `enqueue_failed:` prefix and the endpoint surfaces 503
    (RFC 7807). The durable SBOM file is left in place (see service docstring)."""
    import services.sbom_ingest_service as svc

    def _boom(scan):  # type: ignore[no-untyped-def]
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr(svc, "enqueue_scan", _boom)

    _team, user, project = await _seed(client, role="developer")
    resp = await client.post(
        f"/v1/projects/{project.id}/sbom-ingest",
        headers=_bearer_for(user),
        files=_sbom_part(),
    )
    assert resp.status_code == 503, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)

    # The persisted scan row is failed with the enqueue_failed prefix.
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Scan

        scan = (
            await session.execute(
                select(Scan).where(Scan.project_id == project.id)
            )
        ).scalar_one()
        assert scan.status == "failed"
        assert scan.error_message is not None
        assert scan.error_message.startswith("enqueue_failed:")


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/scans/{scan_id}/conformance
#
# Conformance read surface. Permission×state ordering (CLAUDE.md §2 rule 1): a
# non-member sees 404 (existence-hide) regardless of whether a verdict exists —
# the project authz gate fires before any per-scan lookup. A member with no
# verdict row (or a cross-project scan_id) also sees 404, never a leak.
# ---------------------------------------------------------------------------


async def _seed_sbom_scan_with_verdict(
    client: AsyncClient,
    *,
    project_id: uuid.UUID,
    result: str = "warn",
    source_format: str = "cyclonedx",
):
    """Insert a succeeded sbom scan + its conformance verdict; return scan_id."""
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Project, SbomConformance

        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one()
        scan = await make_scan(session, project=project, kind="sbom", status="succeeded")
        session.add(
            SbomConformance(
                scan_id=scan.id,
                project_id=project_id,
                source_format=source_format,
                result=result,
                n_fail=0,
                n_warn=1,
                component_count=4,
                purl_coverage_pct=100,
                license_coverage_pct=100,
                hash_coverage_pct=0,
                checks=[
                    {
                        "id": "purl",
                        "label": "PURL coverage (>= 90%)",
                        "required": True,
                        "status": "pass",
                        "detail": "100% (4/4)",
                        "missing": [],
                    },
                    {
                        "id": "hash",
                        "label": "Hash coverage (>= 50%, recommended)",
                        "required": False,
                        "status": "warn",
                        "detail": "0% (0/4)",
                        "missing": [],
                    },
                ],
            )
        )
        await session.commit()
        return scan.id


async def test_get_conformance_returns_verdict(client) -> None:
    team, user, project = await _seed(client, role="developer")
    scan_id = await _seed_sbom_scan_with_verdict(client, project_id=project.id)

    resp = await client.get(
        f"/v1/projects/{project.id}/scans/{scan_id}/conformance",
        headers=_bearer_for(user),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["result"] == "warn"
    assert body["source_format"] == "cyclonedx"
    assert body["purl_coverage_pct"] == 100
    assert body["component_count"] == 4
    assert {c["id"] for c in body["checks"]} == {"purl", "hash"}


async def test_get_conformance_cross_team_is_404(client) -> None:
    # Project + verdict owned by team A.
    _team_a, _user_a, project = await _seed(client, role="developer")
    scan_id = await _seed_sbom_scan_with_verdict(client, project_id=project.id)

    # A developer on an unrelated team B must see 404 (existence-hide): the
    # project authz gate fires before the per-scan verdict lookup.
    _team_b, user_b, _project_b = await _seed(client, role="developer")
    resp = await client.get(
        f"/v1/projects/{project.id}/scans/{scan_id}/conformance",
        headers=_bearer_for(user_b),
    )
    assert resp.status_code == 404, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)


async def test_get_conformance_missing_verdict_is_404(client) -> None:
    # A scan that exists in the project but has no verdict row (e.g. ingest
    # still queued, or a non-sbom scan) → 404, never a 500/empty 200.
    team, user, project = await _seed(client, role="developer")
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Project

        p = (
            await session.execute(select(Project).where(Project.id == project.id))
        ).scalar_one()
        scan = await make_scan(session, project=p, kind="sbom", status="queued")
        await session.commit()
        scan_id = scan.id

    resp = await client.get(
        f"/v1/projects/{project.id}/scans/{scan_id}/conformance",
        headers=_bearer_for(user),
    )
    assert resp.status_code == 404, resp.text


async def test_get_conformance_scan_in_other_project_is_404(client) -> None:
    # The (scan_id, project_id) predicate must reject a verdict whose scan lives
    # in a DIFFERENT project of the same team — no cross-project read.
    team, user, project = await _seed(client, role="developer")
    scan_id = await _seed_sbom_scan_with_verdict(client, project_id=project.id)
    other = await _seed_extra_project(client, team_id=team.id)

    resp = await client.get(
        f"/v1/projects/{other.id}/scans/{scan_id}/conformance",
        headers=_bearer_for(user),
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# SPDX input — the endpoint now ACCEPTS SPDX (JSON / Tag-Value), not just
# CycloneDX. enqueue is stubbed, so these assert the front-half (202 + queued
# sbom scan row) without running the worker.
# ---------------------------------------------------------------------------

_VALID_SPDX_JSON = json.dumps(
    {
        "spdxVersion": "SPDX-2.3",
        "name": "doc",
        "creationInfo": {"created": "2026-01-01T00:00:00Z", "creators": ["Tool: syft"]},
        "packages": [
            {
                "SPDXID": "SPDXRef-a",
                "name": "lodash",
                "versionInfo": "4.17.19",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": "pkg:npm/lodash@4.17.19",
                    }
                ],
            }
        ],
    }
).encode()

_VALID_SPDX_TV = (
    b"SPDXVersion: SPDX-2.3\n"
    b"Created: 2026-01-01T00:00:00Z\n"
    b"Creator: Tool: syft\n"
    b"PackageName: lodash\n"
    b"SPDXID: SPDXRef-a\n"
    b"PackageVersion: 4.17.19\n"
    b"ExternalRef: PACKAGE-MANAGER purl pkg:npm/lodash@4.17.19\n"
)


async def test_ingest_spdx_json_returns_202(client, _workspace: Path) -> None:
    _team, user, project = await _seed(client, role="developer")
    resp = await client.post(
        f"/v1/projects/{project.id}/sbom-ingest",
        headers=_bearer_for(user),
        files=_sbom_part(_VALID_SPDX_JSON, name="bom.spdx.json", ctype="application/json"),
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["kind"] == "sbom"


async def test_ingest_spdx_tag_value_returns_202(client, _workspace: Path) -> None:
    _team, user, project = await _seed(client, role="developer")
    resp = await client.post(
        f"/v1/projects/{project.id}/sbom-ingest",
        headers=_bearer_for(user),
        # Tag-Value is not JSON; the .spdx filename carries it past the advisory
        # content-type gate, and the content sniff confirms SPDXVersion:.
        files=_sbom_part(_VALID_SPDX_TV, name="bom.spdx", ctype="application/octet-stream"),
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["kind"] == "sbom"
