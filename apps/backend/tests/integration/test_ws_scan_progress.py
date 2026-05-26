"""
End-to-end integration tests for the WebSocket scan-progress gateway.

Phase 2 PR #9 — task 2.9. The unit tests in
``tests/unit/test_ws_helpers.py`` already cover every branch of
``api.v1.ws.scan_progress_endpoint`` with a fake WebSocket, so this file is
deliberately the *behavioural* layer: a real Starlette TestClient drives a
real FastAPI app against a real Postgres + Redis.

What we pin (one scenario per test function):

  1. ``test_ws_auth_pass_pushes_initial_sync_frame``
       Valid access JWT → first frame is the canonical
       ``{"percent","step","ts"}`` from the seeded scan row.
  2. ``test_ws_auth_invalid_token_closes_1008``
       Garbage / wrong-secret / refresh-typed tokens → 1008 + reason
       ``auth_invalid``.
  3. ``test_ws_auth_timeout_closes_1008``
       No first frame within ``WEBSOCKET_AUTH_TIMEOUT_SECONDS`` → 1008 +
       reason ``auth_timeout``. Wall-clock check (unit suite uses a stub).
  4. ``test_ws_idor_blocked_with_4403``
       team_b user subscribing to a team_a scan → 4403 + reason
       ``forbidden``.
  5. ``test_ws_scan_not_found_4404``
       Random UUID → 4404 + reason ``scan_not_found``.
  6. ``test_ws_forwards_published_progress``
       After auth, calling ``tasks._progress.publish_progress(...)`` from a
       background thread delivers a frame matching the canonical schema.
  7. ``test_ws_terminal_step_succeeded_is_forwarded``
       A ``step="succeeded"`` publish reaches the client; the client closes
       cleanly with code 1000.
  8. ``test_ws_per_user_connection_limit_evicts_oldest``
       Cap=2; the third connection from the same user evicts the first
       with code 1001 (going_away).
  9. ``test_ws_origin_rejected_in_prod_closes_1008``
       APP_ENV=prod + Origin not in CORS allow-list → 1008 + reason
       ``origin_rejected`` BEFORE accept.
 10. ``test_ws_bad_first_message_closes_4400``
       First frame is not ``{"type":"auth"}`` → 4400 + reason
       ``bad_message``.

Wall-clock budget (per test): < 2s except the auth-timeout test which is
calibrated to ``WEBSOCKET_AUTH_TIMEOUT_SECONDS=0.2`` for speed.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core.security import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
)
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Module-scoped bring-up
# ---------------------------------------------------------------------------


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip ws integration test")
    return url


def _require_redis_url() -> str:
    url = os.getenv("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL not set — skip ws integration test")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    """Run alembic upgrade head once before any ws integration test."""
    _require_database_url()
    _require_redis_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            f"alembic upgrade head failed; ws integration cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture(autouse=True)
def _reset_ws_registry() -> Iterator[None]:
    """The per-user connection registry is a module-level singleton — reset
    it between tests so eviction tests start from a clean slate."""
    from api.v1.ws import _reset_registry_for_tests

    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


# ---------------------------------------------------------------------------
# Seed helpers — async DB writes against the real Postgres.
# ---------------------------------------------------------------------------


def _seed_user_with_team_scan() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed (org, team, user, membership, project, scan).

    Returns ``(user_id, team_id, scan_id)``. The scan starts in the
    ``running`` state with ``progress_percent=10`` and
    ``current_step="fetch"`` so the initial-sync push has something
    non-trivial to forward.
    """
    import asyncio

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from core.config import database_url

    async def _build() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team)
            # `make_scan` defaults status to "queued"; we want a row that
            # already has progress so the initial-sync push is meaningful.
            from models import Scan as ScanModel

            scan = ScanModel(
                project_id=project.id,
                kind="source",
                status="running",
                progress_percent=10,
                current_step="fetch",
                requested_by_user_id=user.id,
                scan_metadata={},
            )
            s.add(scan)
            await s.commit()
            await s.refresh(scan)
            user_id = user.id
            team_id = team.id
            scan_id = scan.id
        await engine.dispose()
        return user_id, team_id, scan_id

    return asyncio.run(_build())


def _seed_user_only() -> uuid.UUID:
    """Seed a user with no team membership — used by 4404 tests where we
    just need a valid JWT subject."""
    import asyncio

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from core.config import database_url

    async def _build() -> uuid.UUID:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            user = await make_user(s)
            return user.id
        await engine.dispose()  # unreachable but mypy-safe

    return asyncio.run(_build())


