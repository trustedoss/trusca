"""
Integration tests for POST /v1/webhooks/gitlab — Phase 5 PR #16.

Public endpoint (no JWT). Authentication is the X-Gitlab-Token header
constant-time-compared to the project's ``webhook_secret``. Idempotency
key resolution prefers ``X-Gitlab-Webhook-UUID`` and falls back to the
payload's ``checkout_sha`` (push) or ``object_attributes.id`` (merge
request) — matching ``services.webhook_service._extract_delivery_id``.

What this suite pins:
  - Valid token + Push Hook → 200 enqueued, exactly one Celery dispatch.
  - Wrong / missing token → 401 / 400, no dispatch.
  - Duplicate (provider, delivery_id) → 200 'duplicate', no second dispatch.
  - Adversarial token contents (control bytes, oversized) → 401 / 400.
  - Non-scan events (Issue Hook, Note Hook, etc.) → 200 'ignored'.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from models import Project, WebhookDelivery
from tests._helpers import (
    make_organization,
    make_project,
    make_team,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = BACKEND_ROOT / "tests" / "fixtures" / "webhooks"
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip GitLab webhook tests")
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
            f"alembic upgrade head failed; GitLab webhook tests cannot run\n"
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


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


@pytest.fixture
def captured_dispatches(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The actual call now lives in ``services.scan_service.enqueue_scan``

    (via ``enqueue_system_triggered_scan_async``, promoted there for the N18
    scheduled-scan poller to reuse the same guard-and-insert sequence).
    """
    calls: list[str] = []

    def _fake(scan):  # type: ignore[no-untyped-def]
        calls.append(str(scan.id))
        return f"celery-task-{secrets.token_hex(4)}"

    monkeypatch.setattr(
        "services.scan_service.enqueue_scan",
        _fake,
        raising=False,
    )
    return calls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_gitlab_project(
    client: AsyncClient,
    *,
    secret: str | None = None,
    git_url: str | None = None,
) -> tuple[Project, str]:
    """Create a project with webhook_provider='gitlab' and a fresh secret.

    See the matching docstring in test_webhooks_github.py for why
    ``git_url`` defaults to a per-call unique URL (DB rows persist across
    test sessions; ``_find_project_by_git_url`` would otherwise pick a stale
    project with a different ``webhook_secret``).
    """
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        project = await make_project(session, team=team)
        project.git_url = git_url or f"https://gitlab.com/acme/widgets-{unique_suffix()}"
        project.webhook_secret = secret or secrets.token_urlsafe(32)
        project.webhook_provider = "gitlab"
        await session.commit()
        await session.refresh(project)
        return project, project.webhook_secret


def _push_payload(repo_url: str | None = "https://gitlab.com/acme/widgets") -> dict[str, object]:
    safe_url = repo_url or "https://gitlab.com/unknown/unknown"
    return {
        "object_kind": "push",
        "ref": "refs/heads/main",
        "checkout_sha": secrets.token_hex(20),
        "project": {
            "git_http_url": safe_url,
            "git_ssh_url": safe_url.replace("https://", "git@").replace("/", ":", 1),
            "web_url": safe_url,
        },
    }


def _mr_payload(
    repo_url: str | None,
    *,
    action: str,
    head_sha: str | None = None,
) -> dict[str, Any]:
    """A merge-request payload shaped like the one GitLab actually sends.

    The fields under test — ``object_attributes.action``, ``.iid``,
    ``.last_commit.id`` — exist only on this event, so the push fixture above
    could not exercise any of them.

    The head SHA defaults to a fresh value because the header-less delivery id
    is derived from it, and ``webhook_deliveries`` is unique on
    ``(source, delivery_id)`` across projects. Reusing the fixture's own SHA
    would make the first call of the day pass and every later one report
    ``duplicate`` against a delivery from a previous run.
    """
    safe_url = repo_url or "https://gitlab.com/unknown/unknown"
    fixture = FIXTURES / "gitlab_merge_request_update.json"
    payload: dict[str, Any] = json.loads(fixture.read_text(encoding="utf-8"))
    payload["object_attributes"]["action"] = action
    payload["object_attributes"]["last_commit"]["id"] = head_sha or secrets.token_hex(20)
    payload["project"]["git_http_url"] = safe_url
    payload["project"]["web_url"] = safe_url
    return payload


