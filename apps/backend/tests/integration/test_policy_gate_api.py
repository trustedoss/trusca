"""
Integration tests for the policy-gate HTTP surface — Phase 5 PR #17.

Endpoints under test:

  - GET  /v1/projects/{project_id}/gate-result
  - POST /v1/scans/{scan_id}/post-pr-comment

We drive the real ASGI app with httpx and assert the wire format. The
gate-result endpoint accepts JWT tokens (via ``create_access_token``); the
API-key code path is covered by the unit tests on ``core/api_key_auth.py``.

RFC 7807 contract: every 4xx response carries
``Content-Type: application/problem+json``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    unique_suffix,
)

PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


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


async def _seed_team_and_user(client: AsyncClient, *, role: str = "developer"):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role=role)
    return org, team, user


async def _seed_project(client: AsyncClient, *, team_id: uuid.UUID):
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Team

        team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one()
        project = await make_project(session, team=team)
        project_id = project.id
    return project_id


async def _seed_succeeded_scan(
    client: AsyncClient,
    *,
    project_id: uuid.UUID,
    ref: str | None = None,
    created_at: datetime | None = None,
    critical: bool = False,
    status: str = "succeeded",
) -> uuid.UUID:
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Project

        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one()
        scan = await make_scan(
            session,
            project=project,
            status=status,
            ref=ref,
            created_at=created_at,
        )
        scan_id = scan.id
        if critical:
            await _seed_critical_finding(session, scan_id=scan_id)
    return scan_id


async def _seed_critical_finding(session, *, scan_id: uuid.UUID) -> None:
    """Attach one open critical CVE so the gate verdict on this scan is ``fail``.

    Mirrors the fixture in ``test_action_queue_gate_parity.py`` — duplicated
    rather than imported because cross-importing test modules couples their
    collection order.
    """
    from models import (
        Component,
        ComponentVersion,
        ScanComponent,
        Vulnerability,
        VulnerabilityFinding,
    )

    suffix = unique_suffix()
    purl = f"pkg:npm/pkg-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"pkg-{suffix}")
    session.add(component)
    await session.commit()
    await session.refresh(component)

    cv = ComponentVersion(
        component_id=component.id,
        version="1.0.0",
        purl_with_version=f"{purl}@1.0.0",
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)

    session.add(
        ScanComponent(scan_id=scan_id, component_version_id=cv.id, direct=True, raw_data={})
    )
    vuln = Vulnerability(
        external_id=f"CVE-2024-{suffix}",
        source="NVD",
        severity="critical",
        summary="ref-anchor fixture",
    )
    session.add(vuln)
    await session.commit()
    await session.refresh(vuln)

    session.add(
        VulnerabilityFinding(
            scan_id=scan_id,
            component_version_id=cv.id,
            vulnerability_id=vuln.id,
            status="new",
        )
    )
    await session.commit()


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/gate-result
# ---------------------------------------------------------------------------


async def test_gate_result_unauthenticated_returns_401_problem(client) -> None:
    project_id = uuid.uuid4()
    response = await client.get(f"/v1/projects/{project_id}/gate-result")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_gate_result_member_with_no_scan_returns_pass(client) -> None:
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=_bearer_for(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == "pass"
    assert body["reason"] is None
    assert body["scan_id"] is None
    assert body["critical_cve_count"] == 0
    assert body["forbidden_license_count"] == 0
    assert body["project_id"] == str(project_id)
    assert "evaluated_at" in body


async def test_gate_result_exposes_reachability_fields(client) -> None:
    """v2.3 r2: the gate-result body carries the reachability surfacing fields,
    defaulted on a project with no scan / no reachable findings."""
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=_bearer_for(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reachable_critical_cve_count"] == 0
    assert body["reachable_gate_enforced"] is False


async def test_gate_result_member_with_succeeded_scan_returns_pass_with_scan_id(
    client,
) -> None:
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=_bearer_for(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == "pass"
    assert body["scan_id"] == str(scan_id)


# ---------------------------------------------------------------------------
# The ?ref= anchor — a CI job's verdict must come from the branch it scanned.
#
# These four cover the contract the three shipped CI clients depend on. Before
# they existed, no test anywhere passed ?ref= at all, and all three clients
# omitted it: a pull_request build polled its own pr-<n> scan to succeeded and
# was then judged by the main line, because the resolver prefers the main line
# over recency.
# ---------------------------------------------------------------------------


async def _seed_two_branch_scans(client, *, project_id: uuid.UUID):
    """A failing main-line scan and a NEWER clean ``pr-7`` scan.

    Newer on purpose: recency alone would pick the PR scan, so a test that
    passes here can only be passing because of the main-line preference and
    the ref anchor, not by accident of ordering.
    """
    now = datetime.now(tz=UTC)
    main_scan = await _seed_succeeded_scan(
        client,
        project_id=project_id,
        ref="main",
        created_at=now - timedelta(hours=1),
        critical=True,
    )
    pr_scan = await _seed_succeeded_scan(
        client,
        project_id=project_id,
        ref="pr-7",
        created_at=now,
    )
    return main_scan, pr_scan


async def test_gate_result_without_ref_reads_the_main_line(client) -> None:
    """The unchanged default: no ref → main line, even when it is not newest."""
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    main_scan, _ = await _seed_two_branch_scans(client, project_id=project_id)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=_bearer_for(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scan_id"] == str(main_scan)
    assert body["gate"] == "fail"


async def test_gate_result_ref_anchors_verdict_to_that_branch(client) -> None:
    """?ref=pr-7 → the PR's own clean scan, NOT the main line's failing one."""
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    _, pr_scan = await _seed_two_branch_scans(client, project_id=project_id)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=_bearer_for(user),
        params={"ref": "pr-7"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scan_id"] == str(pr_scan)
    assert body["gate"] == "pass"
    assert body["critical_cve_count"] == 0


async def test_gate_result_ref_accepts_the_long_form_ci_actually_sends(client) -> None:
    """``refs/pull/7/merge`` must resolve the same as ``pr-7``.

    The GitHub action forwards ``github.ref`` verbatim to both the trigger and
    the gate query and relies on the portal normalizing both ends identically.
    A regression in that normalization would silently return the action to
    main-line verdicts, so assert the long form here rather than only the key.
    """
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    _, pr_scan = await _seed_two_branch_scans(client, project_id=project_id)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=_bearer_for(user),
        params={"ref": "refs/pull/7/merge"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["scan_id"] == str(pr_scan)


async def test_gate_result_ref_with_no_succeeded_scan_does_not_borrow_another_branch(
    client,
) -> None:
    """A named branch yields that branch or nothing — never a neighbour's findings."""
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    await _seed_two_branch_scans(client, project_id=project_id)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=_bearer_for(user),
        params={"ref": "release/9.9"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scan_id"] is None
    assert body["gate"] == "pass"
    assert body["critical_cve_count"] == 0


async def test_gate_result_non_team_member_returns_404_existence_hide(client) -> None:
    """Cross-team callers must NOT learn whether a project exists."""
    _, team_a, _ = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team_a.id)

    _, _, outsider = await _seed_team_and_user(client)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=_bearer_for(outsider),
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_gate_result_unknown_project_returns_404(client) -> None:
    _, _, user = await _seed_team_and_user(client)
    response = await client.get(
        f"/v1/projects/{uuid.uuid4()}/gate-result",
        headers=_bearer_for(user),
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# POST /v1/scans/{scan_id}/post-pr-comment
# ---------------------------------------------------------------------------


async def test_post_pr_comment_dry_run_returns_body_preview(client) -> None:
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)

    response = await client.post(
        f"/v1/scans/{scan_id}/post-pr-comment",
        headers=_bearer_for(user),
        json={
            "repo_full_name": "trustedoss/portal",
            "pr_number": 42,
            "dry_run": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "dry_run"
    assert body["comment_id"] is None
    assert body["comment_url"] is None
    assert "TRUSCA" in body["body_preview"]
    assert body["gate"] in ("pass", "fail")


async def test_post_pr_comment_reports_the_scan_in_the_url(client) -> None:
    """The comment describes the caller's scan, not the project's main line.

    CI posts this right after polling its own scan to succeeded. Evaluating
    the project instead put main's verdict, counts and upgrade advice into a
    pull request's comment — under that PR's own scan id.
    """
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    _, pr_scan = await _seed_two_branch_scans(client, project_id=project_id)

    response = await client.post(
        f"/v1/scans/{pr_scan}/post-pr-comment",
        headers=_bearer_for(user),
        json={"repo_full_name": "trustedoss/portal", "pr_number": 7, "dry_run": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == "pass", "the PR's clean scan was judged by the main line"


async def test_post_pr_comment_non_succeeded_scan_keeps_the_latest_verdict(
    client,
) -> None:
    """A queued scan has no snapshot, so the project-wide verdict still applies.

    Pinning must not degrade these callers to a no-signal pass — that would
    turn "we cannot tell yet" into a green comment.
    """
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    await _seed_two_branch_scans(client, project_id=project_id)
    queued = await _seed_succeeded_scan(client, project_id=project_id, ref="pr-8", status="queued")

    response = await client.post(
        f"/v1/scans/{queued}/post-pr-comment",
        headers=_bearer_for(user),
        json={"repo_full_name": "trustedoss/portal", "pr_number": 8, "dry_run": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["gate"] == "fail"


async def test_post_pr_comment_unknown_scan_returns_404(client) -> None:
    _, _, user = await _seed_team_and_user(client)
    response = await client.post(
        f"/v1/scans/{uuid.uuid4()}/post-pr-comment",
        headers=_bearer_for(user),
        json={
            "repo_full_name": "trustedoss/portal",
            "pr_number": 1,
            "dry_run": True,
        },
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_post_pr_comment_outsider_returns_404_existence_hide(client) -> None:
    _, team_a, _ = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team_a.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)

    _, _, outsider = await _seed_team_and_user(client)

    response = await client.post(
        f"/v1/scans/{scan_id}/post-pr-comment",
        headers=_bearer_for(outsider),
        json={
            "repo_full_name": "trustedoss/portal",
            "pr_number": 1,
            "dry_run": True,
        },
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_post_pr_comment_invalid_repo_slug_returns_422_problem(client) -> None:
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)

    response = await client.post(
        f"/v1/scans/{scan_id}/post-pr-comment",
        headers=_bearer_for(user),
        json={
            # Path-traversal attempt — must be rejected by the schema.
            "repo_full_name": "../etc/passwd",
            "pr_number": 1,
            "dry_run": True,
        },
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_post_pr_comment_unauthenticated_returns_401(client) -> None:
    response = await client.post(
        f"/v1/scans/{uuid.uuid4()}/post-pr-comment",
        json={
            "repo_full_name": "trustedoss/portal",
            "pr_number": 1,
            "dry_run": True,
        },
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# M-2 — project-scoped API key boundary on the gate surface
# ---------------------------------------------------------------------------


async def test_load_project_for_gate_blocks_sibling_project_for_scoped_key(
    client: AsyncClient,
) -> None:
    """M-2 / security review finding: a project-scoped key principal must not
    reach a SIBLING project's gate data via _load_project_for_gate — the gate
    surface (gate-result + post-pr-comment) accepts API keys, so the team gate
    alone left the same cross-project leak the scan endpoints had.

    Existence-hide: the boundary surfaces the same 404 a cross-team caller sees.
    """
    import dataclasses

    from api.v1.policy_gate import _load_project_for_gate
    from services.project_service import ProjectNotFound
    from tests._helpers import principal_for

    _, team, user = await _seed_team_and_user(client)
    scoped_project = await _seed_project(client, team_id=team.id)
    sibling = await _seed_project(client, team_id=team.id)

    factory = await _factory(client)
    actor = dataclasses.replace(
        principal_for(user, team_ids=[team.id], role="developer"),
        api_key_project_id=scoped_project,
    )

    async with factory() as session:
        # Its own project resolves.
        own = await _load_project_for_gate(session, scoped_project, actor)
        assert own.id == scoped_project

        # The sibling project of the SAME team is existence-hidden.
        with pytest.raises(ProjectNotFound):
            await _load_project_for_gate(session, sibling, actor)

    # A JWT principal (api_key_project_id=None) is unaffected by the boundary.
    jwt_actor = principal_for(user, team_ids=[team.id], role="developer")
    async with factory() as session:
        resolved = await _load_project_for_gate(session, sibling, jwt_actor)
        assert resolved.id == sibling


# ---------------------------------------------------------------------------
# Audit — the PR comment is an external side effect no DB row records, so the
# endpoint writes the AuditLog row explicitly (automatic listener can't see it)
# ---------------------------------------------------------------------------


async def test_post_pr_comment_posted_writes_audit_row(client, monkeypatch) -> None:
    from sqlalchemy import select

    from api.v1 import policy_gate as pg
    from models import AuditLog
    from services.sca_comment import PostedComment

    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)

    async def _fake_post(**_kwargs):
        return PostedComment(
            status="posted",
            comment_id=987654,
            comment_url="https://github.com/trustedoss/portal/pull/42#issuecomment-987654",
            body_preview="TRUSCA gate summary",
        )

    monkeypatch.setattr(pg, "post_pr_comment", _fake_post)
    monkeypatch.setattr(pg, "_resolve_github_token", lambda: "ghp_test_token")

    response = await client.post(
        f"/v1/scans/{scan_id}/post-pr-comment",
        headers=_bearer_for(user),
        json={
            "repo_full_name": "trustedoss/portal",
            "pr_number": 42,
            "dry_run": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "posted"

    factory = await _factory(client)
    async with factory() as session:
        row = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.action == "sca_pr_comment.posted",
                        AuditLog.target_id == str(scan_id),
                    )
                    .order_by(AuditLog.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
    assert row is not None, "posting a PR comment must leave an audit row"
    assert row.target_table == "scans"
    assert row.actor_user_id == user.id
    assert row.team_id == team.id
    assert row.diff["repo_full_name"] == "trustedoss/portal"
    assert row.diff["pr_number"] == 42
    assert row.diff["comment_id"] == 987654
    # The GitHub token must never round-trip into the audit trail.
    assert "ghp_test_token" not in str(row.diff)


async def test_post_pr_comment_dry_run_writes_no_audit_row(client) -> None:
    from sqlalchemy import select

    from models import AuditLog

    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)

    response = await client.post(
        f"/v1/scans/{scan_id}/post-pr-comment",
        headers=_bearer_for(user),
        json={
            "repo_full_name": "trustedoss/portal",
            "pr_number": 7,
            "dry_run": True,
        },
    )
    assert response.status_code == 200, response.text

    factory = await _factory(client)
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action.like("sca_pr_comment.%"),
                        AuditLog.target_id == str(scan_id),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert rows == [], "dry_run has no side effect and must not be audited"


# ---------------------------------------------------------------------------
# An empty scan passes the gate, and the payload has to say why (ER1)
# ---------------------------------------------------------------------------
#
# Nothing on the scan path counts components, so a tree the scanner cannot
# parse produces an empty SBOM, exits 0, and lands as `succeeded`. Every count
# the gate reads is then 0 and the verdict is `pass`, indistinguishable in
# the response body, from a project that was scanned properly and is clean.
# The verdict is deliberately left as `pass`: an empty result is the correct
# answer for an unsupported build system, and failing there would break
# pipelines that are working. What changes is that the payload carries which
# kind of result it was, so the CI client can say so.


async def _set_component_outcome(client: AsyncClient, *, scan_id: uuid.UUID, outcome: str) -> None:
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Scan

        scan = (await session.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()
        merged = dict(scan.scan_metadata or {})
        merged["component_outcome"] = outcome
        scan.scan_metadata = merged
        await session.commit()


async def test_gate_result_reports_an_empty_scan_alongside_its_pass(
    client,
) -> None:
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)
    await _set_component_outcome(client, scan_id=scan_id, outcome="empty_no_manifests")

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=_bearer_for(user),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    # Still a pass: an unreadable build system is not a policy violation.
    assert body["gate"] == "pass"
    assert body["critical_cve_count"] == 0
    # But the reason every count is 0 now travels with the verdict.
    assert body["component_outcome"] == "empty_no_manifests"


async def test_gate_result_separates_an_empty_scan_from_a_failed_one(
    client,
) -> None:
    """The two empties need different actions, so they are different values."""
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)
    await _set_component_outcome(client, scan_id=scan_id, outcome="empty_with_manifests")

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=_bearer_for(user),
    )

    assert response.status_code == 200, response.text
    assert response.json()["component_outcome"] == "empty_with_manifests"


async def test_gate_result_reports_no_outcome_for_a_scan_predating_the_capture(
    client,
) -> None:
    """Unknown must read as unknown, not as either answer.

    Every scan that ran before this field existed has no value for it, and
    defaulting those to "found components" would tell the CI client a scan was
    fine on no evidence.
    """
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    await _seed_succeeded_scan(client, project_id=project_id)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=_bearer_for(user),
    )

    assert response.status_code == 200, response.text
    assert response.json()["component_outcome"] is None


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/gate-result/sarif  (ER29)
# ---------------------------------------------------------------------------

SARIF_JSON = "application/sarif+json"


async def test_gate_sarif_unauthenticated_returns_401_problem(client) -> None:
    project_id = uuid.uuid4()
    response = await client.get(f"/v1/projects/{project_id}/gate-result/sarif")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_gate_sarif_without_a_scan_is_valid_and_empty(client) -> None:
    """A project with no scan must still upload cleanly.

    An empty run is how code scanning learns that previously-reported alerts
    are gone. A 404 here would either fail the CI step or leave stale alerts
    standing on the branch forever.
    """
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result/sarif",
        headers=_bearer_for(user),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(SARIF_JSON)
    body = response.json()
    assert body["version"] == "2.1.0"
    assert body["runs"][0]["results"] == []
    assert body["runs"][0]["tool"]["driver"]["name"] == "TRUSCA"


async def test_gate_sarif_reports_an_open_finding(client) -> None:
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    await _seed_succeeded_scan(client, project_id=project_id, critical=True)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result/sarif",
        headers=_bearer_for(user),
    )
    assert response.status_code == 200, response.text
    run = response.json()["runs"][0]

    assert len(run["results"]) == 1
    result = run["results"][0]
    rules = run["tool"]["driver"]["rules"]

    # A critical CVE must arrive as `error`: SARIF has no "critical", and
    # anything softer buries it among mediums in the code-scanning list.
    assert result["level"] == "error"
    assert result["ruleId"].startswith("CVE-")
    # ruleIndex must actually address the rule, or GitHub renders the wrong one.
    assert rules[result["ruleIndex"]]["id"] == result["ruleId"]
    assert rules[result["ruleIndex"]]["properties"]["security-severity"] == "9.5"

    # Every result needs a location or the upload is rejected.
    location = result["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"]
    assert location["region"]["startLine"] == 1
    assert result["partialFingerprints"]["truscaFindingId"]


async def test_gate_sarif_omits_a_suppressed_finding(client) -> None:
    """The one place SARIF and the gate deliberately disagree.

    The gate counts a suppressed finding, because a local decision to look away
    does not answer "may this build proceed". Code scanning must not re-raise
    it, because a scanner that re-raises triaged findings teaches reviewers to
    ignore it.
    """
    from sqlalchemy import select, update

    from models import VulnerabilityFinding

    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id, critical=True)

    factory = await _factory(client)
    async with factory() as session:
        await session.execute(
            update(VulnerabilityFinding)
            .where(VulnerabilityFinding.scan_id == scan_id)
            .values(status="suppressed")
        )
        await session.commit()
        remaining = (
            await session.execute(
                select(VulnerabilityFinding).where(VulnerabilityFinding.scan_id == scan_id)
            )
        ).scalars().all()
        assert len(remaining) == 1, "fixture should still have the finding, just suppressed"

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result/sarif",
        headers=_bearer_for(user),
    )
    assert response.status_code == 200, response.text
    assert response.json()["runs"][0]["results"] == []

    # ... while the gate still counts it. Asserted here rather than in a
    # separate test so the divergence is visible in one place.
    gate = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=_bearer_for(user),
    )
    assert gate.json()["critical_cve_count"] == 1


async def test_gate_sarif_hides_another_teams_project(client) -> None:
    """Same existence-hiding as the verdict: this is the same data."""
    _, team, _owner = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    _, _other_team, outsider = await _seed_team_and_user(client)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result/sarif",
        headers=_bearer_for(outsider),
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# KEV and end-of-life gate axes (ER29)
#
# The point of these axes is what they say when they could NOT judge. A count
# of 0 is produced both by "nothing is exploited" and by "the catalog was never
# synced", and only the outcome field separates them. Every test below pins
# which of the two a given deployment state produces.
# ---------------------------------------------------------------------------


async def _seed_kev_finding(
    session,
    *,
    scan_id: uuid.UUID,
    kev: bool,
    vuln_created_at: datetime | None = None,
) -> None:
    from models import (
        Component,
        ComponentVersion,
        ScanComponent,
        Vulnerability,
        VulnerabilityFinding,
    )

    suffix = unique_suffix()
    purl = f"pkg:npm/kev-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"kev-{suffix}")
    session.add(component)
    await session.commit()
    await session.refresh(component)

    cv = ComponentVersion(
        component_id=component.id,
        version="1.0.0",
        purl_with_version=f"{purl}@1.0.0",
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)

    session.add(
        ScanComponent(scan_id=scan_id, component_version_id=cv.id, direct=True, raw_data={})
    )
    vuln = Vulnerability(
        external_id=f"CVE-2025-{suffix}",
        source="NVD",
        severity="high",
        summary="kev fixture",
        kev=kev,
    )
    if vuln_created_at is not None:
        vuln.created_at = vuln_created_at
    session.add(vuln)
    await session.commit()
    await session.refresh(vuln)

    session.add(
        VulnerabilityFinding(
            scan_id=scan_id,
            component_version_id=cv.id,
            vulnerability_id=vuln.id,
            status="new",
        )
    )
    await session.commit()


async def _set_kev_last_synced(session, *, when: datetime | None) -> None:
    """Put the deployment's KEV sync state where the test needs it."""
    from sqlalchemy import delete

    from models import KevSyncState

    await session.execute(delete(KevSyncState))
    if when is not None:
        session.add(KevSyncState(id=True, last_synced_at=when, last_result="synced"))
    await session.commit()


async def test_kev_axis_is_off_by_default(client, monkeypatch) -> None:
    """No deployment that has not opted in changes behaviour."""
    monkeypatch.delenv("GATE_KEV_ENABLED", raising=False)
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)
    factory = await _factory(client)
    async with factory() as session:
        await _set_kev_last_synced(session, when=datetime.now(UTC))
        await _seed_kev_finding(session, scan_id=scan_id, kev=True)

    body = (
        await client.get(
            f"/v1/projects/{project_id}/gate-result", headers=_bearer_for(user)
        )
    ).json()
    assert body["kev_gate_enabled"] is False
    assert body["kev_gate_count"] == 0
    assert body["kev_outcome"] == "not_configured"
    assert body["gate"] == "pass"


