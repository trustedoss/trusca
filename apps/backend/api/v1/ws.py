# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
WebSocket gateway — Phase 2 PR #9 task 2.9.

Endpoint:
    GET /ws/scans/{scan_id}            (WebSocket upgrade)

Lifecycle:
    1. Origin gate — Sec-WebSocket-Origin must be in
       `core.config.cors_allowed_origins()`. Empty origin (CLI tooling like
       `wscat`) is permitted only when `app_env() == "dev"`. A reject closes
       with code 1008 reason="origin_rejected" before accept().
    2. Accept the upgrade (we cannot send close codes pre-accept on Starlette
       reliably; closing a NOT-yet-accepted socket simply 403s the handshake,
       which is what we want for the origin gate).
    3. First-message auth — the client must send
       `{"type":"auth","token":"<JWT access>"}` within
       `websocket_auth_timeout_seconds()`. Missing/malformed/expired tokens
       close 1008. The token is verified with the same `decode_token(...,
       expected_type=TOKEN_TYPE_ACCESS)` the HTTP surface uses.
    4. IDOR gate — `services.scan_service.get_scan(...)` checks that the
       authenticated user belongs to the scan's project's owning team.
       Failure → close 4404 (scan_not_found) or 4403 (forbidden).
    5. Connection admission (W4): the gateway generates a per-connection id
       and checks it against two Redis-backed caps shared by every backend
       process: a per-user cap (evicts that user's oldest connection,
       wherever it lives) and a global cap (refuses the new connection
       outright). See "Connection caps" below.
    6. Initial sync push: the gateway emits one progress frame
       `{"percent": int, "step": str, "ts": iso8601}` from the current row
       so a refreshed page sees the latest state without waiting for the
       next worker tick.
    7. Redis subscribe loop: listens on BOTH
       `core.config.scan_progress_channel(scan_id)` (forwards every payload
       as text; the publisher is trusted, so we forward verbatim, no
       re-serialize) AND this connection's own eviction channel
       (`core.ws_registry.evict_channel`). A message on the latter means the
       per-user cap admitted a newer connection and this one lost its slot.
       Polls with a timeout rather than blocking forever so a quiet
       connection (scan finished, no more progress frames) still re-touches
       its presence entry on `WEBSOCKET_PRESENCE_HEARTBEAT_SECONDS`.
    8. Disconnect: natural close, self-eviction, or `WebSocketDisconnect`.
       The registry entry and the Redis client/pubsub are always cleaned up
       in `finally`.