async def _post_mr(
    client: AsyncClient,
    project: Project,
    secret: str,
    *,
    action: str,
    delivery_uuid: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST a merge-request hook. Omit *delivery_uuid* to emulate old GitLab."""
    body = json.dumps(payload or _mr_payload(project.git_url, action=action)).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Gitlab-Token": secret,
        "X-Gitlab-Event": "Merge Request Hook",
    }
    if delivery_uuid is not None:
        headers["X-Gitlab-Webhook-UUID"] = delivery_uuid
    response = await client.post("/v1/webhooks/gitlab", content=body, headers=headers)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


@pytest.mark.parametrize("action", ["open", "reopen", "update"])
async def test_dependency_changing_mr_actions_scan(
    client: AsyncClient, captured_dispatches: list[str], action: str
) -> None:
    project, secret = await _make_gitlab_project(client)
    result = await _post_mr(
        client, project, secret, action=action, delivery_uuid=str(uuid.uuid4())
    )
    assert result["status"] == "enqueued"
    assert len(captured_dispatches) == 1


@pytest.mark.parametrize("action", ["close", "merge", "approved"])
async def test_non_dependency_mr_actions_do_not_scan(
    client: AsyncClient, captured_dispatches: list[str], action: str
) -> None:
    project, secret = await _make_gitlab_project(client)
    result = await _post_mr(
        client, project, secret, action=action, delivery_uuid=str(uuid.uuid4())
    )
    assert result["status"] == "ignored"
    assert captured_dispatches == []


async def test_team_at_capacity_skips_without_erroring(
    client: AsyncClient,
    captured_dispatches: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The GitLab path honours the same capacity guards as its GitHub twin."""
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "1")

    project, secret = await _make_gitlab_project(client)
    factory = await _factory(client)
    async with factory() as session:
        from tests._helpers import make_scan

        stored = (
            await session.execute(select(Project).where(Project.id == project.id))
        ).scalar_one()
        await make_scan(session, project=stored, status="running", ref="release/1.x")

    result = await _post_mr(
        client, project, secret, action="update", delivery_uuid=str(uuid.uuid4())
    )
    assert result["status"] == "skipped_team_at_capacity"
    assert captured_dispatches == []


