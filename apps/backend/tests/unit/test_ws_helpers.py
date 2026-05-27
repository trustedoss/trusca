"""
Unit tests for the WebSocket gateway helpers (PR #9 task 2.9).

These tests stay inside the helper surface: pure parsing, origin gates,
close code constants, the per-user connection registry, and the
`scan_progress_channel` config helper. The full WebSocket lifecycle
(JWT decode → IDOR → Redis subscribe → forward) is covered by the
integration tests written by the test-writer agent — those tests need a
real Postgres + Redis and an HTTP/WS test client.

Coverage target: ≥ 80 % of `apps/backend/api/v1/ws.py` exercised here so
the dedicated helpers do not regress.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# parse_auth_message
# ---------------------------------------------------------------------------


def test_parse_auth_message_extracts_token_from_well_formed_payload() -> None:
    from api.v1.ws import parse_auth_message

    raw = json.dumps({"type": "auth", "token": "eyJ-fake.jwt.value"})
    assert parse_auth_message(raw) == "eyJ-fake.jwt.value"


def test_parse_auth_message_rejects_non_json_text() -> None:
    from api.v1.ws import parse_auth_message

    with pytest.raises(ValueError):
        parse_auth_message("ping")


def test_parse_auth_message_rejects_json_array() -> None:
    from api.v1.ws import parse_auth_message

    with pytest.raises(ValueError):
        parse_auth_message(json.dumps(["auth", "token"]))


def test_parse_auth_message_rejects_wrong_type() -> None:
    from api.v1.ws import parse_auth_message

    raw = json.dumps({"type": "ping", "token": "abc"})
    with pytest.raises(ValueError):
        parse_auth_message(raw)


def test_parse_auth_message_rejects_missing_token() -> None:
    from api.v1.ws import parse_auth_message

    raw = json.dumps({"type": "auth"})
    with pytest.raises(ValueError):
        parse_auth_message(raw)


def test_parse_auth_message_rejects_empty_token() -> None:
    from api.v1.ws import parse_auth_message

    raw = json.dumps({"type": "auth", "token": ""})
    with pytest.raises(ValueError):
        parse_auth_message(raw)


# ---------------------------------------------------------------------------
# origin_allowed
# ---------------------------------------------------------------------------


def test_origin_allowed_passes_configured_origin_in_prod() -> None:
    from api.v1.ws import origin_allowed

    assert origin_allowed(
        "https://app.example.com",
        allowed=["https://app.example.com"],
        env="prod",
    )


def test_origin_allowed_rejects_unknown_origin_in_prod() -> None:
    from api.v1.ws import origin_allowed

    assert not origin_allowed(
        "https://evil.example.com",
        allowed=["https://app.example.com"],
        env="prod",
    )


def test_origin_allowed_rejects_blank_origin_in_prod() -> None:
    """A missing Origin header in production must be denied — only browsers
    are expected, and browsers always send Origin for the upgrade."""
    from api.v1.ws import origin_allowed

    assert not origin_allowed(None, allowed=["https://app.example.com"], env="prod")
    assert not origin_allowed("", allowed=["https://app.example.com"], env="prod")


def test_origin_allowed_permits_blank_origin_in_dev() -> None:
    """`wscat` and other CLI tools omit Origin; dev is allowed to be lax."""
    from api.v1.ws import origin_allowed

    assert origin_allowed(None, allowed=["http://localhost:5173"], env="dev")
    assert origin_allowed("", allowed=["http://localhost:5173"], env="dev")


# ---------------------------------------------------------------------------
# build_progress_frame
# ---------------------------------------------------------------------------


def test_build_progress_frame_emits_canonical_schema() -> None:
    from api.v1.ws import build_progress_frame

    frame = build_progress_frame(percent=42, step="cdxgen", ts="2026-05-06T12:00:00Z")
    body = json.loads(frame)
    assert body == {
        # P2 #8c — explicit type discriminator.
        "type": "progress",
        "percent": 42,
        "step": "cdxgen",
        "ts": "2026-05-06T12:00:00Z",
    }


def test_build_progress_frame_normalizes_missing_step_to_empty_string() -> None:
    from api.v1.ws import build_progress_frame

    frame = build_progress_frame(percent=0, step=None, ts="2026-05-06T12:00:00Z")
    body = json.loads(frame)
    assert body["step"] == ""


def test_build_progress_frame_auto_fills_ts_when_omitted() -> None:
    from api.v1.ws import build_progress_frame

    frame = build_progress_frame(percent=10, step="bootstrap")
    body = json.loads(frame)
    assert body["percent"] == 10
    assert body["step"] == "bootstrap"
    assert body["type"] == "progress"
    # Timestamp must be ISO 8601 with a Z suffix for UTC.
    assert isinstance(body["ts"], str)
    assert body["ts"].endswith("Z")


# ---------------------------------------------------------------------------
# P2 #8c — build_log_frame
# ---------------------------------------------------------------------------


def test_build_log_frame_emits_canonical_schema() -> None:
    """The log frame mirrors the progress frame shape, keyed by type='log'."""
    from api.v1.ws import build_log_frame

    frame = build_log_frame(
        stage="cdxgen",
        stream="stdout",
        line="resolving package tree…",
        ts="2026-05-06T12:00:00Z",
    )
    body = json.loads(frame)
    assert body == {
        "type": "log",
        "stage": "cdxgen",
        "stream": "stdout",
        "line": "resolving package tree…",
        "ts": "2026-05-06T12:00:00Z",
    }


def test_build_log_frame_auto_fills_ts_when_omitted() -> None:
    from api.v1.ws import build_log_frame

    frame = build_log_frame(stage="scancode", stream="stderr", line="boom")
    body = json.loads(frame)
    assert body["type"] == "log"
    assert body["stage"] == "scancode"
    assert body["stream"] == "stderr"
    assert body["line"] == "boom"
    assert isinstance(body["ts"], str) and body["ts"].endswith("Z")


# ---------------------------------------------------------------------------
# Per-user connection registry
# ---------------------------------------------------------------------------


def test_register_evicts_oldest_when_cap_exceeded() -> None:
    from api.v1.ws import (
        _register_connection,
        _reset_registry_for_tests,
        _unregister_connection,
    )

    _reset_registry_for_tests()
    user_id = uuid.uuid4()

    sock_a = MagicMock(name="ws_a")
    sock_b = MagicMock(name="ws_b")
    sock_c = MagicMock(name="ws_c")
    sock_d = MagicMock(name="ws_d")

    async def _exercise() -> None:
        # Cap=3: first three slot in cleanly, fourth evicts sock_a.
        assert await _register_connection(user_id, sock_a, max_per_user=3) is None
        assert await _register_connection(user_id, sock_b, max_per_user=3) is None
        assert await _register_connection(user_id, sock_c, max_per_user=3) is None
        evicted = await _register_connection(user_id, sock_d, max_per_user=3)
        assert evicted is sock_a

        # Unregister cleans up; double-removal is safe.
        await _unregister_connection(user_id, sock_b)
        await _unregister_connection(user_id, sock_b)

    asyncio.run(_exercise())


def test_register_with_cap_one_evicts_each_previous() -> None:
    from api.v1.ws import _register_connection, _reset_registry_for_tests

    _reset_registry_for_tests()
    user_id = uuid.uuid4()
    sock_a = MagicMock(name="a")
    sock_b = MagicMock(name="b")

    async def _exercise() -> None:
        assert await _register_connection(user_id, sock_a, max_per_user=1) is None
        evicted = await _register_connection(user_id, sock_b, max_per_user=1)
        assert evicted is sock_a

    asyncio.run(_exercise())


def test_register_isolates_users_from_each_other() -> None:
    from api.v1.ws import _register_connection, _reset_registry_for_tests

    _reset_registry_for_tests()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    sock_a = MagicMock()
    sock_b = MagicMock()

    async def _exercise() -> None:
        assert await _register_connection(user_a, sock_a, max_per_user=1) is None
        # user_b has its own bucket — it should NOT evict user_a's socket.
        assert await _register_connection(user_b, sock_b, max_per_user=1) is None

    asyncio.run(_exercise())


# ---------------------------------------------------------------------------
# Close codes are stable constants
# ---------------------------------------------------------------------------


def test_close_codes_match_spec() -> None:
    """The frontend hardcodes these codes — changing them is a breaking
    change. Pin them in a unit test so accidental tweaks fail CI."""
    from api.v1 import ws

    assert ws.WS_CLOSE_NORMAL == 1000
    assert ws.WS_CLOSE_GOING_AWAY == 1001
    assert ws.WS_CLOSE_POLICY_VIOLATION == 1008
    assert ws.WS_CLOSE_INTERNAL == 1011
    assert ws.WS_CLOSE_BAD_MESSAGE == 4400
    assert ws.WS_CLOSE_FORBIDDEN == 4403
    assert ws.WS_CLOSE_NOT_FOUND == 4404


# ---------------------------------------------------------------------------
# scan_progress_channel + websocket_* config getters (CLAUDE.md rule #11)
# ---------------------------------------------------------------------------


def test_scan_progress_channel_uses_canonical_format() -> None:
    from core.config import scan_progress_channel

    sid = "00000000-0000-0000-0000-000000000001"
    assert scan_progress_channel(sid) == f"scan:{sid}:progress"


def test_websocket_max_connections_per_user_reads_env_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLAUDE.md core rule #11 — env access must be runtime, not import-time.

    Setting the env var AFTER the import and calling the getter must yield
    the new value (no module-level caching).
    """
    from core.config import websocket_max_connections_per_user

    monkeypatch.setenv("WEBSOCKET_MAX_CONNECTIONS_PER_USER", "7")
    assert websocket_max_connections_per_user() == 7
    monkeypatch.setenv("WEBSOCKET_MAX_CONNECTIONS_PER_USER", "1")
    assert websocket_max_connections_per_user() == 1


