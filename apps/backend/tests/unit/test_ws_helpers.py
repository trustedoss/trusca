"""
Unit tests for the WebSocket gateway helpers (PR #9 task 2.9).

These tests stay inside the helper surface: pure parsing, origin gates,
close code constants, the per-user connection registry, and the
`scan_progress_channel` config helper. The full WebSocket lifecycle
(JWT decode → IDOR → Redis subscribe → forward) is covered by the
integration tests written by the integration tests — those tests need a
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
# Per-user / global connection registry (W4): the ZSET logic itself lives in
# core.ws_registry and is covered by tests/unit/test_ws_registry.py. What
# stays here is the endpoint-level integration of that module, further down
# in this file (see "Connection caps (W4)").
# ---------------------------------------------------------------------------


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
    # W4: deliberately not 1008 (see api.v1.ws module docstring): the
    # frontend's close handler treats 1008 as an expired session.
    assert ws.WS_CLOSE_CAPACITY == 4429


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


def test_websocket_max_connections_per_user_default_is_eight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W4: raised from 3. The scan detail page opens two sockets per open
    tab, so the old default of 3 evicted a legitimate second tab's first
    socket; 8 covers four tabs open at once."""
    from core.config import websocket_max_connections_per_user

    monkeypatch.delenv("WEBSOCKET_MAX_CONNECTIONS_PER_USER", raising=False)
    assert websocket_max_connections_per_user() == 8


def test_websocket_max_connections_global_reads_env_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import websocket_max_connections_global

    monkeypatch.setenv("WEBSOCKET_MAX_CONNECTIONS_GLOBAL", "42")
    assert websocket_max_connections_global() == 42
    monkeypatch.setenv("WEBSOCKET_MAX_CONNECTIONS_GLOBAL", "9")
    assert websocket_max_connections_global() == 9


def test_websocket_max_connections_global_default_is_five_hundred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import websocket_max_connections_global

    monkeypatch.delenv("WEBSOCKET_MAX_CONNECTIONS_GLOBAL", raising=False)
    assert websocket_max_connections_global() == 500


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
# needing a real client / broker. The integration tests (integration tests) will
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


# ---------------------------------------------------------------------------
# W4: fake Redis broker shared across two or more `scan_progress_endpoint`
# calls in the same test.
#
# This is what makes "simulate multiple worker processes" possible without
# a real multi-process test harness: since `core.ws_registry` moved every
# bit of cap-enforcement state OUT of `api.v1.ws` module globals and into
# whatever object `_redis_pubsub` hands back, two `scan_progress_endpoint`
# calls in the same test process share NOTHING but this one `_FakeBroker`
# instance, exactly as two real uvicorn workers would share nothing but a
# real Redis instance. A test that proves correctness across two of these
# asyncio tasks is proving the same property a real multi-process deployment
# needs, because the code path does not know or care which one it is in.
# ---------------------------------------------------------------------------


class _FakeBroker:
    """In-memory stand-in for the slice of Redis `core.ws_registry` and the
    W4 forward loop use: ZSETs for connect-order (zadd/zcard/zrange/zrem),
    TTL-backed string keys for liveness (set/mget/delete, matching the real
    module's split; see `core.ws_registry` docstring for why the two are
    kept separate), and pub/sub (subscribe/publish/get_message)."""

    def __init__(self) -> None:
        self._zsets: dict[str, dict[str, float]] = {}
        # value is the wall-clock expiry (time.time() + ex); a key is "alive"
        # iff present AND not yet expired, mirroring Redis's own EX behaviour.
        self._strings: dict[str, float] = {}
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    def client(self) -> _FakeRedisClient:
        return _FakeRedisClient(self)


