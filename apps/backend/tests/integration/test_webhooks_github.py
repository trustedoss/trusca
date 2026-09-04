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
import secrets
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from models import Project, WebhookDelivery
from tests._db_required import migrate_to_head
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


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


@pytest.fixture
def captured_dispatches(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace ``services.scan_service.enqueue_scan`` with a recorder.

    The webhook's create-scan-and-dispatch sequence lives in
    ``services.scan_service.enqueue_system_triggered_scan_async`` (promoted
    there so the N18 scheduled-scan poller reuses the same guard-and-insert
    sequence), that module's ``enqueue_scan`` is the one actually called,
    not ``services.webhook_service``'s.

    Returns the recording list so tests can assert call count + scan ids.
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
# Real pull_request payloads — action filter and ref synthesis.
#
# These drive a payload shaped like the one GitHub actually sends rather than
# the push fixture above: the fields under test (`action`, `pull_request.number`)
# only exist there, and the push fixture would have passed either way.
# ---------------------------------------------------------------------------


def _pr_payload(repo_url: str | None, *, action: str) -> dict[str, Any]:
    fixture = FIXTURES / "github_pull_request_opened.json"
    payload: dict[str, Any] = json.loads(fixture.read_text(encoding="utf-8"))
    payload["action"] = action
    payload["repository"]["clone_url"] = repo_url
    payload["repository"]["html_url"] = repo_url
    return payload


async def _post_pr(
    client: AsyncClient,
    project: Project,
    secret: str,
    *,
    action: str,
) -> dict[str, Any]:
    body = json.dumps(_pr_payload(project.git_url, action=action)).encode()
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
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


@pytest.mark.parametrize("action", ["opened", "synchronize", "reopened"])
async def test_dependency_changing_pr_actions_scan(
    client: AsyncClient, captured_dispatches: list[str], action: str
) -> None:
    project, secret = await _make_github_project(client)
    result = await _post_pr(client, project, secret, action=action)
    assert result["status"] == "enqueued"
    assert len(captured_dispatches) == 1


@pytest.mark.parametrize("action", ["closed", "labeled", "assigned", "edited"])
async def test_non_dependency_pr_actions_do_not_scan(
    client: AsyncClient, captured_dispatches: list[str], action: str
) -> None:
    """Labelling a PR cannot change its dependencies, so it must not scan.

    Beyond the wasted worker time, one active scan is allowed per (project,
    ref): a scan started by `labeled` holds that slot, so a real push arriving
    behind it is skipped.
    """
    project, secret = await _make_github_project(client)
    result = await _post_pr(client, project, secret, action=action)
    assert result["status"] == "ignored"
    assert captured_dispatches == []


async def test_pr_scan_is_keyed_to_the_pull_request(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    """The scan's ref must be ``pr-<n>``, matching what the action sends.

    ``payload["ref"]`` does not exist on pull_request events, so reading it
    stored NULL and dropped every webhook-triggered PR scan into the project's
    ad-hoc cohort — where it superseded nothing, grouped with nothing, and
    competed with unrelated ref-less scans for the single active slot.
    """
    project, secret = await _make_github_project(client)
    result = await _post_pr(client, project, secret, action="opened")
    assert result["status"] == "enqueued"

    factory = await _factory(client)
    async with factory() as session:
        from models import Scan

        scan = (
            await session.execute(select(Scan).where(Scan.id == uuid.UUID(str(result["scan_id"]))))
        ).scalar_one()
        assert scan.ref == "pr-12"
        assert scan.scan_metadata["source"] == "webhook-github"


async def test_team_at_capacity_skips_without_erroring(
    client: AsyncClient,
    captured_dispatches: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The webhook path must honour the team concurrency cap.

    It builds scan rows directly rather than going through the API's
    ``prepare_scan_target``, so it had been bypassing the cap entirely: a batch
    push of many branches enqueued one scan per ref with nothing counting them.

    The verdict is reported, not raised. A 4xx/5xx would make the Git host
    retry a delivery that cannot succeed until capacity frees up, aiming a
    retry storm at a system already at its limit.
    """
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "1")

    project, secret = await _make_github_project(client)
    # Occupy the team's single slot with a scan on an unrelated ref, so the
    # per-(project, ref) index cannot be what blocks the delivery below.
    factory = await _factory(client)
    async with factory() as session:
        from tests._helpers import make_scan

        stored = (
            await session.execute(select(Project).where(Project.id == project.id))
        ).scalar_one()
        await make_scan(session, project=stored, status="running", ref="release/1.x")

    body = json.dumps(_push_payload(project.git_url)).encode()
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
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "skipped_team_at_capacity"
    assert captured_dispatches == []


async def test_capacity_skip_leaves_the_delivery_id_reusable(
    client: AsyncClient,
    captured_dispatches: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A capacity skip must not burn the delivery id.

    The push has to be scannable after the operator frees capacity and the Git
    host redelivers. Otherwise it is lost permanently on GitLab installs whose
    delivery id is derived from the payload rather than a per-delivery UUID.

    This used to be achieved by not recording the delivery at all, which left
    the skip with no database trace (gap #39). The row is written now, carrying
    the reason, and the redelivery supersedes it: the id names the delivery's
    current state rather than a counter that gets spent.
    """
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "1")

    project, secret = await _make_github_project(client)
    factory = await _factory(client)
    async with factory() as session:
        from tests._helpers import make_scan

        stored = (
            await session.execute(select(Project).where(Project.id == project.id))
        ).scalar_one()
        blocker = await make_scan(
            session, project=stored, status="running", ref="release/1.x"
        )
        blocker_id = blocker.id

    body = json.dumps(_push_payload(project.git_url)).encode()
    delivery_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": _sign(body, secret),
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": delivery_id,
    }

    blocked = await client.post("/v1/webhooks/github", content=body, headers=headers)
    assert blocked.json()["status"] == "skipped_team_at_capacity"

    # The delivery is recorded, and says why it went unscanned.
    async with factory() as session:
        rows = (
            await session.execute(
                select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id)
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].outcome == "skipped_team_at_capacity"
    assert rows[0].enqueued_scan_id is None

    # Free the capacity and redeliver the very same event.
    async with factory() as session:
        from models import Scan

        scan = (
            await session.execute(select(Scan).where(Scan.id == blocker_id))
        ).scalar_one()
        scan.status = "succeeded"
        await session.commit()

    retried = await client.post("/v1/webhooks/github", content=body, headers=headers)
    assert retried.json()["status"] == "enqueued", "redelivery could not recover"
    assert len(captured_dispatches) == 1

    # Superseded in place: one row for one delivery, now saying it scanned.
    async with factory() as session:
        rows = (
            await session.execute(
                select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id)
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].outcome == "enqueued"
    assert rows[0].enqueued_scan_id is not None


async def test_unauthenticated_delivery_writes_nothing(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    """A rejected delivery must leave no trace in webhook_deliveries.

    The rejection happens before the idempotency gate, so an unauthenticated
    caller cannot fill the table or claim delivery ids that a legitimate
    redelivery would later need.
    """
    delivery_id = str(uuid.uuid4())
    body = json.dumps(_push_payload("https://github.com/never/heardof")).encode()
    response = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, "any-secret"),
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": delivery_id,
        },
    )
    assert response.status_code == 401

    factory = await _factory(client)
    async with factory() as session:
        rows = (
            await session.execute(
                select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id)
            )
        ).scalars().all()
    assert rows == []
    assert captured_dispatches == []


