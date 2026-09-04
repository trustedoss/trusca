# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Integration tests for the S7 429 wait estimate
(concurrency-scaling-plan-2026-08-22.md §1.1/§3.2/§4).

Regression contract (plan §4 S7 row): the 429 keeps its RFC 7807 body and
``Retry-After`` header exactly as before, and the live per-team active-scan
count is never exposed. ``test_scans_api.py`` already pins that shape; the
tests here add the ONE thing this unit changes - the new, optional
``estimated_wait_seconds`` field - without re-asserting the whole envelope.

Two scan-creating surfaces (``POST /v1/projects/{id}/scans`` and
``POST /v1/projects/{id}/sbom-ingest``) build their 429 body from mirrored,
hand-kept-in-sync ``_problem_for_scan_error`` functions (one in
``api/v1/projects.py``, one in ``api/v1/sbom.py``). CLAUDE.md hardening rule
2: the same vocabulary in two places needs a consistency test. This file's
mirror test drives BOTH endpoints against the SAME real broker backlog and
asserts they compute the identical field.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import redis as redis_lib
from httpx import ASGITransport, AsyncClient

from core.config import redis_url
from core.security import create_access_token
from models import User
from tasks.celery_app import _SCAN_QUEUE
from tests._db_required import migrate_to_head
from tests._helpers import make_membership, make_organization, make_project, make_team, make_user

PROBLEM_JSON = "application/problem+json"

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

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _disk_guard_headroom(monkeypatch: pytest.MonkeyPatch) -> None:
    """This suite's own workspace usage is irrelevant to what it tests
    (team-capacity / broker-backlog behaviour). A dev laptop's real disk can
    sit above the 95% default hard limit for reasons that have nothing to do
    with this test run, which would 503 every trigger before it ever reaches
    the concurrency-cap code path under test; 100 is the guard's own
    documented "effectively off" value."""
    monkeypatch.setenv("DISK_HARD_LIMIT_PCT", "100")


@pytest.fixture(autouse=True)
def _stub_ingest_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """See test_sbom_ingest_api.py's fixture of the same name: the ingest
    service imports ``enqueue_scan`` directly, so the conftest stub (which
    patches ``services.scan_service.enqueue_scan``) does not cover it."""
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


async def _seed_two_projects_one_team(client: AsyncClient):
    """A team + two projects, so the second project's trigger is the one that
    hits the (cap=1) team concurrency limit without colliding with the
    per-(project, ref) active-scan index."""
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")
        project1 = await make_project(session, team=team)
        project2 = await make_project(session, team=team)
    return user, project1, project2


def _sbom_part():
    return {"sbom": ("bom.cdx.json", _VALID_SBOM, "application/json")}


@pytest.fixture
def redis_conn():
    conn = redis_lib.Redis.from_url(os.getenv("REDIS_URL") or redis_url(), decode_responses=True)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Regression: metrics off (default) -> the field is absent, everything else
# in the envelope is unchanged.
# ---------------------------------------------------------------------------


async def test_scan_trigger_429_omits_wait_estimate_by_default(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "1")
    monkeypatch.setenv("SCAN_TRIGGER_RATE_LIMIT", "100/minute")
    monkeypatch.delenv("QUEUE_BACKLOG_METRICS_ENABLED", raising=False)

    user, project1, project2 = await _seed_two_projects_one_team(client)
    headers = _bearer_for(user)

    r1 = await client.post(
        f"/v1/projects/{project1.id}/scans", headers=headers, json={"kind": "source"}
    )
    assert r1.status_code == 202, r1.text

    r2 = await client.post(
        f"/v1/projects/{project2.id}/scans", headers=headers, json={"kind": "source"}
    )
    assert r2.status_code == 429, r2.text
    assert r2.headers["content-type"].startswith(PROBLEM_JSON)
    assert r2.headers["Retry-After"] == "30"
    body = r2.json()
    assert body["limit"] == 1
    assert "running_scans" not in body
    assert "estimated_wait_seconds" not in body