class _FakeRedisClient:
    def __init__(self, broker: _FakeBroker) -> None:
        self._broker = broker

    async def zadd(self, name: str, mapping: dict[str, float]) -> None:
        zset = self._broker._zsets.setdefault(name, {})
        zset.update(mapping)

    async def zcard(self, name: str) -> int:
        return len(self._broker._zsets.get(name, {}))

    async def zrange(self, name: str, start: int, end: int) -> list[str]:
        ordered = sorted(self._broker._zsets.get(name, {}).items(), key=lambda kv: kv[1])
        members = [member for member, _score in ordered]
        if end == -1:
            return members[start:]
        return members[start : end + 1]

    async def zrem(self, name: str, *values: str) -> None:
        zset = self._broker._zsets.get(name, {})
        for value in values:
            zset.pop(value, None)

    async def set(self, name: str, value: str, *, ex: int) -> None:
        import time as _time

        self._broker._strings[name] = _time.time() + ex

    async def mget(self, names: list[str]) -> list[str | None]:
        import time as _time

        now = _time.time()
        result: list[str | None] = []
        for name in names:
            expiry = self._broker._strings.get(name)
            result.append("1" if expiry is not None and expiry > now else None)
        return result

    async def delete(self, *names: str) -> None:
        for name in names:
            self._broker._strings.pop(name, None)

    async def publish(self, channel: str, message: str) -> None:
        for queue in self._broker._subscribers.get(channel, []):
            await queue.put({"type": "message", "channel": channel, "data": message})

    def pubsub(self) -> _FakePubSubHandle:
        return _FakePubSubHandle(self._broker)

    async def aclose(self) -> None:
        return None


class _FakePubSubHandle:
    def __init__(self, broker: _FakeBroker) -> None:
        self._broker = broker
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._channels: list[str] = []

    async def subscribe(self, *channels: str) -> None:
        for channel in channels:
            self._channels.append(channel)
            self._broker._subscribers.setdefault(channel, []).append(self._queue)

    async def unsubscribe(self, *channels: str) -> None:
        for channel in channels:
            subs = self._broker._subscribers.get(channel, [])
            if self._queue in subs:
                subs.remove(self._queue)

    async def aclose(self) -> None:
        return None

    async def get_message(
        self, *, ignore_subscribe_messages: bool = True, timeout: float = 1.0
    ) -> dict[str, Any] | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except TimeoutError:
            return None