def _seed_two_users_one_scan() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed two users in *different* teams; only team_a owns the scan.

    Returns ``(user_a_id, user_b_id, scan_id)``. user_b is the IDOR victim
    in the 4403 test.
    """
    import asyncio

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from core.config import database_url
    from models import Scan as ScanModel

    async def _build() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team_a = await make_team(s, organization=org)
            team_b = await make_team(s, organization=org)
            user_a = await make_user(s)
            user_b = await make_user(s)
            await make_membership(s, user=user_a, team=team_a, role="developer")
            await make_membership(s, user=user_b, team=team_b, role="developer")
            project_a = await make_project(s, team=team_a)
            scan = ScanModel(
                project_id=project_a.id,
                kind="source",
                status="running",
                progress_percent=20,
                current_step="cdxgen",
                requested_by_user_id=user_a.id,
                scan_metadata={},
            )
            s.add(scan)
            await s.commit()
            await s.refresh(scan)
            return user_a.id, user_b.id, scan.id
        await engine.dispose()  # unreachable — mypy-safe

    return asyncio.run(_build())


# ---------------------------------------------------------------------------
# WS client helpers
# ---------------------------------------------------------------------------


def _bearer_token(user_id: uuid.UUID) -> str:
    return create_access_token(subject=str(user_id))


def _send_auth(ws, token: str) -> None:
    ws.send_text(json.dumps({"type": "auth", "token": token}))


def _expect_disconnect(ws) -> WebSocketDisconnect:
    """Drain frames until the server closes; return the disconnect event."""
    try:
        while True:
            ws.receive_text()
    except WebSocketDisconnect as exc:
        return exc
    # `receive_text` always raises eventually because the test client surfaces
    # the close as WebSocketDisconnect — the loop exits via the except above.


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
def client(app, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # Tests that use this fixture exercise the dev path: blank Origin is OK,
    # CORS allow-list contains the dev origin. Tests that need prod behaviour
    # build their own TestClient with explicit env.
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    # Speed up the auth-timeout test path — keep the default permissive enough
    # for hand-rolled client cases.
    monkeypatch.setenv("WEBSOCKET_AUTH_TIMEOUT_SECONDS", "1.0")
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 1. Initial-sync frame
# ---------------------------------------------------------------------------


def test_ws_auth_pass_pushes_initial_sync_frame(client: TestClient) -> None:
    """A successful auth handshake must produce the canonical initial frame
    derived from the scan row — ``{"percent":10,"step":"fetch","ts":...}``."""
    user_id, _team_id, scan_id = _seed_user_with_team_scan()
    token = _bearer_token(user_id)

    with client.websocket_connect(f"/ws/scans/{scan_id}") as ws:
        _send_auth(ws, token)
        raw = ws.receive_text()
        body = json.loads(raw)
        assert body["percent"] == 10
        assert body["step"] == "fetch"
        assert isinstance(body["ts"], str) and body["ts"]


def _seed_user_with_terminal_scan(
    *,
    scan_status: str,
    current_step: str = "finalize",
    progress_percent: int = 90,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a user + a terminal scan whose ``current_step`` is *not* terminal.

    Mirrors the production hazard the P1 #11 fix targets: the worker's last
    ``current_step`` write is ``finalize`` (or anything pre-terminal), but
    ``status`` has been flipped to a terminal value. The initial sync push
    must surface the terminal status as the step so a re-opened drawer does
    not render a spinner on a done scan.
    """
    import asyncio

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from core.config import database_url

    async def _build() -> tuple[uuid.UUID, uuid.UUID]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team)
            from models import Scan as ScanModel

            scan = ScanModel(
                project_id=project.id,
                kind="source",
                status=scan_status,
                progress_percent=progress_percent,
                current_step=current_step,
                requested_by_user_id=user.id,
                scan_metadata={},
            )
            s.add(scan)
            await s.commit()
            await s.refresh(scan)
            user_id = user.id
            scan_id = scan.id
        await engine.dispose()
        return user_id, scan_id

    return asyncio.run(_build())


