"""
Request-scoped middlewares.

RequestIDMiddleware:
- Reads inbound `X-Request-ID` header (any client/proxy can supply one) or
  generates a new UUIDv4 when absent.
- Binds the id into structlog contextvars so every log line emitted while
  handling the request carries it automatically.
- Echoes the id back via `X-Request-ID` response header so log correlation
  works end-to-end.

Implemented as a pure ASGI middleware (not BaseHTTPMiddleware) so that
exceptions raised inside route handlers propagate cleanly to Starlette's
ServerErrorMiddleware and our RFC 7807 exception handlers.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

REQUEST_ID_HEADER = "x-request-id"
REQUEST_ID_HEADER_BYTES = REQUEST_ID_HEADER.encode("latin-1")

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