async def test_a_known_exploited_cve_fails_the_build(client, monkeypatch) -> None:
    monkeypatch.setenv("GATE_KEV_ENABLED", "true")
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)
    factory = await _factory(client)
    async with factory() as session:
        await _set_kev_last_synced(session, when=datetime.now(UTC) + timedelta(hours=1))
        await _seed_kev_finding(session, scan_id=scan_id, kev=True)

    body = (
        await client.get(
            f"/v1/projects/{project_id}/gate-result", headers=_bearer_for(user)
        )
    ).json()
    assert body["kev_gate_count"] == 1
    assert body["kev_outcome"] == "evaluated"
    assert body["gate"] == "fail"
    assert "known-exploited" in body["reason"]


async def test_an_unsynced_kev_catalog_is_not_reported_as_clean(
    client, monkeypatch
) -> None:
    """The failure this axis exists to prevent.

    With no sync, every `kev` flag is the column default. The count is 0 and
    the build passes, and without `kev_outcome` that pass is indistinguishable
    from a real all-clear.
    """
    monkeypatch.setenv("GATE_KEV_ENABLED", "true")
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)
    factory = await _factory(client)
    async with factory() as session:
        await _set_kev_last_synced(session, when=None)
        await _seed_kev_finding(session, scan_id=scan_id, kev=False)

    body = (
        await client.get(
            f"/v1/projects/{project_id}/gate-result", headers=_bearer_for(user)
        )
    ).json()
    assert body["kev_gate_count"] == 0
    assert body["kev_outcome"] == "no_data"
    # Default policy still passes; the caller is told not to read it as clean.
    assert body["gate"] == "pass"