def test_websocket_max_connections_per_user_default_is_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import websocket_max_connections_per_user

    monkeypatch.delenv("WEBSOCKET_MAX_CONNECTIONS_PER_USER", raising=False)
    assert websocket_max_connections_per_user() == 3


def test_websocket_auth_timeout_seconds_reads_env_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import websocket_auth_timeout_seconds

    monkeypatch.setenv("WEBSOCKET_AUTH_TIMEOUT_SECONDS", "2.5")
    assert websocket_auth_timeout_seconds() == pytest.approx(2.5)
    monkeypatch.delenv("WEBSOCKET_AUTH_TIMEOUT_SECONDS", raising=False)
    assert websocket_auth_timeout_seconds() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Router registration smoke
# ---------------------------------------------------------------------------


def test_router_exposes_websocket_route() -> None:
    """The router must declare exactly one WebSocket route at the expected
    path. Confirms `app.include_router(ws_router)` will pick it up."""
    from api.v1 import ws_router

    paths = {getattr(r, "path", None) for r in ws_router.routes}
    assert "/ws/scans/{scan_id}" in paths


# ---------------------------------------------------------------------------
# scan_progress_endpoint — drive the full lifecycle with stubs.
#
# These tests exercise the FastAPI WebSocket route function directly with a
# minimal fake WebSocket so we cover origin gating, auth-message handling,
# JWT failure, IDOR, initial sync, and the Redis subscribe loop without
# needing a real client / broker. The integration tests (test-writer) will
# add a Postgres+Redis-backed end-to-end smoke later.
# ---------------------------------------------------------------------------


