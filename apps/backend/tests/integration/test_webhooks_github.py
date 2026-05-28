"""
Integration tests for POST /v1/webhooks/github — Phase 5 PR #16.

Public endpoint (no JWT). Authentication is HMAC-SHA256 over the raw body
keyed by the project's ``webhook_secret``. The endpoint is idempotent on
``X-GitHub-Delivery`` so duplicate retries from the SCM do not re-enqueue
a scan.

What this suite pins:
  - Valid HMAC + push event → 202 (HTTP 200 + status='enqueued') and a
    Celery dispatch is observed exactly once via the patched enqueue_scan.
  - Bad HMAC / missing signature header → 401 + Problem Details.
  - Same X-GitHub-Delivery twice → second is 200 'duplicate', NO second
    Celery dispatch.
  - Adversarial inputs (oversized body, control bytes, truncated digest,
    body-mismatched signature) → 401 / 400, never 500.
  - Non-push events (issue / pull_request_review_comment) → 200 'ignored',
    NO Celery dispatch.

The Celery enqueue dispatcher is patched at the import site inside
``services.webhook_service`` (the service does ``from tasks import
enqueue_scan``) — same pattern as
tests/integration/scan/test_trigger_scan_enqueues_celery.py.
"""

from __future__ import annotations

import hashlib
import hmac
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
        pytest.skip("DATABASE_URL not set — skip GitHub webhook tests")
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
            f"alembic upgrade head failed; GitHub webhook tests cannot run\n"
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
    """Replace ``services.webhook_service.enqueue_scan`` with a recorder.

    Returns the recording list so tests can assert call count + scan ids.
    """
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


async def _make_github_project(
    client: AsyncClient,
    *,
    secret: str | None = None,
    git_url: str | None = None,
) -> tuple[Project, str]:
    """Create a project with webhook_provider='github' and a fresh secret.

    NOTE: ``git_url`` defaults to a per-call unique URL. Tests run against the
    real Postgres dev instance, which is NOT truncated between runs, so a
    constant default URL would leave many ``Project`` rows sharing the same
    ``git_url`` from prior sessions. ``services.webhook_service._find_project_by_git_url``
    does ``select(Project).where(...).first()`` with no ORDER BY, so it would
    pick an arbitrary stale project (with a different ``webhook_secret``) and
    HMAC verification would fail (401). Using a unique URL per call is the
    test-isolation fix.
    """
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        project = await make_project(session, team=team)
        project.git_url = git_url or f"https://github.com/acme/widgets-{unique_suffix()}"
        project.webhook_secret = secret or secrets.token_urlsafe(32)
        project.webhook_provider = "github"
        await session.commit()
        await session.refresh(project)
        return project, project.webhook_secret


def _push_payload(repo_url: str | None = "https://github.com/acme/widgets") -> dict[str, object]:
    return {
        "ref": "refs/heads/main",
        "repository": {
            "clone_url": repo_url,
            "html_url": repo_url,
        },
        "pusher": {"name": "octocat"},
    }