async def test_unsynced_kev_blocks_when_the_operator_asks(client, monkeypatch) -> None:
    monkeypatch.setenv("GATE_KEV_ENABLED", "true")
    monkeypatch.setenv("GATE_KEV_ON_MISSING_DATA", "block")
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)
    factory = await _factory(client)
    async with factory() as session:
        await _set_kev_last_synced(session, when=None)
        await _seed_kev_finding(session, scan_id=scan_id, kev=False)

    body = (
        await client.get(
            f"/v1/projects/{project_id}/gate-result", headers=_bearer_for(user)
        )
    ).json()
    assert body["kev_outcome"] == "no_data"
    assert body["gate"] == "fail"
    assert "never been synced" in body["reason"]


async def test_a_finding_newer_than_the_last_sync_is_partial(
    client, monkeypatch
) -> None:
    """The gap that is easy to miss.

    The sync runs daily. A CVE discovered this morning carries `kev = false`
    because nothing has reconciled it yet, so the axis must not claim it
    checked. `partial` never blocks, but it must not read as `evaluated`.
    """
    monkeypatch.setenv("GATE_KEV_ENABLED", "true")
    monkeypatch.setenv("GATE_KEV_ON_MISSING_DATA", "block")
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)
    factory = await _factory(client)
    async with factory() as session:
        synced_at = datetime.now(UTC) - timedelta(days=1)
        await _set_kev_last_synced(session, when=synced_at)
        # One CVE the sync has already reconciled (rows are shared across
        # scans, so most findings reuse a long-existing vulnerability row) ...
        await _seed_kev_finding(
            session,
            scan_id=scan_id,
            kev=False,
            vuln_created_at=synced_at - timedelta(days=30),
        )
        # ... and one discovered since, whose `kev = false` is the column
        # default rather than an answer.
        await _seed_kev_finding(
            session,
            scan_id=scan_id,
            kev=False,
            vuln_created_at=datetime.now(UTC),
        )

    body = (
        await client.get(
            f"/v1/projects/{project_id}/gate-result", headers=_bearer_for(user)
        )
    ).json()
    assert body["kev_outcome"] == "partial"
    # Even under `block`: partial is normal, and an option that fires on a
    # normal state is one nobody can leave switched on.
    assert body["gate"] == "pass"