class _FakeWebSocket:
    """Minimal WebSocket double for the route function.

    Records what the endpoint did (accept, send_text, close) so each test
    can assert on the close code and forwarded frames.
    """

    def __init__(
        self,
        *,
        origin: str | None,
        incoming: list[str] | None = None,
        receive_exceptions: list[BaseException] | None = None,
        app: Any = None,
    ) -> None:
        from starlette.datastructures import Headers

        headers_dict: dict[str, str] = {}
        if origin is not None:
            headers_dict["origin"] = origin
        self.headers = Headers(headers_dict)
        self.client = None
        self.app = app

        self._incoming = list(incoming or [])
        self._receive_exceptions = list(receive_exceptions or [])

        self.accepted = False
        self.sent: list[str] = []
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self.closed = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        if self._receive_exceptions:
            raise self._receive_exceptions.pop(0)
        if self._incoming:
            return self._incoming.pop(0)
        # Default: simulate disconnect.
        from fastapi import WebSocketDisconnect

        raise WebSocketDisconnect(code=1000)

    async def send_text(self, data: str) -> None:
        if self.closed:
            from fastapi import WebSocketDisconnect

            raise WebSocketDisconnect(code=1000)
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.close_code = code
        self.close_reason = reason
        self.closed = True


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_endpoint_rejects_disallowed_origin_before_accept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.v1.ws import (
        REASON_ORIGIN_REJECTED,
        WS_CLOSE_POLICY_VIOLATION,
        scan_progress_endpoint,
    )

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com")

    ws = _FakeWebSocket(origin="https://evil.example.com")
    _run(scan_progress_endpoint(ws, str(uuid.uuid4())))  # type: ignore[arg-type]

    assert ws.accepted is False  # closed pre-accept
    assert ws.close_code == WS_CLOSE_POLICY_VIOLATION
    assert ws.close_reason == REASON_ORIGIN_REJECTED


