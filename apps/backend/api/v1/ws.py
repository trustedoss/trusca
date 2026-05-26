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
    5. Initial sync push — the gateway emits one progress frame
       `{"percent": int, "step": str, "ts": iso8601}` from the current row
       so a refreshed page sees the latest state without waiting for the
       next worker tick.
    6. Redis subscribe loop — listens on
       `core.config.scan_progress_channel(scan_id)` and forwards every
       payload as text. The publisher is trusted, so we forward bytes
       verbatim (no re-serialize) to avoid breaking forward compatibility.
    7. Disconnect — natural close, or `WebSocketDisconnect`. The pubsub /
       Redis client is always closed in `finally`.

Close codes (single source of truth):
    1000   Normal closure
    1001   Going away (oldest evicted by per-user connection cap)
    1008   Policy violation (auth timeout, bad token, origin rejected)
    1011   Internal error (Redis connect failure, etc.)
    4400   Bad message format (first frame not parseable JSON)
    4403   IDOR / RBAC denial
    4404   Scan not found

Per-user connection cap:
    `core.config.websocket_max_connections_per_user()` (default 3) caps
    concurrent connections per user. The 4th attempt evicts the oldest with
    code 1001 reason="newer_connection". Tracking is in-process — multi-
    worker deployments will need a Redis-backed counter (TODO inside
    `_register_connection`).