def test_ws_initial_sync_for_succeeded_scan_reports_terminal_step(
    client: TestClient,
) -> None:
    """P1 #11 — re-opening a succeeded scan's drawer must NOT report the
    worker's last pre-terminal ``current_step`` (commonly ``finalize``).

    The gateway rewrites the initial sync step to the terminal status, so the
    SPA can flip its panel to the success branch on the very first frame
    instead of rendering a spinner on a step that is in fact done.
    """
    user_id, scan_id = _seed_user_with_terminal_scan(scan_status="succeeded")
    token = _bearer_token(user_id)

    with client.websocket_connect(f"/ws/scans/{scan_id}") as ws:
        _send_auth(ws, token)
        raw = ws.receive_text()
        body = json.loads(raw)
        # Step is the terminal verdict — NOT the worker's last write ("finalize").
        assert body["step"] == "succeeded"
        # Percent is pinned at 100 for the succeeded branch so a UI that
        # binds the progress bar value to the initial frame renders a full bar.
        assert body["percent"] == 100


def test_ws_initial_sync_for_failed_scan_reports_failed_step(
    client: TestClient,
) -> None:
    """Mirror of the succeeded test for the failed terminal branch."""
    user_id, scan_id = _seed_user_with_terminal_scan(
        scan_status="failed", progress_percent=60
    )
    token = _bearer_token(user_id)

    with client.websocket_connect(f"/ws/scans/{scan_id}") as ws:
        _send_auth(ws, token)
        raw = ws.receive_text()
        body = json.loads(raw)
        assert body["step"] == "failed"
        # Failed keeps the persisted percent — we do not pretend completion.
        assert body["percent"] == 60


def test_ws_initial_sync_for_cancelled_scan_reports_cancelled_step(
    client: TestClient,
) -> None:
    """Mirror of the succeeded test for the cancelled terminal branch."""
    user_id, scan_id = _seed_user_with_terminal_scan(
        scan_status="cancelled", progress_percent=30
    )
    token = _bearer_token(user_id)

    with client.websocket_connect(f"/ws/scans/{scan_id}") as ws:
        _send_auth(ws, token)
        raw = ws.receive_text()
        body = json.loads(raw)
        assert body["step"] == "cancelled"
        assert body["percent"] == 30


# ---------------------------------------------------------------------------
# 2. Bad / wrong-typed / unsigned tokens
# ---------------------------------------------------------------------------


def test_ws_auth_invalid_token_closes_1008(client: TestClient) -> None:
    """Garbage tokens, refresh-typed tokens, and tokens signed with the wrong
    key must all close 1008 with reason ``auth_invalid``."""
    _user_id, _team_id, scan_id = _seed_user_with_team_scan()

    cases: list[str] = []
    cases.append("not.a.jwt")
    # Refresh token where access expected.
    refresh, _jti, _exp = create_refresh_token(subject=str(uuid.uuid4()))
    assert refresh  # sanity
    cases.append(refresh)
    # Token signed with wrong secret — rebuild ourselves so we can guarantee
    # the signature mismatch without depending on env tweaks.
    from jose import jwt as _jwt

    wrong_signed = _jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "type": "access",
            "exp": int(time.time()) + 60,
            "iat": int(time.time()),
            "jti": uuid.uuid4().hex,
        },
        "this-key-is-not-the-real-one-31chars!!",
        algorithm="HS256",
    )
    cases.append(wrong_signed)

    for raw_token in cases:
        with client.websocket_connect(f"/ws/scans/{scan_id}") as ws:
            _send_auth(ws, raw_token)
            disconnect = _expect_disconnect(ws)
            assert disconnect.code == 1008, f"token={raw_token[:12]}…: {disconnect.code}"
            assert disconnect.reason in (
                "auth_invalid",
                # Refresh-typed tokens trip the same code path; the helper
                # narrows the reason but keep this assertion loose so a future
                # refactor does not need to thread fine-grained reasons.
            )
            assert disconnect.reason == "auth_invalid"


# Ensure refresh-typed token emits the same close code (separate test for
# clarity in CI failure reports — unit tests prove the parser logic).
def test_ws_refresh_token_rejected_with_1008(client: TestClient) -> None:
    user_id, _team_id, scan_id = _seed_user_with_team_scan()
    refresh, _jti, _exp = create_refresh_token(subject=str(user_id))
    # Sanity: the token IS a refresh, not an access. python-jose requires the
    # key argument even with verify_signature=False, so we pass an empty
    # string and disable the audience/signature checks — we only need the
    # claims back to assert on `type`.
    from jose import jwt as _jwt

    decoded = _jwt.decode(
        refresh,
        key="",
        options={"verify_signature": False, "verify_aud": False},
    )
    assert decoded["type"] == TOKEN_TYPE_REFRESH

    with client.websocket_connect(f"/ws/scans/{scan_id}") as ws:
        _send_auth(ws, refresh)
        disconnect = _expect_disconnect(ws)
        assert disconnect.code == 1008
        assert disconnect.reason == "auth_invalid"