def test_endpoint_rejects_invalid_scan_id_with_4404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.v1.ws import REASON_SCAN_NOT_FOUND, WS_CLOSE_NOT_FOUND, scan_progress_endpoint

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")

    ws = _FakeWebSocket(origin=None)
    _run(scan_progress_endpoint(ws, "not-a-uuid"))  # type: ignore[arg-type]

    assert ws.accepted is True
    assert ws.close_code == WS_CLOSE_NOT_FOUND
    assert ws.close_reason == REASON_SCAN_NOT_FOUND


def test_endpoint_closes_on_auth_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1.ws import REASON_AUTH_TIMEOUT, WS_CLOSE_POLICY_VIOLATION, scan_progress_endpoint

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    # Set a very short timeout so the test runs fast.
    monkeypatch.setenv("WEBSOCKET_AUTH_TIMEOUT_SECONDS", "0.05")

    # No incoming frames AND no exception → receive_text default raises
    # WebSocketDisconnect, but with a long-running future the wait_for
    # itself times out. We intercept by stubbing _await_first_frame.
    async def _raise_timeout(*_args: Any, **_kwargs: Any) -> str:
        raise TimeoutError

    monkeypatch.setattr("api.v1.ws._await_first_frame", _raise_timeout)

    ws = _FakeWebSocket(origin=None)
    _run(scan_progress_endpoint(ws, str(uuid.uuid4())))  # type: ignore[arg-type]

    assert ws.close_code == WS_CLOSE_POLICY_VIOLATION
    assert ws.close_reason == REASON_AUTH_TIMEOUT