def _fake_redis_pubsub(broker: _FakeBroker) -> Any:
    """Build a `_redis_pubsub`-shaped async context manager bound to `broker`.

    Matches the real `api.v1.ws._redis_pubsub(channels) -> (client, pubsub)`
    signature so it drops in via `monkeypatch.setattr`.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _cm(channels: tuple[str, ...]) -> AsyncIterator[tuple[Any, Any]]:
        client = broker.client()
        pubsub = client.pubsub()
        await pubsub.subscribe(*channels)
        try:
            yield client, pubsub
        finally:
            await pubsub.unsubscribe(*channels)

    return _cm


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

    async def _resolve(
        _session: Any, _user_id: uuid.UUID, *, issued_at: int | None = None
    ) -> _FakeUser:
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
        scan_progress_endpoint,
    )
    from services.scan_service import ScanNotFound

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")

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
        scan_progress_endpoint,
    )
    from services.scan_service import ScanForbidden

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")

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
        scan_progress_endpoint,
    )

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")

    user = _FakeUser(is_active=False)
    monkeypatch.setattr(
        "api.v1.ws.decode_token",
        lambda *args, **kwargs: {"sub": str(user.id), "type": "access"},
    )

    async def _resolve(
        _session: Any, _user_id: uuid.UUID, *, issued_at: int | None = None
    ) -> _FakeUser:
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


async def _open_concurrent(
    sockets: list[_FakeWebSocket], scan_id: str, *, stagger: float = 0.02
) -> list[asyncio.Task[None]]:
    """Launch `scan_progress_endpoint` for each socket as an independent
    asyncio task, staggered so registration order is deterministic (the
    W4 eviction pick is score-ordered by connect time)."""
    from api.v1.ws import scan_progress_endpoint

    tasks: list[asyncio.Task[None]] = []
    for ws in sockets:
        tasks.append(asyncio.create_task(scan_progress_endpoint(ws, scan_id)))  # type: ignore[arg-type]
        await asyncio.sleep(stagger)
    return tasks


async def _settle_and_cancel(tasks: list[asyncio.Task[None]], *, settle: float = 0.1) -> None:
    """Give every task a chance to reach its steady-state (registered, and
    either forwarding or self-evicted), then cancel whatever is still
    running, a connection that survives to this point is, by construction,
    one the cap never touched."""
    await asyncio.sleep(settle)
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def test_endpoint_pushes_initial_sync_then_forwards_pubsub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: auth passes, initial frame is the current row, then a
    published progress event is forwarded verbatim."""
    from core.config import scan_progress_channel

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    monkeypatch.setattr("api.v1.ws.WEBSOCKET_PRESENCE_HEARTBEAT_SECONDS", 0.05)

    user = _FakeUser()
    scan = _FakeScan(percent=42, step="cdxgen")
    _patch_authenticated_path(monkeypatch, user=user, get_scan_result=scan)

    broker = _FakeBroker()
    monkeypatch.setattr("api.v1.ws._redis_pubsub", _fake_redis_pubsub(broker))

    scan_id = str(uuid.uuid4())
    ws = _FakeWebSocket(
        origin=None,
        incoming=[json.dumps({"type": "auth", "token": "x"})],
    )

    async def _scenario() -> None:
        tasks = await _open_concurrent([ws], scan_id, stagger=0.0)
        await asyncio.sleep(0.02)  # let auth + registration + initial push land
        await broker.client().publish(
            scan_progress_channel(scan_id),
            '{"percent":50,"step":"ort","ts":"2026-05-06T12:00:00Z"}',
        )
        await asyncio.sleep(0.05)  # let the forward loop pick it up
        await _settle_and_cancel(tasks, settle=0.0)

    asyncio.run(_scenario())

    # First frame is the initial sync (percent/step from scan row).
    assert len(ws.sent) >= 1
    initial = json.loads(ws.sent[0])
    assert initial["percent"] == 42
    assert initial["step"] == "cdxgen"

    # The pubsub message is forwarded verbatim.
    assert any('"percent":50' in s for s in ws.sent[1:])

    # Cancelled while still open, nothing evicted or errored it.
    assert ws.close_code is None


def test_endpoint_recovers_from_internal_redis_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis errors while opening the client/subscription → close 1011."""
    from contextlib import asynccontextmanager

    from api.v1.ws import (
        REASON_INTERNAL,
        WS_CLOSE_INTERNAL,
        scan_progress_endpoint,
    )

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")

    user = _FakeUser()
    scan = _FakeScan()
    _patch_authenticated_path(monkeypatch, user=user, get_scan_result=scan)

    @asynccontextmanager
    async def _broken_pubsub(_channels: tuple[str, ...]) -> AsyncIterator[Any]:
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


# ---------------------------------------------------------------------------
# Connection caps (W4): Redis-backed, shared by every "process". Each test
# below runs several `scan_progress_endpoint` calls as independent asyncio
# tasks against ONE shared `_FakeBroker`, which is the only thing they share
# (see the `_FakeBroker` docstring above), the same property a real
# multi-worker deployment has via a real Redis instance.
# ---------------------------------------------------------------------------


def test_multi_task_per_user_cap_evicts_the_oldest_regardless_of_which_task_registered_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 4th connection (cap=3) evicts the oldest with code 1001, and the
    pick is correct even though nothing but the fake broker is shared
    between the four connections' tasks, proving the eviction no longer
    depends on which "worker" a socket happened to land on (the defect this
    unit fixes: `docs/concurrency-scaling-plan-2026-08-22.md` §1.7)."""
    from api.v1.ws import REASON_NEWER_CONNECTION, WS_CLOSE_GOING_AWAY

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("WEBSOCKET_MAX_CONNECTIONS_PER_USER", "3")
    monkeypatch.setattr("api.v1.ws.WEBSOCKET_PRESENCE_HEARTBEAT_SECONDS", 0.05)

    user = _FakeUser()
    scan = _FakeScan()
    _patch_authenticated_path(monkeypatch, user=user, get_scan_result=scan)

    broker = _FakeBroker()
    monkeypatch.setattr("api.v1.ws._redis_pubsub", _fake_redis_pubsub(broker))

    scan_id = str(uuid.uuid4())
    sockets = [
        _FakeWebSocket(origin=None, incoming=[json.dumps({"type": "auth", "token": "x"})])
        for _ in range(4)
    ]

    async def _scenario() -> None:
        tasks = await _open_concurrent(sockets, scan_id)
        await _settle_and_cancel(tasks)

    asyncio.run(_scenario())

    assert sockets[0].close_code == WS_CLOSE_GOING_AWAY
    assert sockets[0].close_reason == REASON_NEWER_CONNECTION
    for ws in sockets[1:]:
        # Cancelled while still open, the cap never touched these.
        assert ws.close_code is None