async def _seed_component_with_eol(
    session,
    *,
    scan_id: uuid.UUID,
    eol_state: str | None,
    evaluated: bool,
) -> None:
    """One scanned component with a given lifecycle answer.

    ``evaluated=False`` leaves ``eol_evaluated_at`` NULL, which is a component
    the lifecycle catalog never looked at. ``eol_state='unknown'`` is the other
    half of "no answer": looked at, but the catalog has no entry for it.
    """
    from models import Component, ComponentVersion, ScanComponent

    suffix = unique_suffix()
    purl = f"pkg:npm/eol-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"eol-{suffix}")
    session.add(component)
    await session.commit()
    await session.refresh(component)

    cv = ComponentVersion(
        component_id=component.id,
        version="1.0.0",
        purl_with_version=f"{purl}@1.0.0",
        eol_state=eol_state,
        eol_evaluated_at=datetime.now(UTC) if evaluated else None,
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)

    session.add(
        ScanComponent(scan_id=scan_id, component_version_id=cv.id, direct=True, raw_data={})
    )
    await session.commit()


async def test_eol_axis_is_off_by_default(client, monkeypatch) -> None:
    monkeypatch.delenv("GATE_EOL_ENABLED", raising=False)
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)
    factory = await _factory(client)
    async with factory() as session:
        await _seed_component_with_eol(
            session, scan_id=scan_id, eol_state="eol", evaluated=True
        )

    body = (
        await client.get(
            f"/v1/projects/{project_id}/gate-result", headers=_bearer_for(user)
        )
    ).json()
    assert body["eol_gate_enabled"] is False
    assert body["eol_gate_count"] == 0
    assert body["eol_outcome"] == "not_configured"
    assert body["gate"] == "pass"