def test_endpoint_closes_on_first_frame_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the peer closes BEFORE the auth frame the endpoint exits silently."""
    from fastapi import WebSocketDisconnect

    from api.v1.ws import scan_progress_endpoint

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")

    async def _raise_disconnect(*_args: Any, **_kwargs: Any) -> str:
        raise WebSocketDisconnect(code=1000)

    monkeypatch.setattr("api.v1.ws._await_first_frame", _raise_disconnect)

    ws = _FakeWebSocket(origin=None)
    _run(scan_progress_endpoint(ws, str(uuid.uuid4())))  # type: ignore[arg-type]

    # No close call needed because the client already disconnected — but
    # `close_code` stays None to prove we did not over-close.
    assert ws.close_code is None


def test_endpoint_closes_4400_on_malformed_first_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.v1.ws import REASON_BAD_MESSAGE, WS_CLOSE_BAD_MESSAGE, scan_progress_endpoint

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")

    ws = _FakeWebSocket(origin=None, incoming=["this is not json"])
    _run(scan_progress_endpoint(ws, str(uuid.uuid4())))  # type: ignore[arg-type]

    assert ws.accepted is True
    assert ws.close_code == WS_CLOSE_BAD_MESSAGE
    assert ws.close_reason == REASON_BAD_MESSAGE


def test_endpoint_closes_1008_on_invalid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jose import JWTError

    from api.v1.ws import REASON_AUTH_INVALID, WS_CLOSE_POLICY_VIOLATION, scan_progress_endpoint

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")

    def _reject(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise JWTError("bad token")

    monkeypatch.setattr("api.v1.ws.decode_token", _reject)

    ws = _FakeWebSocket(
        origin=None,
        incoming=[json.dumps({"type": "auth", "token": "garbage"})],
    )
    _run(scan_progress_endpoint(ws, str(uuid.uuid4())))  # type: ignore[arg-type]

    assert ws.close_code == WS_CLOSE_POLICY_VIOLATION
    assert ws.close_reason == REASON_AUTH_INVALID


def test_endpoint_closes_1008_when_sub_is_not_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.v1.ws import REASON_AUTH_INVALID, WS_CLOSE_POLICY_VIOLATION, scan_progress_endpoint

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")

    monkeypatch.setattr(
        "api.v1.ws.decode_token",
        lambda *args, **kwargs: {"sub": "not-a-uuid", "type": "access"},
    )

    ws = _FakeWebSocket(
        origin=None,
        incoming=[json.dumps({"type": "auth", "token": "x"})],
    )
    _run(scan_progress_endpoint(ws, str(uuid.uuid4())))  # type: ignore[arg-type]

    assert ws.close_code == WS_CLOSE_POLICY_VIOLATION
    assert ws.close_reason == REASON_AUTH_INVALID


# ---------------------------------------------------------------------------
# Helper to build an "authenticated" path through the endpoint.
# ---------------------------------------------------------------------------


class _FakeUser:
    """Stand-in that quacks like CurrentUser for the endpoint's needs."""

    def __init__(self, *, is_active: bool = True) -> None:
        self.id = uuid.uuid4()
        self.email = "u@example.com"
        self.role = "developer"
        self.team_ids: list[uuid.UUID] = []
        self.team_roles: dict[uuid.UUID, str] = {}
        self.is_active = is_active
        self.is_superuser = False


class _FakeScan:
    def __init__(
        self,
        *,
        percent: int = 25,
        step: str | None = "cdxgen",
        status: str = "running",
    ) -> None:
        self.id = uuid.uuid4()
        self.progress_percent = percent
        self.current_step = step
        # P1 #11 — the gateway's initial-sync builder now reads `scan.status`
        # to rewrite a terminal row's step to the terminal verdict. Default to
        # "running" so the bulk of these tests exercise the live-stream path
        # unchanged; the terminal branches have dedicated integration tests in
        # tests/integration/test_ws_scan_progress.py.
        self.status = status


class _FakeSessionCM:
    """Mimic an `async with session_factory() as session:` context."""

    async def __aenter__(self) -> _FakeSessionCM:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None