async def test_full_disk_skips_without_erroring(
    client: AsyncClient,
    captured_dispatches: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workspace over its hard limit stops scans without erroring.

    The reading is faked rather than the threshold lowered. This used to set
    ``DISK_HARD_LIMIT_PCT=0`` so that any usage counted as over-limit, but that
    value now clamps to 50 (#36), which made the test pass or fail on how full
    the runner's disk happened to be. Faking ``statvfs`` at 99% used against the
    shipped default is both stable and closer to the real condition.
    """
    monkeypatch.setattr(
        os,
        "statvfs",
        lambda _path: SimpleNamespace(f_blocks=100, f_bavail=1, f_frsize=4096),
    )

    project, secret = await _make_gitlab_project(client)
    result = await _post_mr(
        client, project, secret, action="update", delivery_uuid=str(uuid.uuid4())
    )
    assert result["status"] == "skipped_disk_full"
    assert captured_dispatches == []


async def test_mr_scan_is_keyed_to_the_merge_request(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    """The scan's ref must be ``mr-<iid>``; MR hooks carry no top-level ref."""
    project, secret = await _make_gitlab_project(client)
    result = await _post_mr(
        client, project, secret, action="update", delivery_uuid=str(uuid.uuid4())
    )
    assert result["status"] == "enqueued"

    factory = await _factory(client)
    async with factory() as session:
        from models import Scan

        scan = (
            await session.execute(
                select(Scan).where(Scan.id == uuid.UUID(str(result["scan_id"])))
            )
        ).scalar_one()
        assert scan.ref == "mr-7"
        assert scan.scan_metadata["source"] == "webhook-gitlab"


async def test_old_gitlab_rescans_a_merge_request_after_a_new_push(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    """Without the delivery UUID header, successive pushes must still scan.

    The fallback delivery id used to be the merge request's own id, so every
    event on one MR shared it: the first was recorded and each later push was
    dismissed as a duplicate and never scanned. Pairing the id with the head
    SHA makes it move as the branch does.

    The first scan is marked terminal in between, because otherwise the active-
    scan guard would legitimately skip the second and hide what is under test.
    """
    project, secret = await _make_gitlab_project(client)

    first = await _post_mr(client, project, secret, action="update")
    assert first["status"] == "enqueued", first

    factory = await _factory(client)
    async with factory() as session:
        from models import Scan

        scan = (
            await session.execute(
                select(Scan).where(Scan.id == uuid.UUID(str(first["scan_id"])))
            )
        ).scalar_one()
        scan.status = "succeeded"
        await session.commit()

    # Same MR, new commit — a different head SHA is the only payload change.
    moved = _mr_payload(project.git_url, action="update")
    moved["object_attributes"]["last_commit"]["id"] = secrets.token_hex(20)
    second = await _post_mr(client, project, secret, action="update", payload=moved)

    assert second["status"] == "enqueued", "a new push to the same MR was skipped"
    assert len(captured_dispatches) == 2


# ---------------------------------------------------------------------------
# Happy path — Push Hook with valid token
# ---------------------------------------------------------------------------


async def test_valid_token_push_hook_enqueues_scan(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    project, secret = await _make_gitlab_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()
    delivery_id = str(uuid.uuid4())

    response = await client.post(
        "/v1/webhooks/gitlab",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": secret,
            "X-Gitlab-Event": "Push Hook",
            "X-Gitlab-Webhook-UUID": delivery_id,
        },
    )
    assert response.status_code == 200, response.text
    body_json = response.json()
    assert body_json["status"] == "enqueued"
    assert body_json["delivery_id"] == delivery_id
    assert body_json["scan_id"] is not None
    assert len(captured_dispatches) == 1


async def test_merge_request_hook_enqueues_scan(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    project, secret = await _make_gitlab_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()

    response = await client.post(
        "/v1/webhooks/gitlab",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": secret,
            "X-Gitlab-Event": "Merge Request Hook",
            "X-Gitlab-Webhook-UUID": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "enqueued"
    assert len(captured_dispatches) == 1


async def test_missing_webhook_uuid_falls_back_to_checkout_sha(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    """Older GitLab does not send X-Gitlab-Webhook-UUID; fall back to checkout_sha."""
    project, secret = await _make_gitlab_project(client)
    payload = _push_payload(project.git_url)
    body = json.dumps(payload).encode()

    response = await client.post(
        "/v1/webhooks/gitlab",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": secret,
            "X-Gitlab-Event": "Push Hook",
            # No X-Gitlab-Webhook-UUID — service must fall back to checkout_sha.
        },
    )
    assert response.status_code == 200, response.text
    body_json = response.json()
    assert body_json["status"] == "enqueued"
    # delivery_id should reflect the sha fallback ("sha:<hex>").
    assert body_json["delivery_id"] is not None
    assert body_json["delivery_id"].startswith("sha:")


# ---------------------------------------------------------------------------
# Authentication failures
# ---------------------------------------------------------------------------


async def test_wrong_token_returns_401(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    project, _secret = await _make_gitlab_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()

    response = await client.post(
        "/v1/webhooks/gitlab",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": "wrong-token-value",
            "X-Gitlab-Event": "Push Hook",
            "X-Gitlab-Webhook-UUID": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert captured_dispatches == []


async def test_missing_token_header_returns_400(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    project, _secret = await _make_gitlab_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()

    response = await client.post(
        "/v1/webhooks/gitlab",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Event": "Push Hook",
            "X-Gitlab-Webhook-UUID": str(uuid.uuid4()),
        },
    )
    assert response.status_code in (400, 401)
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert captured_dispatches == []


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_duplicate_webhook_uuid_is_idempotent(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    project, secret = await _make_gitlab_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()
    delivery_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-Gitlab-Token": secret,
        "X-Gitlab-Event": "Push Hook",
        "X-Gitlab-Webhook-UUID": delivery_id,
    }
    first = await client.post("/v1/webhooks/gitlab", content=body, headers=headers)
    second = await client.post("/v1/webhooks/gitlab", content=body, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "enqueued"
    assert second.json()["status"] == "duplicate"
    assert len(captured_dispatches) == 1


async def test_duplicate_persists_one_delivery_row(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    project, secret = await _make_gitlab_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()
    delivery_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-Gitlab-Token": secret,
        "X-Gitlab-Event": "Push Hook",
        "X-Gitlab-Webhook-UUID": delivery_id,
    }
    await client.post("/v1/webhooks/gitlab", content=body, headers=headers)
    await client.post("/v1/webhooks/gitlab", content=body, headers=headers)

    factory = await _factory(client)
    async with factory() as session:
        rows = (
            await session.execute(
                select(WebhookDelivery).where(
                    WebhookDelivery.provider == "gitlab",
                    WebhookDelivery.delivery_id == delivery_id,
                )
            )
        ).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Non-scan events
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_header",
    ["Issue Hook", "Note Hook", "Pipeline Hook", "Job Hook", "Wiki Page Hook"],
)
async def test_non_scan_event_returns_ignored_no_dispatch(
    client: AsyncClient, captured_dispatches: list[str], event_header: str
) -> None:
    project, secret = await _make_gitlab_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()

    response = await client.post(
        "/v1/webhooks/gitlab",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": secret,
            "X-Gitlab-Event": event_header,
            "X-Gitlab-Webhook-UUID": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ignored"
    assert captured_dispatches == []


# ---------------------------------------------------------------------------
# Adversarial token + body inputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,token_value",
    [
        ("rejects_crlf_token", "valid-token-prefix\r\nset-cookie: x=y"),
        ("rejects_null_byte_token", "valid-token\x00admin"),
        ("rejects_oversized_token", "x" * 5000),
        ("rejects_empty_token", ""),
    ],
)
async def test_malformed_token_returns_401(
    client: AsyncClient,
    captured_dispatches: list[str],
    label: str,
    token_value: str,
) -> None:
    project, _secret = await _make_gitlab_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()

    # httpx rejects CRLF in header values at the client level — caller must
    # pre-encode safely. We pre-strip the most pathological control bytes
    # before sending so the request reaches the server, then prove the
    # service still rejects the (now header-safe but secret-mismatched) token.
    safe_token = token_value.replace("\r", "").replace("\n", "").replace("\x00", "")

    response = await client.post(
        "/v1/webhooks/gitlab",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": safe_token,
            "X-Gitlab-Event": "Push Hook",
            "X-Gitlab-Webhook-UUID": str(uuid.uuid4()),
        },
    )
    assert response.status_code in (400, 401), (
        f"{label!r} got {response.status_code}: {response.text!r}"
    )
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert captured_dispatches == []


async def test_empty_body_returns_400(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/webhooks/gitlab",
        content=b"",
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": "anything",
            "X-Gitlab-Event": "Push Hook",
            "X-Gitlab-Webhook-UUID": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 400
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_invalid_json_body_returns_400(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/webhooks/gitlab",
        content=b"{[malformed",
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": "anything",
            "X-Gitlab-Event": "Push Hook",
            "X-Gitlab-Webhook-UUID": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 400
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_unknown_repo_is_indistinguishable_from_a_bad_token(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    """See the GitHub twin: the status code must not reveal what is onboarded."""
    unknown_body = json.dumps(_push_payload("https://gitlab.com/never/seen")).encode()
    unknown = await client.post(
        "/v1/webhooks/gitlab",
        content=unknown_body,
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": "any",
            "X-Gitlab-Event": "Push Hook",
            "X-Gitlab-Webhook-UUID": str(uuid.uuid4()),
        },
    )

    project, _secret = await _make_gitlab_project(client)
    known_body = json.dumps(_push_payload(project.git_url)).encode()
    wrong_token = await client.post(
        "/v1/webhooks/gitlab",
        content=known_body,
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": "not-the-projects-token",
            "X-Gitlab-Event": "Push Hook",
            "X-Gitlab-Webhook-UUID": str(uuid.uuid4()),
        },
    )

    assert unknown.status_code == 401
    assert unknown.headers["content-type"].startswith(PROBLEM_JSON)
    assert wrong_token.status_code == 401
    assert unknown.json() == wrong_token.json()
    assert captured_dispatches == []


async def test_oversized_payload_does_not_500(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    project, secret = await _make_gitlab_project(client)
    payload = _push_payload(project.git_url)
    payload["pad"] = "B" * (1024 * 1024)
    body = json.dumps(payload).encode()

    response = await client.post(
        "/v1/webhooks/gitlab",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": secret,
            "X-Gitlab-Event": "Push Hook",
            "X-Gitlab-Webhook-UUID": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "enqueued"


# ---------------------------------------------------------------------------
# #38: bounds on the pre-authentication surface
# ---------------------------------------------------------------------------


async def test_body_over_the_cap_is_refused_with_413(
    client: AsyncClient,
    captured_dispatches: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over the cap the receiver answers 413 before comparing the token.

    GitLab authenticates with a header rather than a body signature, but the
    repository still has to be resolved from the parsed payload, so the same
    unauthenticated parse work sits in front of the decision.
    """
    monkeypatch.setenv("WEBHOOK_MAX_BODY_BYTES", str(64 * 1024))
    project, secret = await _make_gitlab_project(client)
    payload = _push_payload(project.git_url)
    payload["pad"] = "B" * (80 * 1024)
    body = json.dumps(payload).encode()

    response = await client.post(
        "/v1/webhooks/gitlab",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": secret,
            "X-Gitlab-Event": "Push Hook",
            "X-Gitlab-Webhook-UUID": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 413, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert captured_dispatches == []


async def test_source_ip_over_the_rate_limit_gets_429(
    client: AsyncClient,
    captured_dispatches: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same IP-keyed budget as the GitHub receiver, they share the limit string."""
    monkeypatch.setenv("WEBHOOK_RATE_LIMIT", "2/minute")
    body = json.dumps(_push_payload("https://gitlab.com/acme/nothing-here")).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Gitlab-Token": "wrong-token",
        "X-Gitlab-Event": "Push Hook",
    }

    seen: list[int] = []
    for _ in range(3):
        response = await client.post(
            "/v1/webhooks/gitlab",
            content=body,
            headers={**headers, "X-Gitlab-Webhook-UUID": str(uuid.uuid4())},
        )
        seen.append(response.status_code)

    assert seen[:2] == [401, 401], seen
    assert seen[2] == 429, seen
    assert captured_dispatches == []