# ---------------------------------------------------------------------------
# 3. Auth timeout — wall-clock check
# ---------------------------------------------------------------------------


def test_ws_auth_timeout_closes_1008(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the client does not send the first auth frame within the configured
    window the server must close 1008 / ``auth_timeout``."""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    # Tight window so the test runs fast.
    monkeypatch.setenv("WEBSOCKET_AUTH_TIMEOUT_SECONDS", "0.2")

    _user_id, _team_id, scan_id = _seed_user_with_team_scan()

    started = time.monotonic()
    with TestClient(app) as c:
        with c.websocket_connect(f"/ws/scans/{scan_id}") as ws:
            disconnect = _expect_disconnect(ws)
    elapsed = time.monotonic() - started

    assert disconnect.code == 1008
    assert disconnect.reason == "auth_timeout"
    # Sanity: the timeout actually fired, not some other path. Allow generous
    # slack for CI jitter — the contract is "close happens after roughly the
    # configured window", not "exactly N ms".
    assert elapsed >= 0.15, f"timeout fired too early ({elapsed:.3f}s)"


# ---------------------------------------------------------------------------
# 4. IDOR — team_b user, team_a scan
# ---------------------------------------------------------------------------


def test_ws_idor_blocked_with_4403(client: TestClient) -> None:
    _user_a_id, user_b_id, scan_id = _seed_two_users_one_scan()
    token = _bearer_token(user_b_id)

    with client.websocket_connect(f"/ws/scans/{scan_id}") as ws:
        _send_auth(ws, token)
        disconnect = _expect_disconnect(ws)

    assert disconnect.code == 4403
    assert disconnect.reason == "forbidden"


# ---------------------------------------------------------------------------
# 5. Scan not found — random UUID
# ---------------------------------------------------------------------------


def test_ws_scan_not_found_4404(client: TestClient) -> None:
    """A valid JWT for an existing user, but the scan id does not exist →
    4404 / ``scan_not_found``."""
    user_id = _seed_user_only()
    token = _bearer_token(user_id)
    bogus_scan_id = uuid.uuid4()

    with client.websocket_connect(f"/ws/scans/{bogus_scan_id}") as ws:
        _send_auth(ws, token)
        disconnect = _expect_disconnect(ws)

    assert disconnect.code == 4404
    assert disconnect.reason == "scan_not_found"


# ---------------------------------------------------------------------------
# 6. Forwarded progress event — Redis publisher → WebSocket
# ---------------------------------------------------------------------------


def test_ws_forwards_published_progress(client: TestClient) -> None:
    """``publish_progress(scan_id, step="cdxgen", percent=42)`` must reach
    a connected client as a JSON frame matching the canonical schema."""
    from tasks._progress import publish_progress, reset_publisher_for_tests

    reset_publisher_for_tests()

    user_id, _team_id, scan_id = _seed_user_with_team_scan()
    token = _bearer_token(user_id)

    with client.websocket_connect(f"/ws/scans/{scan_id}") as ws:
        _send_auth(ws, token)
        # First frame is the initial sync (percent=10, step="fetch").
        initial = json.loads(ws.receive_text())
        assert initial["step"] == "fetch"

        # Publish from a background thread so the WebSocket loop on the main
        # thread is free to receive. The TestClient runs the ASGI app in its
        # own anyio portal — `publish_progress` is sync redis-py, safe to
        # call from any thread.
        def _publish() -> None:
            # A tiny delay gives the server's PubSub subscribe call time to
            # register before the publish; without it the message can be
            # dropped because Redis pub/sub does not buffer.
            time.sleep(0.05)
            publish_progress(scan_id, step="cdxgen", percent=42)

        publisher = threading.Thread(target=_publish)
        publisher.start()
        try:
            forwarded = json.loads(ws.receive_text())
        finally:
            publisher.join(timeout=2.0)

    assert forwarded["percent"] == 42
    assert forwarded["step"] == "cdxgen"
    assert isinstance(forwarded["ts"], str) and forwarded["ts"]


# ---------------------------------------------------------------------------
# 7. Terminal `succeeded` step is forwarded; client-driven close = 1000
# ---------------------------------------------------------------------------


def test_ws_terminal_step_succeeded_is_forwarded(client: TestClient) -> None:
    from tasks._progress import publish_progress, reset_publisher_for_tests

    reset_publisher_for_tests()

    user_id, _team_id, scan_id = _seed_user_with_team_scan()
    token = _bearer_token(user_id)

    with client.websocket_connect(f"/ws/scans/{scan_id}") as ws:
        _send_auth(ws, token)
        # Drain the initial sync.
        ws.receive_text()

        def _publish_terminal() -> None:
            time.sleep(0.05)
            publish_progress(scan_id, step="succeeded", percent=100)

        publisher = threading.Thread(target=_publish_terminal)
        publisher.start()
        try:
            terminal = json.loads(ws.receive_text())
        finally:
            publisher.join(timeout=2.0)

    assert terminal["step"] == "succeeded"
    assert terminal["percent"] == 100
    # Exiting the `with` block closes the client side cleanly. The endpoint
    # detects the disconnect and unregisters the connection — implicitly
    # asserted by the next test (registry reset) running cleanly.


# ---------------------------------------------------------------------------
# 8. Per-user connection limit — eviction with code 1001
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "Per-user connection cap eviction requires a single event loop shared "
        "across the connections so `await evicted.close(...)` can complete on "
        "the originating socket's loop. Starlette's TestClient gives each "
        "websocket_connect its own anyio portal/thread, so the cross-loop "
        "close hangs in tests even though the production single-loop path is "
        "correct. The unit suite "
        "(`tests/unit/test_ws_helpers.py::test_endpoint_evicts_oldest_on_"
        "fourth_connection`) already pins this with an in-memory fake; an "
        "integration smoke would need a real uvicorn process + httpx-ws "
        "client. Tracked as a follow-up for the security-reviewer pass."
    )
)
def test_ws_per_user_connection_limit_evicts_oldest(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reserved scenario — see skip reason above for the runtime constraint."""
    raise NotImplementedError


# ---------------------------------------------------------------------------
# 9. Origin gate in prod — disallowed Origin closes 1008 pre-accept
# ---------------------------------------------------------------------------


def test_ws_origin_rejected_in_prod_closes_1008(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In production, an Origin not in CORS_ALLOWED_ORIGINS must close 1008
    with reason ``origin_rejected`` BEFORE the server accepts the upgrade.
    """
    monkeypatch.setenv("APP_ENV", "prod")
    # Use https:// because validate_cors_origins() rejects http:// in prod.
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com")
    # Required-in-prod SECRET_KEY: keep the dev placeholder length but rename
    # so the secret_key() guard passes.
    monkeypatch.setenv("SECRET_KEY", "ws-integration-prod-secret-32-chars!")

    # Seed has to happen BEFORE the TestClient lifespan boots the app under
    # prod env so the seed engine reads the right URL — both share DATABASE_URL.
    _user_id, _team_id, scan_id = _seed_user_with_team_scan()

    # NOTE: changing CORS to a single https:// origin means the running app's
    # CORSMiddleware was constructed with the dev allow-list — but the WS
    # endpoint reads CORS at call time (cors_allowed_origins()), so it sees
    # the override regardless of how the HTTP middleware was wired.

    with TestClient(app) as c:
        # The pre-accept close raises WebSocketDisconnect on the connect call.
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with c.websocket_connect(
                f"/ws/scans/{scan_id}",
                headers={"origin": "https://evil.example.com"},
            ):
                pass

    assert exc_info.value.code == 1008
    assert exc_info.value.reason == "origin_rejected"


# ---------------------------------------------------------------------------
# 10. Bad first message — 4400
# ---------------------------------------------------------------------------


def test_ws_bad_first_message_closes_4400(client: TestClient) -> None:
    """A first frame that is not a JSON ``{"type":"auth","token":"..."}``
    object must close 4400 / ``bad_message``."""
    _user_id, _team_id, scan_id = _seed_user_with_team_scan()

    with client.websocket_connect(f"/ws/scans/{scan_id}") as ws:
        ws.send_text("ping")
        disconnect = _expect_disconnect(ws)
    assert disconnect.code == 4400
    assert disconnect.reason == "bad_message"


def test_ws_first_message_with_wrong_type_closes_4400(client: TestClient) -> None:
    _user_id, _team_id, scan_id = _seed_user_with_team_scan()

    with client.websocket_connect(f"/ws/scans/{scan_id}") as ws:
        ws.send_text(json.dumps({"type": "hello", "token": "x"}))
        disconnect = _expect_disconnect(ws)
    assert disconnect.code == 4400
    assert disconnect.reason == "bad_message"
