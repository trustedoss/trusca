"""
Request-scoped middlewares.

RequestIDMiddleware:
- Reads inbound `X-Request-ID` header (any client/proxy can supply one) or
  generates a new UUIDv4 when absent.
- Binds the id into structlog contextvars so every log line emitted while
  handling the request carries it automatically.
- Echoes the id back via `X-Request-ID` response header so log correlation
  works end-to-end.

AuditContextMiddleware:
- Captures request_id, ip, and user_agent into the audit ContextVar so the
  SQLAlchemy `before_flush` listener (core.audit) can attach them to every
  AuditLog row. The `user_id` slot is filled later by
  `get_current_user`/`get_optional_current_user` once the bearer token is
  resolved.

SecurityHeadersMiddleware:
- Attaches a baseline set of hardening headers (`X-Content-Type-Options`,
  `Referrer-Policy`, `X-Frame-Options`) to every HTTP response, including
  4xx/5xx error responses and CORS pre-flight. CSP is *not* set here because
  the only HTML surface served by FastAPI is the OpenAPI `/docs` page (and
  the Vite dev server in development) which both rely on inline scripts —
  CSP for that surface is a separate hardening PR.

All middlewares are pure ASGI (not BaseHTTPMiddleware) so exceptions raised
inside route handlers propagate cleanly to Starlette's ServerErrorMiddleware
and our RFC 7807 handlers.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

from core.audit import audit_context

REQUEST_ID_HEADER = "x-request-id"
REQUEST_ID_HEADER_BYTES = REQUEST_ID_HEADER.encode("latin-1")
USER_AGENT_HEADER_BYTES = b"user-agent"
X_FORWARDED_FOR_HEADER_BYTES = b"x-forwarded-for"

# Loose ASGI shapes: keys are protocol-defined but values mix str/int/bytes/
# lists. We rely on runtime checks rather than encoding the union here.
Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class RequestIDMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _extract_request_id(scope) or str(uuid.uuid4())

        clear_contextvars()
        bind_contextvars(
            request_id=request_id,
            method=scope.get("method"),
            path=scope.get("path"),
        )

        log = structlog.get_logger("http")
        started = time.perf_counter()
        status_holder: dict[str, int] = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message.get("status", 500))
                headers = list(message.get("headers") or [])
                headers = [(k, v) for k, v in headers if k.lower() != REQUEST_ID_HEADER_BYTES]
                headers.append((REQUEST_ID_HEADER_BYTES, request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            log.info(
                "request_completed",
                status_code=status_holder["status"],
                duration_ms=round(duration_ms, 2),
            )
            clear_contextvars()


def _extract_request_id(scope: Scope) -> str | None:
    headers: list[tuple[bytes, bytes]] = scope.get("headers", []) or []
    for key, value in headers:
        if key.lower() == REQUEST_ID_HEADER_BYTES:
            try:
                return value.decode("latin-1")
            except UnicodeDecodeError:
                return None
    return None


def _extract_header(scope: Scope, target: bytes) -> str | None:
    headers: list[tuple[bytes, bytes]] = scope.get("headers", []) or []
    for key, value in headers:
        if key.lower() == target:
            try:
                return value.decode("latin-1")
            except UnicodeDecodeError:
                return None
    return None


def _extract_client_ip(scope: Scope) -> str | None:
    """
    Resolve the caller's IP. Prefer X-Forwarded-For (first hop) so reverse
    proxies in front of FastAPI work; fall back to the ASGI client tuple.
    """
    fwd = _extract_header(scope, X_FORWARDED_FOR_HEADER_BYTES)
    if fwd:
        # XFF is a comma-separated list — the leftmost is the original client.
        return fwd.split(",", 1)[0].strip() or None
    client = scope.get("client")
    if isinstance(client, tuple | list) and client:
        host = client[0]
        return str(host) if host else None
    return None


_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
)


class SecurityHeadersMiddleware:
    """Append baseline hardening headers to every HTTP response.

    Idempotent: if the route handler already emitted any of these headers,
    the existing value wins (we never duplicate or override). This keeps the
    middleware safe to install alongside future per-route overrides.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                existing = {k.lower() for k, _ in headers}
                for name, value in _SECURITY_HEADERS:
                    if name not in existing:
                        headers.append((name, value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class AuditContextMiddleware:
    """
    Bind request_id / ip / user_agent into the audit ContextVar.

    Runs after RequestIDMiddleware so we can read the same request_id the logs
    use. The `user_id` slot is filled later by `get_current_user`.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _extract_request_id(scope)
        ip = _extract_client_ip(scope)
        user_agent = _extract_header(scope, USER_AGENT_HEADER_BYTES)

        token = audit_context.set(
            {
                "request_id": request_id,
                "ip": ip,
                "user_agent": user_agent,
                "user_id": None,
                "team_id": None,
            }
        )
        try:
            await self.app(scope, receive, send)
        finally:
            audit_context.reset(token)