Logging:
    Every connect/auth-failure/close is logged via structlog with the scan
    id, user id, and remote address. Tokens are NEVER logged (CLAUDE.md
    quality standard §5).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
    app_env,
    cors_allowed_origins,
    redis_url,
    scan_progress_channel,
    websocket_auth_timeout_seconds,
    websocket_max_connections_per_user,
)
from core.security import (
    TOKEN_TYPE_ACCESS,
    CurrentUser,
    decode_token,
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

# Reasons (short ASCII strings — RFC 6455 limits reason to 123 bytes).
REASON_AUTH_TIMEOUT = "auth_timeout"
REASON_AUTH_INVALID = "auth_invalid"
REASON_AUTH_INACTIVE = "auth_inactive"
REASON_BAD_MESSAGE = "bad_message"
REASON_ORIGIN_REJECTED = "origin_rejected"
REASON_FORBIDDEN = "forbidden"
REASON_SCAN_NOT_FOUND = "scan_not_found"
REASON_NEWER_CONNECTION = "newer_connection"
REASON_INTERNAL = "internal"


# ---------------------------------------------------------------------------
# Per-user connection registry (per-process, in-memory)
# ---------------------------------------------------------------------------


@dataclass
class _Registry:
    """Tracks open WebSockets keyed by user id.

    `connections[user_id]` is an ordered deque (oldest first). When the size
    exceeds the cap we pop the oldest and close it asynchronously.
    """

    connections: dict[uuid.UUID, deque[WebSocket]]
    lock: asyncio.Lock


def _new_registry() -> _Registry:
    return _Registry(connections={}, lock=asyncio.Lock())


# Module-level singleton; tests reset via `_reset_registry_for_tests`.
_registry: _Registry = _new_registry()


def _reset_registry_for_tests() -> None:  # pragma: no cover — test hook
    """Reset the shared registry. Tests use this between cases."""
    global _registry
    _registry = _new_registry()


async def _register_connection(
    user_id: uuid.UUID, websocket: WebSocket, *, max_per_user: int
) -> WebSocket | None:
    """Register `websocket` and evict the oldest if over the cap.

    Returns the evicted WebSocket (caller closes it) or None.

    TODO: replace the in-process dict with a Redis-backed counter once the
    backend runs more than one worker — today the cap is per-process.
    """
    async with _registry.lock:
        bucket = _registry.connections.setdefault(user_id, deque())
        bucket.append(websocket)
        if len(bucket) > max_per_user:
            return bucket.popleft()
    return None


async def _unregister_connection(user_id: uuid.UUID, websocket: WebSocket) -> None:
    async with _registry.lock:
        bucket = _registry.connections.get(user_id)
        if bucket is None:
            return
        try:
            bucket.remove(websocket)
        except ValueError:
            pass
        if not bucket:
            del _registry.connections[user_id]


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
    """
    body: dict[str, Any] = {
        "percent": int(percent),
        "step": step or "",
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


async def _resolve_user(session: AsyncSession, user_id: uuid.UUID) -> CurrentUser | None:
    """Load the user + memberships and project them into a CurrentUser.

    Mirrors `core.security._load_current_user` (which expects an HTTP
    Request); we re-implement the SELECT here because WebSocket scopes do
    not provide a Request object the dependency would accept.
    """
    from models import Membership, User  # local import — avoid module cycles

    stmt = select(User).where(User.id == user_id).options(selectinload(User.memberships))
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        return None

    memberships: list[Membership] = list(user.memberships)
    team_ids = [m.team_id for m in memberships]
    team_roles = {m.team_id: m.role for m in memberships}

    # Highest role (super_admin > team_admin > developer); duplicate of the
    # tiny helper in core.security to avoid pulling a private symbol.
    role_priority = {"developer": 1, "team_admin": 2, "super_admin": 3}
    if user.is_superuser:
        role = "super_admin"
    elif memberships:
        role = max((m.role for m in memberships), key=lambda r: role_priority.get(r, 0))
    else:
        role = "developer"

    return CurrentUser(
        id=user.id,
        email=user.email,
        role=role,
        team_ids=team_ids,
        team_roles=team_roles,
        is_active=bool(user.is_active),
        is_superuser=bool(user.is_superuser),
    )


# ---------------------------------------------------------------------------
# Redis subscribe context manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _redis_pubsub(channel: str) -> AsyncIterator[Any]:
    """Open a Redis client + pubsub on `channel`; close both on exit.

    `Any` rather than the redis.asyncio types because the redis library does
    not export precise types for the pubsub object (and mypy-strict + that
    library together is brittle).
    """
    # `redis.asyncio.from_url` is loosely-typed in the redis package — the
    # `# type: ignore[no-untyped-call]` keeps mypy --strict happy without
    # disabling the broader check in this module.
    client: Any = redis_async.from_url(redis_url())  # type: ignore[no-untyped-call]
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    try:
        yield pubsub
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:  # noqa: BLE001 — best-effort teardown
            log.debug("ws_pubsub_unsubscribe_failed", channel=channel, exc_info=True)
        try:
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            log.debug("ws_pubsub_close_failed", exc_info=True)
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            log.debug("ws_redis_close_failed", exc_info=True)


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
        raw = await _await_first_frame(
            websocket, timeout=websocket_auth_timeout_seconds()
        )
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
        current_user = await _resolve_user(session, user_id)
        if current_user is None or not current_user.is_active:
            log.warning("ws_auth_failed", reason=REASON_AUTH_INACTIVE)
            await websocket.close(
                code=WS_CLOSE_POLICY_VIOLATION, reason=REASON_AUTH_INACTIVE
            )
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

    # ---- 5. Register connection (per-user cap) -------------------------
    max_per_user = websocket_max_connections_per_user()
    evicted = await _register_connection(
        current_user.id, websocket, max_per_user=max_per_user
    )
    if evicted is not None:
        # Evict the oldest BEFORE we push anything to the new connection so
        # the user does not briefly see two open streams from this worker.
        try:
            await evicted.close(
                code=WS_CLOSE_GOING_AWAY, reason=REASON_NEWER_CONNECTION
            )
        except Exception:  # noqa: BLE001 — already-closed is fine
            log.debug("ws_evict_close_failed", exc_info=True)

    log.info("ws_connected", user_id=str(current_user.id))

    # ---- 6. Initial sync push ------------------------------------------
    try:
        await websocket.send_text(initial_frame)
    except WebSocketDisconnect:
        await _unregister_connection(current_user.id, websocket)
        log.info("ws_closed", code=WS_CLOSE_NORMAL, reason="client_disconnect_initial")
        structlog.contextvars.unbind_contextvars("scan_id", "remote_addr", "user_id")
        return

    # ---- 7. Subscribe + forward loop -----------------------------------
    channel = scan_progress_channel(scan_id)
    close_code = WS_CLOSE_NORMAL
    close_reason = ""
    try:
        async with _redis_pubsub(channel) as pubsub:
            async for message in pubsub.listen():
                if not isinstance(message, dict):
                    continue
                if message.get("type") != "message":
                    continue
                payload = message.get("data")
                if isinstance(payload, bytes | bytearray):
                    text = bytes(payload).decode("utf-8", errors="replace")
                elif isinstance(payload, str):
                    text = payload
                else:
                    # Unexpected payload type — skip (publisher is trusted, so
                    # this is "should not happen" territory; we log and move on
                    # rather than tear the connection down).
                    log.debug("ws_skip_unknown_payload_type", payload_type=type(payload).__name__)
                    continue
                await websocket.send_text(text)
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
        await _unregister_connection(current_user.id, websocket)
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
    "REASON_INTERNAL",
    "REASON_NEWER_CONNECTION",
    "REASON_ORIGIN_REJECTED",
    "REASON_SCAN_NOT_FOUND",
    "WS_CLOSE_BAD_MESSAGE",
    "WS_CLOSE_FORBIDDEN",
    "WS_CLOSE_GOING_AWAY",
    "WS_CLOSE_INTERNAL",
    "WS_CLOSE_NORMAL",
    "WS_CLOSE_NOT_FOUND",
    "WS_CLOSE_POLICY_VIOLATION",
    "build_progress_frame",
    "origin_allowed",
    "parse_auth_message",
    "router",
    "scan_progress_endpoint",
]