async def test_push_behind_an_active_scan_says_it_was_skipped(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    """A skipped push must not be reported as a duplicate delivery.

    Lifecycle sequence (hardening rule 5): scan running -> new delivery arrives
    -> we decline to start a second one. Reporting that as ``duplicate`` told
    the operator "we already handled this delivery", so a genuinely unscanned
    commit looked handled in the SCM's delivery log.
    """
    project, secret = await _make_github_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()

    first = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, secret),
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    assert first.json()["status"] == "enqueued"

    # A DIFFERENT delivery id — this is a new event, not a replay.
    second = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, secret),
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "skipped_active_scan"
    assert second.json()["scan_id"] is None
    assert len(captured_dispatches) == 1

    # And a true replay of the first delivery still reads as duplicate, so the
    # two remain distinguishable.
    replay_id = str(uuid.uuid4())
    for _ in range(2):
        replay = await client.post(
            "/v1/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign(body, secret),
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": replay_id,
            },
        )
    assert replay.json()["status"] == "duplicate"


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


async def test_unknown_repo_is_indistinguishable_from_a_bad_signature(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    """An unconfigured repository must answer exactly as a bad signature does.

    The project lookup necessarily precedes signature verification, because the
    secret is per-project. That made the status code an oracle: an
    unauthenticated caller could POST a payload naming any repository URL and
    read 404-vs-401 as "this portal does not watch that repo" versus "it does".
    Walking a list of an organisation's repositories then maps which ones are
    onboarded here.

    Asserting the two responses match byte-for-byte, rather than just asserting
    401, is what actually pins the property: a future edit that adds a
    distinguishing detail string to either branch fails here.
    """
    unknown_body = json.dumps(_push_payload("https://github.com/never/heardof")).encode()
    unknown = await client.post(
        "/v1/webhooks/github",
        content=unknown_body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(unknown_body, "any-secret"),
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )

    project, _secret = await _make_github_project(client)
    known_body = json.dumps(_push_payload(project.git_url)).encode()
    wrong_signature = await client.post(
        "/v1/webhooks/github",
        content=known_body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(known_body, "not-the-projects-secret"),
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )

    assert unknown.status_code == 401
    assert unknown.headers["content-type"].startswith(PROBLEM_JSON)
    assert wrong_signature.status_code == 401
    assert unknown.json() == wrong_signature.json()
    assert captured_dispatches == []


async def test_oversized_payload_does_not_500(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    """A 1MB payload with no matching repo must return 4xx, never 500."""
    project, secret = await _make_github_project(client)
    payload = _push_payload(project.git_url)
    # Add ~1MB of pad text. This stays below the 2 MiB WEBHOOK_MAX_BODY_BYTES
    # default (#38) and is large enough to exercise the body-read + HMAC path.
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
    """Control bytes in repo URL → unmatched project → 401, never 500.

    The point of these cases is that the normalizer fails closed rather than
    letting a control byte reach Postgres (which cannot encode NUL and would
    500). An unmatched project now answers 401 like any other unrecognised
    repository, so that is what we assert; the property under test is the
    absence of a 5xx.
    """
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
    assert response.status_code == 401, (
        f"{label!r} got {response.status_code}: {response.text!r}"
    )
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert captured_dispatches == []


# ---------------------------------------------------------------------------
# #38: bounds on the pre-authentication surface
# ---------------------------------------------------------------------------


async def test_body_over_the_cap_is_refused_with_413(
    client: AsyncClient,
    captured_dispatches: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over the cap the receiver answers 413 and does no signature work.

    The signature covers the body, so it cannot be checked until the body is
    read. Without a cap the caller decides how much the process reads, and
    resolving the repository afterwards is linear in the URL length, so the
    work in front of the first credential check scales with an attacker-chosen
    number.
    """
    monkeypatch.setenv("WEBHOOK_MAX_BODY_BYTES", str(64 * 1024))
    project, secret = await _make_github_project(client)
    payload = _push_payload(project.git_url)
    payload["pad"] = "A" * (80 * 1024)
    body = json.dumps(payload).encode()

    response = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            # A signature that would otherwise verify, the cap decides first.
            "X-Hub-Signature-256": _sign(body, secret),
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 413, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert captured_dispatches == []


async def test_body_at_the_cap_still_scans(
    client: AsyncClient,
    captured_dispatches: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap refuses what is over it, not what is near it."""
    monkeypatch.setenv("WEBHOOK_MAX_BODY_BYTES", str(64 * 1024))
    project, secret = await _make_github_project(client)
    payload = _push_payload(project.git_url)
    payload["pad"] = "A" * (32 * 1024)
    body = json.dumps(payload).encode()
    assert len(body) < 64 * 1024

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
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "enqueued"


async def test_source_ip_over_the_rate_limit_gets_429(
    client: AsyncClient,
    captured_dispatches: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The receiver is public, so the only pre-auth identity is the source IP.

    A 429 costs a delivery: the Git host does not retry on its own, which is
    why the shipped budget is far above real traffic. The limit itself is what
    stops an anonymous flood from spending the parse path indefinitely.
    """
    monkeypatch.setenv("WEBHOOK_RATE_LIMIT", "2/minute")
    body = json.dumps(_push_payload("https://github.com/acme/nothing-here")).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": _sign(body, "wrong-secret"),
        "X-GitHub-Event": "push",
    }

    seen: list[int] = []
    for _ in range(3):
        response = await client.post(
            "/v1/webhooks/github",
            content=body,
            headers={**headers, "X-GitHub-Delivery": str(uuid.uuid4())},
        )
        seen.append(response.status_code)

    assert seen[:2] == [401, 401], seen
    assert seen[2] == 429, seen
    assert captured_dispatches == []


# ---------------------------------------------------------------------------
# #39: every ending is answerable by a query, not only by reading the logs
# ---------------------------------------------------------------------------


async def test_every_ending_is_recorded_on_the_delivery(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    """`enqueued_scan_id IS NULL` used to collapse four different endings.

    An operator asking "which pushes went unscanned in the last day, and why"
    had to aggregate logs. This drives three of the endings through the real
    endpoint and reads them back with one SELECT; the capacity skips have their
    own test above because they need a blocker scan to provoke.
    """
    project, secret = await _make_github_project(client)
    factory = await _factory(client)

    async def _deliver(event: str, delivery_id: str, payload: dict[str, object]) -> str:
        body = json.dumps(payload).encode()
        response = await client.post(
            "/v1/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign(body, secret),
                "X-GitHub-Event": event,
                "X-GitHub-Delivery": delivery_id,
            },
        )
        assert response.status_code == 200, response.text
        return str(response.json()["status"])

    enqueued_id = str(uuid.uuid4())
    ignored_id = str(uuid.uuid4())

    assert await _deliver("push", enqueued_id, _push_payload(project.git_url)) == "enqueued"
    assert await _deliver("issues", ignored_id, _push_payload(project.git_url)) == "ignored"
    # The same delivery id again: a genuine replay from the Git host.
    assert await _deliver("push", enqueued_id, _push_payload(project.git_url)) == "duplicate"

    async with factory() as session:
        rows = (
            await session.execute(
                select(WebhookDelivery).where(
                    WebhookDelivery.delivery_id.in_([enqueued_id, ignored_id])
                )
            )
        ).scalars().all()

    by_id = {r.delivery_id: r for r in rows}
    assert by_id[enqueued_id].outcome == "enqueued"
    assert by_id[enqueued_id].enqueued_scan_id is not None
    assert by_id[ignored_id].outcome == "ignored"
    assert by_id[ignored_id].enqueued_scan_id is None
    # A replay does not overwrite what the original delivery achieved.
    assert by_id[enqueued_id].outcome != "duplicate"


async def test_unscanned_deliveries_are_one_query(
    client: AsyncClient, captured_dispatches: list[str]
) -> None:
    """The question the column exists for, asked the way an operator would."""
    project, secret = await _make_github_project(client)
    factory = await _factory(client)

    body = json.dumps(_push_payload(project.git_url)).encode()
    ignored_id = str(uuid.uuid4())
    response = await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, secret),
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": ignored_id,
        },
    )
    assert response.json()["status"] == "ignored"

    async with factory() as session:
        unscanned = (
            await session.execute(
                select(WebhookDelivery.outcome).where(
                    WebhookDelivery.project_id == project.id,
                    WebhookDelivery.enqueued_scan_id.is_(None),
                )
            )
        ).scalars().all()

    assert unscanned == ["ignored"], "the reason must come back with the row"


async def test_replay_of_a_scanned_delivery_is_duplicate_even_at_capacity(
    client: AsyncClient,
    captured_dispatches: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The duplicate check runs before the capacity guard now.

    It used to be the other way round, and the guide had to explain that
    redelivering an already-scanned event during a capacity crunch answered
    "skipped_team_at_capacity", a status about this request that read like a
    statement about the commit. Asking "have we seen this?" first removes the
    ambiguity, and the delivery keeps the outcome it earned.
    """
    project, secret = await _make_github_project(client)
    body = json.dumps(_push_payload(project.git_url)).encode()
    delivery_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": _sign(body, secret),
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": delivery_id,
    }

    first = await client.post("/v1/webhooks/github", content=body, headers=headers)
    assert first.json()["status"] == "enqueued"

    # Now fill the team's capacity and replay the same delivery.
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "1")
    replay = await client.post("/v1/webhooks/github", content=body, headers=headers)
    assert replay.json()["status"] == "duplicate"

    factory = await _factory(client)
    async with factory() as session:
        row = (
            await session.execute(
                select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id)
            )
        ).scalar_one()
    assert row.outcome == "enqueued", "a replay must not rewrite the original ending"