async def test_sbom_ingest_429_omits_wait_estimate_by_default(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "1")
    monkeypatch.setenv("SCAN_TRIGGER_RATE_LIMIT", "100/minute")
    monkeypatch.delenv("QUEUE_BACKLOG_METRICS_ENABLED", raising=False)

    user, project1, project2 = await _seed_two_projects_one_team(client)
    headers = _bearer_for(user)

    r1 = await client.post(
        f"/v1/projects/{project1.id}/sbom-ingest", headers=headers, files=_sbom_part()
    )
    assert r1.status_code == 202, r1.text

    r2 = await client.post(
        f"/v1/projects/{project2.id}/sbom-ingest", headers=headers, files=_sbom_part()
    )
    assert r2.status_code == 429, r2.text
    body = r2.json()
    assert body["limit"] == 1
    assert "running_scans" not in body
    assert "estimated_wait_seconds" not in body


# ---------------------------------------------------------------------------
# Mirror consistency: both surfaces, same real backlog, same estimate.
# ---------------------------------------------------------------------------


async def test_both_scan_creating_surfaces_compute_the_same_wait_estimate(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, redis_conn
) -> None:
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "1")
    monkeypatch.setenv("SCAN_TRIGGER_RATE_LIMIT", "100/minute")
    monkeypatch.setenv("QUEUE_BACKLOG_METRICS_ENABLED", "true")
    monkeypatch.setenv("SCAN_QUEUE_SLOT_COUNT", "2")
    monkeypatch.setenv("SCAN_AVERAGE_DURATION_SECONDS", "600")

    # This is a real, shared dev Redis (not truncated between test runs), so
    # the scan queue may not start at 0 - measure the baseline rather than
    # assume it, the same reason test_queue_backlog_alert_task.py's own real-
    # broker test asserts a lower bound instead of an exact literal. Pushed
    # probes are unique per test id to survive alongside any pre-existing
    # traffic.
    baseline = int(redis_conn.llen(_SCAN_QUEUE))
    probes = [f'{{"probe": "s7-mirror-{uuid.uuid4()}-{i}"}}' for i in range(5)]
    for probe in probes:
        redis_conn.lpush(_SCAN_QUEUE, probe)
    expected_wait_seconds = ((baseline + 5) // 2) * 600

    try:
        user_a, project_a1, project_a2 = await _seed_two_projects_one_team(client)
        headers_a = _bearer_for(user_a)
        ra1 = await client.post(
            f"/v1/projects/{project_a1.id}/scans", headers=headers_a, json={"kind": "source"}
        )
        assert ra1.status_code == 202, ra1.text
        scan_response = await client.post(
            f"/v1/projects/{project_a2.id}/scans", headers=headers_a, json={"kind": "source"}
        )

        user_b, project_b1, project_b2 = await _seed_two_projects_one_team(client)
        headers_b = _bearer_for(user_b)
        rb1 = await client.post(
            f"/v1/projects/{project_b1.id}/sbom-ingest", headers=headers_b, files=_sbom_part()
        )
        assert rb1.status_code == 202, rb1.text
        ingest_response = await client.post(
            f"/v1/projects/{project_b2.id}/sbom-ingest", headers=headers_b, files=_sbom_part()
        )
    finally:
        for probe in probes:
            redis_conn.lrem(_SCAN_QUEUE, 1, probe)

    assert scan_response.status_code == 429, scan_response.text
    assert ingest_response.status_code == 429, ingest_response.text

    scan_body = scan_response.json()
    ingest_body = ingest_response.json()

    assert scan_body["estimated_wait_seconds"] == expected_wait_seconds
    assert ingest_body["estimated_wait_seconds"] == expected_wait_seconds
    assert scan_body["estimated_wait_seconds"] == ingest_body["estimated_wait_seconds"]
    # The team-specific running-scan count still never rides along, on
    # either surface, even with the new field present.
    assert "running_scans" not in scan_body
    assert "running_scans" not in ingest_body