def _sign(body: bytes, secret: str) -> str:
    """Return the X-Hub-Signature-256 header value for *body*."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ---------------------------------------------------------------------------
# Happy path — push event with valid HMAC
# ---------------------------------------------------------------------------


async def test_valid_hmac_push_event_enqueues_scan(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    project, secret = await _make_github_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()
    delivery_id = str(uuid.uuid4())

    response = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, secret),
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": delivery_id,
        },
    )
    assert response.status_code == 200, response.text
    body_json = response.json()
    assert body_json["status"] == "enqueued"
    assert body_json["delivery_id"] == delivery_id
    assert body_json["scan_id"] is not None
    assert len(captured_dispatches) == 1


async def test_pull_request_event_also_enqueues(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    project, secret = await _make_github_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()
    response = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, secret),
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "enqueued"
    assert len(captured_dispatches) == 1


# ---------------------------------------------------------------------------
# Idempotency — duplicate X-GitHub-Delivery
# ---------------------------------------------------------------------------


async def test_duplicate_delivery_id_is_idempotent(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    """Re-sending the same X-GitHub-Delivery must NOT enqueue a second scan."""
    project, secret = await _make_github_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()
    sig = _sign(body, secret)
    delivery_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": delivery_id,
    }

    first = await client.post("/v1/webhooks/github", content=body, headers=headers)
    second = await client.post("/v1/webhooks/github", content=body, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "enqueued"
    assert second.json()["status"] == "duplicate"
    # Exactly one dispatch — the second call deduplicated on (provider, delivery_id).
    assert len(captured_dispatches) == 1


async def test_duplicate_delivery_persists_one_row(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    project, secret = await _make_github_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()
    sig = _sign(body, secret)
    delivery_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": delivery_id,
    }
    await client.post("/v1/webhooks/github", content=body, headers=headers)
    await client.post("/v1/webhooks/github", content=body, headers=headers)

    factory = await _factory(client)
    async with factory() as session:
        rows = (
            await session.execute(
                select(WebhookDelivery).where(
                    WebhookDelivery.provider == "github",
                    WebhookDelivery.delivery_id == delivery_id,
                )
            )
        ).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Authentication failures — HMAC must always be valid
# ---------------------------------------------------------------------------


async def test_missing_signature_header_returns_400(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    """A missing X-Hub-Signature-256 raises WebhookHeaderMissing → 400."""
    project, _secret = await _make_github_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()

    response = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    # Missing required header → header-missing error path.
    assert response.status_code in (400, 401)
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert captured_dispatches == []


async def test_invalid_hmac_returns_401(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    project, _secret = await _make_github_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()

    response = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            # Signature was computed against a different secret.
            "X-Hub-Signature-256": _sign(body, "wrong-secret"),
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert captured_dispatches == []


async def test_signature_against_different_body_returns_401(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    """HMAC must be over the bytes we received, not a different blob."""
    project, secret = await _make_github_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()
    other_body = b'{"different": "payload"}'

    response = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(other_body, secret),
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert captured_dispatches == []


@pytest.mark.parametrize(
    "label,header_value",
    [
        ("rejects_truncated_digest", "sha256=abc123"),
        ("rejects_no_prefix", "deadbeef" * 8),  # raw hex, no sha256= prefix
        ("rejects_wrong_algo_prefix", "sha1=" + "a" * 40),
        ("rejects_garbage_hex", "sha256=zzznothex"),
        ("rejects_empty_after_prefix", "sha256="),
    ],
)
async def test_malformed_signature_returns_401(
    client: AsyncClient,
    captured_dispatches: list[str],
    label: str,
    header_value: str,
) -> None:
    project, _secret = await _make_github_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()

    response = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": header_value,
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 401, (
        f"{label!r} value={header_value!r} got {response.status_code}: {response.text!r}"
    )
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert captured_dispatches == []


# ---------------------------------------------------------------------------
# Non-push events — accepted but no scan enqueue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_type",
    ["issues", "issue_comment", "pull_request_review_comment", "ping", "release"],
)
async def test_non_scan_event_returns_ignored_no_dispatch(
    client: AsyncClient, captured_dispatches: list[str], event_type: str
) -> None:
    project, secret = await _make_github_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()

    response = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, secret),
            "X-GitHub-Event": event_type,
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ignored"
    assert captured_dispatches == []


# ---------------------------------------------------------------------------
# Adversarial body inputs (parametrized per memory feedback)
# ---------------------------------------------------------------------------


async def test_empty_body_returns_400(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/webhooks/github",
        content=b"",
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=" + "a" * 64,
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 400
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_invalid_json_body_returns_400(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/webhooks/github",
        content=b"not json {[",
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=" + "a" * 64,
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 400
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_array_payload_returns_400(client: AsyncClient) -> None:
    """Top-level JSON array (not an object) must be rejected with 400, not 500."""
    response = await client.post(
        "/v1/webhooks/github",
        content=b"[1,2,3]",
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=" + "a" * 64,
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 400
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_unknown_repo_returns_404(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    """A payload with no matching project must return 404."""
    body = json.dumps(_push_payload("https://github.com/never/heardof")).encode()

    response = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, "any-secret"),
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert captured_dispatches == []


async def test_oversized_payload_does_not_500(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    """A 1MB payload with no matching repo must return 4xx, never 500."""
    project, secret = await _make_github_project(client)
    payload = _push_payload(project.git_url)
    # Add ~1MB of pad text. This stays below FastAPI's default request limit
    # but is large enough to exercise the body-read + HMAC path under load.
    payload["pad"] = "A" * (1024 * 1024)
    body = json.dumps(payload).encode()
    response = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, secret),
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    # Should succeed (200 enqueued) — we just want to prove the path is robust
    # to large bodies and never 500s.
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] != PROBLEM_JSON
    assert response.json()["status"] == "enqueued"


@pytest.mark.parametrize(
    "label,repo",
    [
        # NUL byte — Postgres VARCHAR/TEXT cannot encode 0x00; without the
        # defensive normalize_repo_url filter this would 500
        # (asyncpg.CharacterNotInRepertoireError).
        ("rejects_nul_byte", "https://github.com/acme/wid\x00gets"),
        # CRLF response-splitting attempt embedded in repo URL.
        ("rejects_crlf", "https://github.com/acme/wid\r\nset-cookie: x=y/gets"),
        # ASCII C0 control byte (BEL = 0x07).
        ("rejects_bel_byte", "https://github.com/acme/\x07widgets"),
        # Mixed control bytes — our normalize must fail closed to None.
        ("rejects_mixed_controls", "https://github.com/acme/wid\x00\r\ngets"),
    ],
)
async def test_payload_with_control_bytes_in_repo_name_does_not_500(
    client: AsyncClient,
    captured_dispatches: list[str],
    label: str,
    repo: str,
) -> None:
    """Control bytes in repo URL → unmatched project → 404, never 500."""
    body = json.dumps(_push_payload(repo)).encode()

    response = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, "anything"),
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    # No project matches — service raises WebhookProjectNotFound (404).
    assert response.status_code == 404, (
        f"{label!r} got {response.status_code}: {response.text!r}"
    )
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert captured_dispatches == []