async def test_an_end_of_life_component_fails_the_build(client, monkeypatch) -> None:
    monkeypatch.setenv("GATE_EOL_ENABLED", "true")
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)
    factory = await _factory(client)
    async with factory() as session:
        await _seed_component_with_eol(
            session, scan_id=scan_id, eol_state="eol", evaluated=True
        )

    body = (
        await client.get(
            f"/v1/projects/{project_id}/gate-result", headers=_bearer_for(user)
        )
    ).json()
    assert body["eol_gate_count"] == 1
    assert body["eol_outcome"] == "evaluated"
    assert body["gate"] == "fail"
    assert "past end of life" in body["reason"]


async def test_a_never_evaluated_catalog_is_not_reported_as_clean(
    client, monkeypatch
) -> None:
    """Every component unevaluated: the count is 0 because nothing was checked."""
    monkeypatch.setenv("GATE_EOL_ENABLED", "true")
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)
    factory = await _factory(client)
    async with factory() as session:
        await _seed_component_with_eol(
            session, scan_id=scan_id, eol_state=None, evaluated=False
        )

    body = (
        await client.get(
            f"/v1/projects/{project_id}/gate-result", headers=_bearer_for(user)
        )
    ).json()
    assert body["eol_gate_count"] == 0
    assert body["eol_outcome"] == "no_data"
    assert body["gate"] == "pass"