def test_multi_task_two_tabs_four_sockets_all_survive_the_per_user_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression contract (W4): the scan detail page opens two sockets per
    tab, so two tabs open four. With the default per-user cap (8) all four
    must stay open, no matter which of several independent tasks each one
    lands on, this is exactly the scenario that used to succeed or fail
    depending on worker placement when the cap lived in a process-local
    dict (default was 3, which this specific scenario already exceeded)."""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    monkeypatch.delenv("WEBSOCKET_MAX_CONNECTIONS_PER_USER", raising=False)
    monkeypatch.setattr("api.v1.ws.WEBSOCKET_PRESENCE_HEARTBEAT_SECONDS", 0.05)

    user = _FakeUser()
    scan = _FakeScan()
    _patch_authenticated_path(monkeypatch, user=user, get_scan_result=scan)

    broker = _FakeBroker()
    monkeypatch.setattr("api.v1.ws._redis_pubsub", _fake_redis_pubsub(broker))

    scan_id = str(uuid.uuid4())
    sockets = [
        _FakeWebSocket(origin=None, incoming=[json.dumps({"type": "auth", "token": "x"})])
        for _ in range(4)
    ]

    async def _scenario() -> None:
        tasks = await _open_concurrent(sockets, scan_id)
        await _settle_and_cancel(tasks)

    asyncio.run(_scenario())

    for i, ws in enumerate(sockets):
        assert (
            ws.close_code is None
        ), f"socket {i} was closed with code={ws.close_code} reason={ws.close_reason}"


def test_global_cap_refuses_new_connection_without_evicting_anyone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The global cap never evicts a stranger's live connection. Reaching it
    refuses the NEW connection with 4429/capacity_at_limit; the connection
    already holding the last slot is untouched."""
    from api.v1.ws import REASON_GLOBAL_CAPACITY, WS_CLOSE_CAPACITY

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("WEBSOCKET_MAX_CONNECTIONS_GLOBAL", "1")
    monkeypatch.setattr("api.v1.ws.WEBSOCKET_PRESENCE_HEARTBEAT_SECONDS", 0.05)

    user_a = _FakeUser()
    user_b = _FakeUser()
    users_by_token = {"token-a": user_a, "token-b": user_b}

    monkeypatch.setattr(
        "api.v1.ws.decode_token",
        lambda tok, **_kwargs: {"sub": str(users_by_token[tok].id), "type": "access"},
    )

    async def _resolve(
        _session: Any, user_id: uuid.UUID, *, issued_at: int | None = None
    ) -> _FakeUser:
        for candidate in users_by_token.values():
            if candidate.id == user_id:
                return candidate
        raise AssertionError("unknown user_id in test")

    monkeypatch.setattr("api.v1.ws._resolve_user", _resolve)
    monkeypatch.setattr("api.v1.ws._session_factory", lambda _app: (lambda: _FakeSessionCM()))

    scan = _FakeScan()

    async def _get_scan(_session: Any, *, scan_id: uuid.UUID, actor: Any) -> _FakeScan:
        return scan

    monkeypatch.setattr("api.v1.ws.get_scan", _get_scan)

    broker = _FakeBroker()
    monkeypatch.setattr("api.v1.ws._redis_pubsub", _fake_redis_pubsub(broker))

    scan_id = str(uuid.uuid4())
    ws_a = _FakeWebSocket(origin=None, incoming=[json.dumps({"type": "auth", "token": "token-a"})])
    ws_b = _FakeWebSocket(origin=None, incoming=[json.dumps({"type": "auth", "token": "token-b"})])

    async def _scenario() -> None:
        tasks = await _open_concurrent([ws_a, ws_b], scan_id)
        await _settle_and_cancel(tasks)

    asyncio.run(_scenario())

    assert ws_a.close_code is None  # first connection, holds the only slot
    assert ws_b.close_code == WS_CLOSE_CAPACITY
    assert ws_b.close_reason == REASON_GLOBAL_CAPACITY


