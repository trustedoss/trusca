"""
API-key scope x target project, on every route that accepts a key and
resolves a project.

A key is an authorization boundary: a project-scoped key reaches its own
project, a team-scoped key its team's projects, an org-scoped key whatever its
issuer's memberships reach. The team check alone is not enough for a
project-scoped key, because the key's principal is narrowed to the whole
owning team; each route must also compare ``api_key_project_id``. The SBOM
conformance read did not, so a single-project CI key read sibling projects'
verdicts.

Routes (every caller of ``require_role_or_api_key`` and of the policy-gate
dual-auth dependency; the snapshot-anchor dependency resolves labels only and
sits in front of JWT-only routes):

- ``GET  /v1/projects/{id}/scans/{scan_id}/conformance``
- ``GET  /v1/scans/{scan_id}``
- ``GET  /v1/scans/{scan_id}/provenance``
- ``GET  /v1/projects/{id}/gate-result``
- ``GET  /v1/projects/{id}/gate-result/sarif``
- ``POST /v1/scans/{scan_id}/post-pr-comment`` (dry run)
- ``POST /v1/projects/{id}/scans``
- ``POST /v1/projects/{id}/sbom-ingest``

Every target project has a scan in progress, so a caller allowed through on
the two write routes gets the 409 for that state. A denied caller must get the
denial instead (hardening rule 1: permission before state). The denial is 404
on the project-addressed reads. The scan-addressed reads and the two write
routes keep their documented 403 for both a foreign team and a key bound to
another project (``test_scans_api.py``, ``test_sbom_ingest_api.py``); the
property under test is that the key is refused, and refused before state.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.security import CurrentUser
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
)

pytestmark = pytest.mark.integration

_SBOM = json.dumps(
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


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import services.sbom_ingest_service as ingest

    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setattr(ingest, "enqueue_scan", lambda scan: str(uuid.uuid4()))


@dataclass
class _Target:
    project_id: uuid.UUID
    scan_id: uuid.UUID


@dataclass
class _World:
    keys: dict[str, str]
    targets: dict[str, _Target]


@pytest.fixture
async def world(db_factory: async_sessionmaker[Any]) -> _World:
    """Team T holds projects A and B, team U holds C. The issuer administers T only."""
    from models import SbomConformance
    from services.api_key_service import issue_api_key

    async with db_factory() as session:
        org = await make_organization(session)
        team_t = await make_team(session, organization=org)
        team_u = await make_team(session, organization=org)
        issuer = await make_user(session)
        await make_membership(session, user=issuer, team=team_t, role="group_admin")

        targets: dict[str, _Target] = {}
        for label, team in (("A", team_t), ("B", team_t), ("C", team_u)):
            project = await make_project(session, team=team)
            done = await make_scan(session, project=project, kind="sbom", status="succeeded")
            session.add(
                SbomConformance(
                    scan_id=done.id,
                    project_id=project.id,
                    source_format="cyclonedx",
                    result="pass",
                    n_fail=0,
                    n_warn=0,
                    component_count=1,
                    purl_coverage_pct=100,
                    license_coverage_pct=100,
                    hash_coverage_pct=100,
                    checks=[],
                )
            )
            await session.commit()
            # The state a permitted caller trips on the write routes.
            await make_scan(session, project=project, status="queued")
            targets[label] = _Target(project_id=project.id, scan_id=done.id)

        member = CurrentUser(
            id=issuer.id,
            email=issuer.email,
            role="group_admin",
            team_ids=[team_t.id],
            team_roles={team_t.id: "group_admin"},
        )
        # Only a super admin may issue an org key. Issue it under a super-admin
        # principal carrying the issuer's id: the key then resolves to the
        # issuer's real (non-super) memberships, which is the case under test.
        as_super = CurrentUser(
            id=issuer.id, email=issuer.email, role="super_admin", is_superuser=True
        )
        keys: dict[str, str] = {}
        for scope, actor, team_id, project_id in (
            ("project", member, None, targets["A"].project_id),
            ("team", member, team_t.id, None),
            ("org", as_super, None, None),
        ):
            _row, plaintext = await issue_api_key(
                session,
                actor,
                name=f"matrix-{scope}",
                scope=scope,
                team_id=team_id,
                project_id=project_id,
                permission_breadth="read_write",
            )
            keys[scope] = plaintext
    return _World(keys=keys, targets=targets)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from main import app

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _call(client: AsyncClient, route: str, target: _Target, key: str) -> int:
    headers = {"Authorization": f"Bearer {key}"}
    pid, sid = target.project_id, target.scan_id
    if route == "conformance":
        resp = await client.get(f"/v1/projects/{pid}/scans/{sid}/conformance", headers=headers)
    elif route == "scan":
        resp = await client.get(f"/v1/scans/{sid}", headers=headers)
    elif route == "provenance":
        resp = await client.get(f"/v1/scans/{sid}/provenance", headers=headers)
    elif route == "gate_result":
        resp = await client.get(f"/v1/projects/{pid}/gate-result", headers=headers)
    elif route == "gate_sarif":
        resp = await client.get(f"/v1/projects/{pid}/gate-result/sarif", headers=headers)
    elif route == "pr_comment":
        resp = await client.post(
            f"/v1/scans/{sid}/post-pr-comment",
            headers=headers,
            json={"repo_full_name": "acme/app", "pr_number": 1, "dry_run": True},
        )
    elif route == "trigger":
        resp = await client.post(
            f"/v1/projects/{pid}/scans", headers=headers, json={"kind": "source"}
        )
    elif route == "sbom_ingest":
        resp = await client.post(
            f"/v1/projects/{pid}/sbom-ingest",
            headers=headers,
            files={"sbom": ("bom.cdx.json", _SBOM, "application/vnd.cyclonedx+json")},
        )
    else:  # pragma: no cover - parametrization guard
        raise AssertionError(route)
    return resp.status_code


# Which targets each key scope may reach. Team T = {A, B}; the issuer is not
# in team U, which holds C.
_REACH = {
    "project": {"A"},
    "team": {"A", "B"},
    "org": {"A", "B"},
}

# What a permitted caller gets, and what a refused one gets.
_ROUTES = {
    "conformance": (200, 404),
    # The scan read refuses a foreign team with 403 (``test_scans_api.py::
    # test_get_scan_other_team_returns_403``) and a key bound to another
    # project the same way; provenance reuses that resolver.
    "scan": (200, 403),
    "provenance": (200, 403),
    "gate_result": (200, 404),
    "gate_sarif": (200, 404),
    "pr_comment": (200, 404),
    "trigger": (409, 403),
    "sbom_ingest": (409, 403),
}


@pytest.mark.parametrize("route", sorted(_ROUTES))
@pytest.mark.parametrize("key_scope", ["project", "team", "org"])
async def test_key_scope_against_every_target(
    client: AsyncClient, world: _World, route: str, key_scope: str
) -> None:
    allowed_status, denied_status = _ROUTES[route]
    observed = {
        label: await _call(client, route, target, world.keys[key_scope])
        for label, target in world.targets.items()
    }
    expected = {
        label: allowed_status if label in _REACH[key_scope] else denied_status
        for label in world.targets
    }
    assert observed == expected
    for label, code in observed.items():
        if label not in _REACH[key_scope]:
            assert code != 409, f"{route}: state leaked to a refused key on {label}"