def _patch_authenticated_path(
    monkeypatch: pytest.MonkeyPatch,
    *,
    user: _FakeUser,
    get_scan_result: _FakeScan | BaseException,
) -> None:
    """Wire up the typical "auth passes" stubs."""
    monkeypatch.setattr(
        "api.v1.ws.decode_token",
        lambda *args, **kwargs: {"sub": str(user.id), "type": "access"},
    )

    async def _resolve(_session: Any, _user_id: uuid.UUID) -> _FakeUser:
        return user

    monkeypatch.setattr("api.v1.ws._resolve_user", _resolve)

    def _factory_factory(_app: Any) -> Any:
        def _make_session() -> _FakeSessionCM:
            return _FakeSessionCM()

        return _make_session

    monkeypatch.setattr("api.v1.ws._session_factory", _factory_factory)

    async def _get_scan(_session: Any, *, scan_id: uuid.UUID, actor: Any) -> _FakeScan:
        if isinstance(get_scan_result, BaseException):
            raise get_scan_result
        return get_scan_result

    monkeypatch.setattr("api.v1.ws.get_scan", _get_scan)


def test_endpoint_closes_4404_when_scan_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.v1.ws import (
        REASON_SCAN_NOT_FOUND,
        WS_CLOSE_NOT_FOUND,
        _reset_registry_for_tests,
        scan_progress_endpoint,
    )
    from services.scan_service import ScanNotFound

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    _reset_registry_for_tests()

    user = _FakeUser()
    _patch_authenticated_path(monkeypatch, user=user, get_scan_result=ScanNotFound("nope"))

    ws = _FakeWebSocket(
        origin=None,
        incoming=[json.dumps({"type": "auth", "token": "x"})],
    )
    _run(scan_progress_endpoint(ws, str(uuid.uuid4())))  # type: ignore[arg-type]

    assert ws.close_code == WS_CLOSE_NOT_FOUND
    assert ws.close_reason == REASON_SCAN_NOT_FOUND