async def test_uncovered_components_make_the_eol_axis_partial(
    client, monkeypatch
) -> None:
    """The ordinary state, and it must never block.

    The lifecycle catalog covers a curated set of runtimes and frameworks, so
    most application dependencies resolve to `unknown`. If `block` fired here
    it would fire on nearly every build, and an option like that gets switched
    off within the week.
    """
    monkeypatch.setenv("GATE_EOL_ENABLED", "true")
    monkeypatch.setenv("GATE_EOL_ON_MISSING_DATA", "block")
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)
    factory = await _factory(client)
    async with factory() as session:
        await _seed_component_with_eol(
            session, scan_id=scan_id, eol_state="supported", evaluated=True
        )
        # Checked, but the catalog has no entry for this product.
        await _seed_component_with_eol(
            session, scan_id=scan_id, eol_state="unknown", evaluated=True
        )

    body = (
        await client.get(
            f"/v1/projects/{project_id}/gate-result", headers=_bearer_for(user)
        )
    ).json()
    assert body["eol_outcome"] == "partial"
    assert body["gate"] == "pass"


async def test_unevaluated_eol_blocks_when_the_operator_asks(
    client, monkeypatch
) -> None:
    monkeypatch.setenv("GATE_EOL_ENABLED", "true")
    monkeypatch.setenv("GATE_EOL_ON_MISSING_DATA", "block")
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)
    factory = await _factory(client)
    async with factory() as session:
        await _seed_component_with_eol(
            session, scan_id=scan_id, eol_state=None, evaluated=False
        )

    body = (
        await client.get(
            f"/v1/projects/{project_id}/gate-result", headers=_bearer_for(user)
        )
    ).json()
    assert body["eol_outcome"] == "no_data"
    assert body["gate"] == "fail"
    assert "end-of-life gate could not be evaluated" in body["reason"]