def test_endpoint_handles_disconnect_during_initial_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the peer hangs up between accept and initial-sync send, the
    endpoint exits cleanly without raising, and the registry entry it just
    created is cleaned up rather than leaked."""
    from api.v1.ws import scan_progress_endpoint

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")

    user = _FakeUser()
    scan = _FakeScan()
    _patch_authenticated_path(monkeypatch, user=user, get_scan_result=scan)

    broker = _FakeBroker()
    monkeypatch.setattr("api.v1.ws._redis_pubsub", _fake_redis_pubsub(broker))

    ws = _FakeWebSocket(
        origin=None,
        incoming=[json.dumps({"type": "auth", "token": "x"})],
    )
    # Mark closed BEFORE send_text — the FakeWebSocket raises on send.
    ws.closed = True

    _run(scan_progress_endpoint(ws, str(uuid.uuid4())))  # type: ignore[arg-type]

    # Endpoint should have unwound silently and unregistered the entry it
    # created a moment earlier, nothing left behind in the fake broker (the
    # fake, like real Redis ZREM, may still hold an emptied-out dict entry
    # for a key that once existed; what matters is that it carries no data).
    assert broker._zsets.get("ws:conns:global", {}) == {}
    assert all(
        members == {} for key, members in broker._zsets.items() if key.startswith("ws:conns:user:")
    )


def test_endpoint_forwards_string_pubsub_payload_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pubsub delivers str payloads (this client uses decode_responses=True,
    W4); the gateway forwards them verbatim."""
    from core.config import scan_progress_channel

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    monkeypatch.setattr("api.v1.ws.WEBSOCKET_PRESENCE_HEARTBEAT_SECONDS", 0.05)

    user = _FakeUser()
    scan = _FakeScan(percent=10, step="bootstrap")
    _patch_authenticated_path(monkeypatch, user=user, get_scan_result=scan)

    broker = _FakeBroker()
    monkeypatch.setattr("api.v1.ws._redis_pubsub", _fake_redis_pubsub(broker))

    payload_text = '{"percent":99,"step":"finalize","ts":"2026-05-06T12:00:00Z"}'
    scan_id = str(uuid.uuid4())
    ws = _FakeWebSocket(
        origin=None,
        incoming=[json.dumps({"type": "auth", "token": "x"})],
    )

    async def _scenario() -> None:
        tasks = await _open_concurrent([ws], scan_id, stagger=0.0)
        await asyncio.sleep(0.02)
        await broker.client().publish(scan_progress_channel(scan_id), payload_text)
        await asyncio.sleep(0.05)
        await _settle_and_cancel(tasks, settle=0.0)

    asyncio.run(_scenario())

    assert payload_text in ws.sent