def test_endpoint_closes_4403_on_idor(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1.ws import (
        REASON_FORBIDDEN,
        WS_CLOSE_FORBIDDEN,
        _reset_registry_for_tests,
        scan_progress_endpoint,
    )
    from services.scan_service import ScanForbidden

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    _reset_registry_for_tests()

    user = _FakeUser()
    _patch_authenticated_path(monkeypatch, user=user, get_scan_result=ScanForbidden("nope"))

    ws = _FakeWebSocket(
        origin=None,
        incoming=[json.dumps({"type": "auth", "token": "x"})],
    )
    _run(scan_progress_endpoint(ws, str(uuid.uuid4())))  # type: ignore[arg-type]

    assert ws.close_code == WS_CLOSE_FORBIDDEN
    assert ws.close_reason == REASON_FORBIDDEN


def test_endpoint_closes_1008_when_user_inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.v1.ws import (
        REASON_AUTH_INACTIVE,
        WS_CLOSE_POLICY_VIOLATION,
        _reset_registry_for_tests,
        scan_progress_endpoint,
    )

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    _reset_registry_for_tests()

    user = _FakeUser(is_active=False)
    monkeypatch.setattr(
        "api.v1.ws.decode_token",
        lambda *args, **kwargs: {"sub": str(user.id), "type": "access"},
    )

    async def _resolve(_session: Any, _user_id: uuid.UUID) -> _FakeUser:
        return user

    monkeypatch.setattr("api.v1.ws._resolve_user", _resolve)
    monkeypatch.setattr(
        "api.v1.ws._session_factory",
        lambda _app: (lambda: _FakeSessionCM()),
    )

    ws = _FakeWebSocket(
        origin=None,
        incoming=[json.dumps({"type": "auth", "token": "x"})],
    )
    _run(scan_progress_endpoint(ws, str(uuid.uuid4())))  # type: ignore[arg-type]

    assert ws.close_code == WS_CLOSE_POLICY_VIOLATION
    assert ws.close_reason == REASON_AUTH_INACTIVE


def test_endpoint_pushes_initial_sync_then_forwards_pubsub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: auth passes, initial frame is the current row, then a
    single pubsub message is forwarded verbatim before the loop exits."""
    from contextlib import asynccontextmanager

    from api.v1.ws import (
        WS_CLOSE_NORMAL,
        _reset_registry_for_tests,
        scan_progress_endpoint,
    )

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    _reset_registry_for_tests()

    user = _FakeUser()
    scan = _FakeScan(percent=42, step="cdxgen")
    _patch_authenticated_path(monkeypatch, user=user, get_scan_result=scan)

    # Build a fake pubsub that yields one message then raises StopAsyncIteration.
    class _FakePubSub:
        def listen(self) -> Any:
            async def _gen() -> Any:
                yield {
                    "type": "message",
                    "data": b'{"percent":50,"step":"ort","ts":"2026-05-06T12:00:00Z"}',
                }
                # Yield a non-message frame to exercise the filter.
                yield {"type": "subscribe", "data": b"ignored"}

            return _gen()

    @asynccontextmanager
    async def _fake_pubsub(_channel: str) -> AsyncIterator[_FakePubSub]:
        yield _FakePubSub()

    monkeypatch.setattr("api.v1.ws._redis_pubsub", _fake_pubsub)

    ws = _FakeWebSocket(
        origin=None,
        incoming=[json.dumps({"type": "auth", "token": "x"})],
    )
    _run(scan_progress_endpoint(ws, str(uuid.uuid4())))  # type: ignore[arg-type]

    # First frame is the initial sync (percent/step from scan row).
    assert len(ws.sent) >= 1
    initial = json.loads(ws.sent[0])
    assert initial["percent"] == 42
    assert initial["step"] == "cdxgen"

    # The pubsub message is forwarded verbatim — note we do NOT re-serialize.
    assert any('"percent":50' in s for s in ws.sent[1:])

    # The loop ended naturally (StopAsyncIteration from the generator) — no
    # explicit close needed because pubsub.listen() returned.
    assert ws.close_code in (None, WS_CLOSE_NORMAL)


def test_endpoint_recovers_from_internal_redis_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis errors during subscribe → close 1011 (internal)."""
    from contextlib import asynccontextmanager

    from api.v1.ws import (
        REASON_INTERNAL,
        WS_CLOSE_INTERNAL,
        _reset_registry_for_tests,
        scan_progress_endpoint,
    )

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    _reset_registry_for_tests()

    user = _FakeUser()
    scan = _FakeScan()
    _patch_authenticated_path(monkeypatch, user=user, get_scan_result=scan)

    @asynccontextmanager
    async def _broken_pubsub(_channel: str) -> AsyncIterator[Any]:
        raise RuntimeError("redis is on fire")
        yield None  # pragma: no cover — keep mypy happy

    monkeypatch.setattr("api.v1.ws._redis_pubsub", _broken_pubsub)

    ws = _FakeWebSocket(
        origin=None,
        incoming=[json.dumps({"type": "auth", "token": "x"})],
    )
    _run(scan_progress_endpoint(ws, str(uuid.uuid4())))  # type: ignore[arg-type]

    assert ws.close_code == WS_CLOSE_INTERNAL
    assert ws.close_reason == REASON_INTERNAL


def test_endpoint_evicts_oldest_on_fourth_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 4th connection (cap=3) must evict the oldest with code 1001."""
    from contextlib import asynccontextmanager

    from api.v1.ws import (
        WS_CLOSE_GOING_AWAY,
        _register_connection,
        _reset_registry_for_tests,
        scan_progress_endpoint,
    )

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("WEBSOCKET_MAX_CONNECTIONS_PER_USER", "3")
    _reset_registry_for_tests()

    user = _FakeUser()
    scan = _FakeScan()
    _patch_authenticated_path(monkeypatch, user=user, get_scan_result=scan)

    @asynccontextmanager
    async def _empty_pubsub(_channel: str) -> AsyncIterator[Any]:
        class _NoOp:
            def listen(self) -> Any:
                async def _gen() -> Any:
                    return
                    yield  # pragma: no cover

                return _gen()

        yield _NoOp()

    monkeypatch.setattr("api.v1.ws._redis_pubsub", _empty_pubsub)

    # Pre-seed three older "open" sockets attributed to the same user.
    # `close` must be awaitable — the endpoint awaits the eviction.
    older_a = MagicMock(name="older_a")
    older_a.close = AsyncMock()
    older_b = MagicMock(name="older_b")
    older_b.close = AsyncMock()
    older_c = MagicMock(name="older_c")
    older_c.close = AsyncMock()

    async def _seed() -> None:
        await _register_connection(user.id, older_a, max_per_user=3)
        await _register_connection(user.id, older_b, max_per_user=3)
        await _register_connection(user.id, older_c, max_per_user=3)

    asyncio.run(_seed())

    # Fourth connection arrives via the endpoint.
    ws = _FakeWebSocket(
        origin=None,
        incoming=[json.dumps({"type": "auth", "token": "x"})],
    )
    _run(scan_progress_endpoint(ws, str(uuid.uuid4())))  # type: ignore[arg-type]

    # The oldest seeded socket should have been close()'d with going_away.
    older_a.close.assert_awaited_once()
    args, kwargs = older_a.close.call_args
    assert kwargs.get("code", args[0] if args else None) == WS_CLOSE_GOING_AWAY


def test_endpoint_handles_disconnect_during_initial_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the peer hangs up between accept and initial-sync send, the
    endpoint exits cleanly without raising."""
    from api.v1.ws import _reset_registry_for_tests, scan_progress_endpoint

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    _reset_registry_for_tests()

    user = _FakeUser()
    scan = _FakeScan()
    _patch_authenticated_path(monkeypatch, user=user, get_scan_result=scan)

    ws = _FakeWebSocket(
        origin=None,
        incoming=[json.dumps({"type": "auth", "token": "x"})],
    )
    # Mark closed BEFORE send_text — the FakeWebSocket raises on send.
    ws.closed = True

    _run(scan_progress_endpoint(ws, str(uuid.uuid4())))  # type: ignore[arg-type]

    # Endpoint should have unwound silently — the only assertion is that
    # we did not raise and the connection registry is clean.
    from api.v1.ws import _registry

    assert user.id not in _registry.connections


def test_endpoint_forwards_string_pubsub_payload_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pubsub may deliver str payloads (decode_responses=True clients);
    the gateway must accept both bytes and str."""
    from contextlib import asynccontextmanager

    from api.v1.ws import _reset_registry_for_tests, scan_progress_endpoint

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    _reset_registry_for_tests()

    user = _FakeUser()
    scan = _FakeScan(percent=10, step="bootstrap")
    _patch_authenticated_path(monkeypatch, user=user, get_scan_result=scan)

    payload_text = '{"percent":99,"step":"finalize","ts":"2026-05-06T12:00:00Z"}'

    class _PubSub:
        def listen(self) -> Any:
            async def _gen() -> Any:
                yield {"type": "message", "data": payload_text}
                yield {"type": "message", "data": 12345}  # unknown type → skipped

            return _gen()

    @asynccontextmanager
    async def _pubsub(_channel: str) -> AsyncIterator[Any]:
        yield _PubSub()

    monkeypatch.setattr("api.v1.ws._redis_pubsub", _pubsub)

    ws = _FakeWebSocket(
        origin=None,
        incoming=[json.dumps({"type": "auth", "token": "x"})],
    )
    _run(scan_progress_endpoint(ws, str(uuid.uuid4())))  # type: ignore[arg-type]

    # Initial frame + one forwarded text frame; the int payload is skipped.
    assert payload_text in ws.sent