Close codes (single source of truth):
    1000   Normal closure
    1001   Going away (oldest evicted by per-user connection cap)
    1008   Policy violation (auth timeout, bad token, origin rejected). The
           frontend treats this code specifically as an expired session and
           signs the reader out, so nothing else may use it.
    1011   Internal error (Redis connect failure, etc.)
    4400   Bad message format (first frame not parseable JSON)
    4403   IDOR / RBAC denial
    4404   Scan not found
    4429   Global connection cap reached (the new connection is refused; no
           existing connection is evicted for this one; see "Connection
           caps" below)

Connection caps (W4, `core.ws_registry`, `trusca-internal`
`docs/concurrency-scaling-plan-2026-08-22.md` §1.7):
    Both caps are enforced against Redis sorted sets shared by every backend
    process, not a process-local dict, so admission no longer depends on
    which uvicorn worker or pod a given connection happens to land on.

    `core.config.websocket_max_connections_per_user()` (default 8) caps
    concurrent connections per user. The connection that pushes a user over
    this cap is admitted, and the user's OLDEST connection is evicted with
    code 1001 reason="newer_connection", but the eviction is published on
    that connection's own Redis channel and it closes ITSELF from within its
    own asyncio task, whichever process that is. No connection ever closes a
    socket it did not open.

    `core.config.websocket_max_connections_global()` (default 500) caps
    total concurrent connections across every user. A connection that would
    push the total over this cap is refused outright (never admitted, no
    one else evicted) with code 4429 reason="capacity_at_limit".

Logging:
    Every connect/auth-failure/close is logged via structlog with the scan
    id, user id, and remote address. Tokens are NEVER logged (CLAUDE.md
    quality standard §5).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis_async
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from core.config import (
    WEBSOCKET_PRESENCE_HEARTBEAT_SECONDS,
    WEBSOCKET_PRESENCE_TTL_SECONDS,
    app_env,
    cors_allowed_origins,
    redis_url,
    scan_progress_channel,
    websocket_auth_timeout_seconds,
    websocket_max_connections_global,
    websocket_max_connections_per_user,
)
from core.security import (
    TOKEN_TYPE_ACCESS,
    CurrentUser,
    credential_change_invalidates,
    decode_token,
    highest_role,
)
from core.ws_registry import (
    evict_channel,
    new_connection_id,
    publish_eviction,
    register_connection,
    touch_connection,
    unregister_connection,
)
from services.scan_service import (
    ScanForbidden,
    ScanNotFound,
    get_scan,
)

router = APIRouter(tags=["ws"])
log = structlog.get_logger("ws.scans")


# ---------------------------------------------------------------------------
# Close codes
# ---------------------------------------------------------------------------

# Keep these in sync with the module docstring; tests assert on them.
WS_CLOSE_NORMAL: int = 1000
WS_CLOSE_GOING_AWAY: int = 1001
WS_CLOSE_POLICY_VIOLATION: int = 1008
WS_CLOSE_INTERNAL: int = 1011
WS_CLOSE_BAD_MESSAGE: int = 4400
WS_CLOSE_FORBIDDEN: int = 4403
WS_CLOSE_NOT_FOUND: int = 4404
# W4, deliberately NOT 1008: the frontend's close handler treats 1008 as an
# expired session and signs the reader out (`useScanWebSocket.ts`), which
# would be wrong for a capacity refusal that has nothing to do with auth.
WS_CLOSE_CAPACITY: int = 4429

# Reasons (short ASCII strings, RFC 6455 limits reason to 123 bytes).
REASON_AUTH_TIMEOUT = "auth_timeout"
REASON_AUTH_INVALID = "auth_invalid"
REASON_AUTH_INACTIVE = "auth_inactive"
REASON_BAD_MESSAGE = "bad_message"
REASON_ORIGIN_REJECTED = "origin_rejected"
REASON_FORBIDDEN = "forbidden"
REASON_SCAN_NOT_FOUND = "scan_not_found"
REASON_NEWER_CONNECTION = "newer_connection"
REASON_INTERNAL = "internal"
REASON_GLOBAL_CAPACITY = "capacity_at_limit"


# ---------------------------------------------------------------------------
# Connection registry test hook (W4, the registry itself lives in Redis;
# see core.ws_registry). Tests reset the shared keys between cases the same
# way they reset a local dict before this change.
# ---------------------------------------------------------------------------


async def _reset_registry_for_tests() -> None:  # pragma: no cover, test hook
    """Clear every `ws:conns:*` key. Integration tests call this between
    cases so a per-user or global cap set by one test cannot leak into the
    next. Unit tests that never touch a real Redis do not need this hook at
    all. They monkeypatch `_redis_pubsub` instead."""
    client: Any = redis_async.from_url(redis_url(), decode_responses=True)  # type: ignore[no-untyped-call]
    try:
        keys = [key async for key in client.scan_iter(match="ws:conns:*")]
        if keys:
            await client.delete(*keys)
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Helpers (unit-testable)
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """ISO 8601 UTC timestamp matching the publisher's format."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_auth_message(raw: str) -> str:
    """Parse the first frame and return the JWT.

    Raises ValueError if the frame is not a JSON object with
    `{"type":"auth","token":"<str>"}`. The router maps ValueError to close
    code 4400 (bad_message).
    """
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("first frame is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("first frame must be a JSON object")
    if payload.get("type") != "auth":
        raise ValueError("first frame must have type='auth'")
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise ValueError("first frame is missing 'token'")
    return token


def origin_allowed(origin: str | None, *, allowed: list[str], env: str) -> bool:
    """Return True if `origin` may proceed past the handshake gate.

    - A configured origin in `allowed` is always permitted.
    - An empty/missing origin is permitted ONLY when `env == "dev"` (CLI
      tools like wscat do not send Origin). Production must reject blank
      origins so a forged client cannot bypass the browser's same-origin
      enforcement.
    """
    if origin is None or origin == "":
        return env == "dev"
    return origin in allowed


def build_progress_frame(*, percent: int, step: str | None, ts: str | None = None) -> str:
    """Serialize a progress event in the canonical wire format.

    Used for the connect-time initial-sync push. Worker-published payloads
    are forwarded verbatim, so they only need to match this schema.

    P2 #8c — frames now carry an explicit ``type: "progress"`` discriminator
    so the FE can tell a progress event apart from a tool-log line on a
    single channel. Older clients that ignore the field still see the
    historical ``{percent, step, ts}`` envelope unchanged; the field's
    absence on a frame is interpreted by the FE as "progress" (back-compat).
    """
    body: dict[str, Any] = {
        "type": "progress",
        "percent": int(percent),
        "step": step or "",
        "ts": ts or _now_iso(),
    }
    return json.dumps(body, separators=(",", ":"))


def build_log_frame(
    *,
    stage: str,
    stream: str,
    line: str,
    ts: str | None = None,
) -> str:
    """Serialize a tool log line in the canonical wire format (P2 #8c).

    Used by the worker-side publisher (``tasks._progress.publish_log``) and
    by tests pinning the schema. Not used by the gateway itself — published
    log frames are forwarded verbatim from Redis to the WS, the same as
    progress frames.

    Args:
        stage:  Pipeline stage that produced the line (``cdxgen`` /
            ``scancode`` / …). Echoed verbatim.
        stream: ``"stdout"`` or ``"stderr"``. The frame carries the value as
            given — callers are responsible for normalisation.
        line:   The line text. The caller is expected to have truncated it.
        ts:     Optional ISO 8601 timestamp; ``None`` fills with ``_now_iso``.
    """
    body: dict[str, Any] = {
        "type": "log",
        "stage": str(stage),
        "stream": str(stream),
        "line": str(line),
        "ts": ts or _now_iso(),
    }
    return json.dumps(body, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Auth flow
# ---------------------------------------------------------------------------


async def _await_first_frame(websocket: WebSocket, *, timeout: float) -> str:
    """Read the first text frame within `timeout` seconds.

    Raises asyncio.TimeoutError on timeout, WebSocketDisconnect if the peer
    closed first.
    """
    return await asyncio.wait_for(websocket.receive_text(), timeout=timeout)


async def _resolve_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    issued_at: int | None = None,
) -> CurrentUser | None:
    """Load the user + memberships and project them into a CurrentUser.

    Mirrors `core.security._load_current_user` (which expects an HTTP
    Request); we re-implement the SELECT here because WebSocket scopes do
    not provide a Request object the dependency would accept.

    Because it is a copy, it does not inherit the checks added to the
    original. ``issued_at`` is the token's ``iat``, refused when it predates
    the user's last password change: without it a stolen token kept streaming
    scan progress and log lines after the victim reset their password, while
    the same token was correctly refused on every HTTP route.
    """
    from models import Membership, User  # local import — avoid module cycles

    stmt = select(User).where(User.id == user_id).options(selectinload(User.memberships))
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        return None

    if credential_change_invalidates(
        issued_at,
        password_changed_at=user.password_changed_at,
        mfa_changed_at=user.mfa_changed_at,
    ):
        return None

    memberships: list[Membership] = list(user.memberships)
    team_ids = [m.team_id for m in memberships]
    team_roles = {m.team_id: m.role for m in memberships}

    # The shared helper, not a second copy of it. The copy that used to live
    # here listed three grades and omitted ``viewer``, which the contract test
    # covering the real priority map could not see, because it did not know
    # this map existed.
    role = highest_role(
        [m.role for m in memberships],
        is_superuser=bool(user.is_superuser),
    )

    return CurrentUser(
        id=user.id,
        email=user.email,
        role=role,
        team_ids=team_ids,
        team_roles=team_roles,
        is_active=bool(user.is_active),
        is_superuser=bool(user.is_superuser),
        password_changed_at=user.password_changed_at,
        mfa_changed_at=user.mfa_changed_at,
    )


# ---------------------------------------------------------------------------
# Redis subscribe context manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _redis_pubsub(channels: tuple[str, ...]) -> AsyncIterator[tuple[Any, Any]]:
    """Open a Redis client + subscribe to `channels`; close both on exit.

    Yields `(client, pubsub)`. W4: the client is also handed to
    `core.ws_registry` for the connection-cap ZSETs, so a single connection's
    presence tracking and its progress/eviction subscriptions share one
    Redis connection instead of opening two. `decode_responses=True` (unlike
    the pre-W4 client) so registry members and channel names come back as
    `str` without a manual decode at every call site; message *payloads* are
    still handled defensively for both `str` and `bytes` below, since a
    payload published by a `decode_responses=False` client (the worker side,
    `tasks/_progress.py`) can still arrive as either depending on redis-py
    version behaviour.

    `Any` rather than the redis.asyncio types because the redis library does
    not export precise types for the client/pubsub objects (and mypy-strict
    + that library together is brittle).
    """
    # `redis.asyncio.from_url` is loosely-typed in the redis package — the
    # `# type: ignore[no-untyped-call]` keeps mypy --strict happy without
    # disabling the broader check in this module.
    client: Any = redis_async.from_url(redis_url(), decode_responses=True)  # type: ignore[no-untyped-call]
    pubsub = client.pubsub()
    await pubsub.subscribe(*channels)
    try:
        yield client, pubsub
    finally:
        try:
            await pubsub.unsubscribe(*channels)
        except Exception:  # noqa: BLE001 — best-effort teardown
            log.debug("ws_pubsub_unsubscribe_failed", channels=channels, exc_info=True)
        try:
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            log.debug("ws_pubsub_close_failed", exc_info=True)
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            log.debug("ws_redis_close_failed", exc_info=True)


async def _forward_loop(
    websocket: WebSocket,
    pubsub: Any,
    *,
    client: Any,
    progress_channel: str,
    evict_channel_name: str,
    connection_id: str,
) -> tuple[int, str]:
    """Forward progress/log frames until disconnect, self-eviction, or error.

    W4: polls `pubsub.get_message(timeout=...)` rather than iterating
    `pubsub.listen()` (the pre-W4 shape). A scan detail tab can sit open and
    silent for a long time after the scan finishes (no more progress frames
    to forward), and the Redis-backed connection-cap entry backing this
    connection has nothing else to key its liveness off, so every quiet
    interval re-touches it (`core.ws_registry.touch_connection`).

    Two subscriptions share `pubsub`: the scan's progress channel and this
    connection's own eviction channel. A message on the latter means the
    per-user cap admitted a newer connection and picked this one as the
    oldest: we close ourselves (nobody else ever calls `.close()` on a
    socket it did not open) and return.

    `WebSocketDisconnect` from `websocket.send_text` is deliberately NOT
    caught here: it propagates to the caller, same as the pre-W4 shape,
    which maps it to close_reason="client_disconnect" without an explicit
    close (the peer is already gone).
    """
    while True:
        message = await pubsub.get_message(
            ignore_subscribe_messages=True, timeout=WEBSOCKET_PRESENCE_HEARTBEAT_SECONDS
        )

        if message is None:
            # Nothing published during this quiet interval; prove we are
            # still here so a cap check on another connection does not
            # prune us as stale.
            await touch_connection(
                client,
                connection_id=connection_id,
                presence_ttl_seconds=WEBSOCKET_PRESENCE_TTL_SECONDS,
            )
            continue

        channel = message.get("channel")
        channel_name = channel.decode() if isinstance(channel, bytes | bytearray) else channel

        if channel_name == evict_channel_name:
            data = message.get("data")
            reason = data.decode() if isinstance(data, bytes | bytearray) else str(data)
            reason = reason or REASON_NEWER_CONNECTION
            try:
                await websocket.close(code=WS_CLOSE_GOING_AWAY, reason=reason)
            except Exception:  # noqa: BLE001 (already-closed is fine)
                log.debug("ws_self_evict_close_failed", exc_info=True)
            return WS_CLOSE_GOING_AWAY, reason

        if channel_name != progress_channel:
            # We only ever subscribe to the two channels above (defensive
            # check; should not happen).
            log.debug("ws_skip_unexpected_channel", channel=channel_name)
            continue

        payload = message.get("data")
        if isinstance(payload, bytes | bytearray):
            text = bytes(payload).decode("utf-8", errors="replace")
        elif isinstance(payload, str):
            text = payload
        else:
            # Unexpected payload type: skip (publisher is trusted, so this
            # is "should not happen" territory; we log and move on rather
            # than tear the connection down).
            log.debug("ws_skip_unknown_payload_type", payload_type=type(payload).__name__)
            continue
        await websocket.send_text(text)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/scans/{scan_id}")
async def scan_progress_endpoint(websocket: WebSocket, scan_id: str) -> None:
    """Per-scan progress stream.

    See module docstring for the full lifecycle. This function is the only
    public surface; everything else in the module is a helper.
    """
    remote = _remote_addr(websocket)
    structlog.contextvars.bind_contextvars(scan_id=scan_id, remote_addr=remote)

    # ---- 1. Origin gate (pre-accept) -----------------------------------
    origin = websocket.headers.get("origin")
    env = app_env()
    if not origin_allowed(origin, allowed=cors_allowed_origins(), env=env):
        log.warning("ws_origin_rejected", origin=origin)
        # Pre-accept close — Starlette translates this into a 403 handshake.
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason=REASON_ORIGIN_REJECTED)
        structlog.contextvars.unbind_contextvars("scan_id", "remote_addr")
        return

    await websocket.accept()

    # ---- 2. Validate scan id ------------------------------------------
    try:
        scan_uuid = uuid.UUID(scan_id)
    except (ValueError, TypeError):
        log.warning("ws_bad_scan_id")
        await websocket.close(code=WS_CLOSE_NOT_FOUND, reason=REASON_SCAN_NOT_FOUND)
        structlog.contextvars.unbind_contextvars("scan_id", "remote_addr")
        return

    # ---- 3. First-message auth ----------------------------------------
    try:
        raw = await _await_first_frame(websocket, timeout=websocket_auth_timeout_seconds())
    except TimeoutError:
        log.warning("ws_auth_failed", reason=REASON_AUTH_TIMEOUT)
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason=REASON_AUTH_TIMEOUT)
        structlog.contextvars.unbind_contextvars("scan_id", "remote_addr")
        return
    except WebSocketDisconnect:
        # Peer hung up before sending auth — nothing to do.
        structlog.contextvars.unbind_contextvars("scan_id", "remote_addr")
        return

    try:
        token = parse_auth_message(raw)
    except ValueError as exc:
        log.warning("ws_auth_failed", reason=REASON_BAD_MESSAGE, error=str(exc))
        await websocket.close(code=WS_CLOSE_BAD_MESSAGE, reason=REASON_BAD_MESSAGE)
        structlog.contextvars.unbind_contextvars("scan_id", "remote_addr")
        return

    try:
        claims = decode_token(token, expected_type=TOKEN_TYPE_ACCESS)
    except (JWTError, ValueError):
        # Token is invalid/expired/wrong type. We DO NOT log the token.
        log.warning("ws_auth_failed", reason=REASON_AUTH_INVALID)
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason=REASON_AUTH_INVALID)
        structlog.contextvars.unbind_contextvars("scan_id", "remote_addr")
        return

    sub = claims.get("sub")
    try:
        user_id = uuid.UUID(str(sub))
    except (ValueError, TypeError):
        log.warning("ws_auth_failed", reason=REASON_AUTH_INVALID)
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason=REASON_AUTH_INVALID)
        structlog.contextvars.unbind_contextvars("scan_id", "remote_addr")
        return

    # Load user from DB (active check + team_ids for IDOR).
    session_factory = _session_factory(websocket)
    async with session_factory() as session:
        current_user = await _resolve_user(session, user_id, issued_at=claims.get("iat"))
        if current_user is None or not current_user.is_active:
            log.warning("ws_auth_failed", reason=REASON_AUTH_INACTIVE)
            await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason=REASON_AUTH_INACTIVE)
            structlog.contextvars.unbind_contextvars("scan_id", "remote_addr")
            return

        structlog.contextvars.bind_contextvars(user_id=str(current_user.id))

        # ---- 4. IDOR gate -------------------------------------------
        try:
            scan = await get_scan(session, scan_id=scan_uuid, actor=current_user)
        except ScanNotFound:
            log.info("ws_closed", code=WS_CLOSE_NOT_FOUND, reason=REASON_SCAN_NOT_FOUND)
            await websocket.close(code=WS_CLOSE_NOT_FOUND, reason=REASON_SCAN_NOT_FOUND)
            structlog.contextvars.unbind_contextvars("scan_id", "remote_addr", "user_id")
            return
        except ScanForbidden:
            log.warning("ws_closed", code=WS_CLOSE_FORBIDDEN, reason=REASON_FORBIDDEN)
            await websocket.close(code=WS_CLOSE_FORBIDDEN, reason=REASON_FORBIDDEN)
            structlog.contextvars.unbind_contextvars("scan_id", "remote_addr", "user_id")
            return

        # P1 #11 — for a terminal scan, the row's ``current_step`` is whatever
        # the worker happened to last write (typically ``finalize``) — the
        # worker does not always post a follow-up ``current_step=succeeded``
        # before flipping ``status``. If we just echoed ``current_step`` here,
        # the SPA would re-mount the drawer on a completed scan and see step
        # = "finalize" → render an animated spinner on a step that is in fact
        # done. Surface the terminal status as the step instead, and pin
        # percent at 100 / latest, so the initial sync frame already carries
        # the terminal verdict and the UI does not need a second round-trip
        # to know the scan is over.
        initial_step = scan.current_step
        initial_percent = int(scan.progress_percent or 0)
        if scan.status in ("succeeded", "failed", "cancelled"):
            initial_step = scan.status
            if scan.status == "succeeded":
                initial_percent = 100
        initial_frame = build_progress_frame(
            percent=initial_percent,
            step=initial_step,
        )

    # ---- 5-7. Admission (W4), initial sync push, subscribe + forward ---
    # A single Redis client backs both the connection-cap registry and the
    # progress/eviction pub/sub for this connection's whole lifetime.
    connection_id = new_connection_id()
    progress_channel = scan_progress_channel(scan_id)
    my_evict_channel = evict_channel(connection_id)
    close_code = WS_CLOSE_NORMAL
    close_reason = ""
    try:
        async with _redis_pubsub((progress_channel, my_evict_channel)) as (client, pubsub):
            # ---- 5. Admission: per-user + global caps (W4) -------------
            result = await register_connection(
                client,
                user_id=current_user.id,
                connection_id=connection_id,
                max_per_user=websocket_max_connections_per_user(),
                max_global=websocket_max_connections_global(),
                presence_ttl_seconds=WEBSOCKET_PRESENCE_TTL_SECONDS,
            )
            if not result.accepted:
                # Global cap: refuse outright. We never evict a stranger's
                # live connection just to seat a new one.
                log.warning("ws_global_capacity_reached", user_id=str(current_user.id))
                close_code = WS_CLOSE_CAPACITY
                close_reason = REASON_GLOBAL_CAPACITY
                await websocket.close(code=close_code, reason=close_reason)
                return
            if result.evicted_connection_id is not None:
                # Per-user cap: the oldest connection lost its slot. It
                # closes ITSELF from within its own asyncio task (wherever
                # that is) on receipt of this notice; we never touch a
                # socket we did not open.
                await publish_eviction(
                    client, result.evicted_connection_id, reason=REASON_NEWER_CONNECTION
                )

            log.info("ws_connected", user_id=str(current_user.id))

            # ---- 6. Initial sync push ----------------------------------
            try:
                await websocket.send_text(initial_frame)
            except WebSocketDisconnect:
                close_reason = "client_disconnect_initial"
                await unregister_connection(
                    client, user_id=current_user.id, connection_id=connection_id
                )
                return

            # ---- 7. Subscribe + forward loop ---------------------------
            try:
                close_code, close_reason = await _forward_loop(
                    websocket,
                    pubsub,
                    client=client,
                    progress_channel=progress_channel,
                    evict_channel_name=my_evict_channel,
                    connection_id=connection_id,
                )
            finally:
                await unregister_connection(
                    client, user_id=current_user.id, connection_id=connection_id
                )
    except WebSocketDisconnect:
        # Peer closed — normal path.
        close_reason = "client_disconnect"
    except Exception as exc:  # noqa: BLE001 — every other failure is internal
        log.error("ws_internal_error", error=str(exc), exc_info=True)
        close_code = WS_CLOSE_INTERNAL
        close_reason = REASON_INTERNAL
        try:
            await websocket.close(code=close_code, reason=close_reason)
        except Exception:  # noqa: BLE001 — already closed is acceptable
            log.debug("ws_internal_close_failed", exc_info=True)
    finally:
        log.info("ws_closed", code=close_code, reason=close_reason)
        structlog.contextvars.unbind_contextvars("scan_id", "remote_addr", "user_id")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _remote_addr(websocket: WebSocket) -> str | None:
    """Best-effort remote address for log lines."""
    client = websocket.client
    if client is None:
        return None
    return client.host


def _session_factory(websocket: WebSocket) -> async_sessionmaker[AsyncSession]:
    """Resolve the app's async session factory.

    FastAPI's `Depends(get_db)` does not work cleanly inside WebSocket
    routes (the Request-bound dependency machinery is HTTP-only), so we
    reach into `app.state` directly. The lifespan installs the factory; if
    it has not yet (e.g. tests that bypass lifespan), `core.db._ensure_state`
    builds it lazily on first use.
    """
    from core.db import _ensure_state

    return _ensure_state(websocket.app)


__all__ = [
    "REASON_AUTH_INACTIVE",
    "REASON_AUTH_INVALID",
    "REASON_AUTH_TIMEOUT",
    "REASON_BAD_MESSAGE",
    "REASON_FORBIDDEN",
    "REASON_GLOBAL_CAPACITY",
    "REASON_INTERNAL",
    "REASON_NEWER_CONNECTION",
    "REASON_ORIGIN_REJECTED",
    "REASON_SCAN_NOT_FOUND",
    "WS_CLOSE_BAD_MESSAGE",
    "WS_CLOSE_CAPACITY",
    "WS_CLOSE_FORBIDDEN",
    "WS_CLOSE_GOING_AWAY",
    "WS_CLOSE_INTERNAL",
    "WS_CLOSE_NORMAL",
    "WS_CLOSE_NOT_FOUND",
    "WS_CLOSE_POLICY_VIOLATION",
    "build_log_frame",
    "build_progress_frame",
    "origin_allowed",
    "parse_auth_message",
    "router",
    "scan_progress_endpoint",
]
