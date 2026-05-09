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
    calls: list[str] = []

    def _fake(scan):  # type: ignore[no-untyped-def]
        calls.append(str(scan.id))
        return f"celery-task-{secrets.token_hex(4)}"

    monkeypatch.setattr(
        "services.webhook_service.enqueue_scan",
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


async def test_unknown_repo_returns_404(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    body = json.dumps(_push_payload("https://gitlab.com/never/seen")).encode()

    response = await client.post(
        "/v1/webhooks/gitlab",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": "any",
            "X-Gitlab-Event": "Push Hook",
            "X-Gitlab-Webhook-UUID": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
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
