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

import posixpath
import time
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

from core.audit import audit_context
from core.config import demo_read_only

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


# ---------------------------------------------------------------------------
# v2.1 Track B (B5) — DEMO_READ_ONLY guard.
#
# Core SECURITY boundary. When DEMO_READ_ONLY is on (the public live demo), the
# instance must serve reads but reject every mutation. We enforce this in ONE
# place (this middleware) instead of sprinkling per-endpoint guards, so a new
# router added later cannot silently escape the policy — the middleware sees the
# raw ASGI scope for *every* HTTP request before any router/dependency runs.
#
# Design (allow-list, fail-closed):
#   * "safe" HTTP methods (GET / HEAD / OPTIONS) always pass — they cannot
#     mutate state, and OPTIONS must pass so CORS preflight keeps working.
#   * every OTHER method (POST / PUT / PATCH / DELETE, plus any exotic verb) is
#     BLOCKED by default. We only let a request through if its *normalized* path
#     is on a tiny, explicit allow-list of auth flows the demo still needs
#     (login / refresh / logout). This is deliberately a deny-by-default policy:
#     adding a new mutating endpoint requires no change here and is blocked
#     automatically, which is the safe direction for a public demo.
#   * WebSocket upgrades (scope["type"] == "websocket") are *not* a mutation
#     surface we expose for writes — the only ws route streams read-only scan
#     progress — but to be safe we let the upgrade through unchanged (a ws
#     connection cannot POST/PUT; any server-side mutation would itself be an
#     HTTP call that this middleware already gates). Non-http/ws scopes
#     (lifespan) pass through untouched.
#
# Bypass hardening:
#   * The path is normalized with posixpath.normpath after collapsing '\' to '/'
#     and percent-decoding is NOT relied upon (the ASGI scope path is already
#     percent-decoded by the server). normpath resolves '.'/'..' segments so a
#     traversal like '/v1/projects/../auth/login' cannot smuggle a write path
#     onto the allow-list, and an attempt like '/auth/login/../../v1/projects'
#     collapses to a NON-allow-listed path and is blocked.
#   * Method comparison is case-insensitive (HTTP methods are case-sensitive per
#     RFC 7231, but we upper-case defensively so a server that forwards a
#     lower-case verb cannot dodge the check).
#   * Trailing slashes are stripped before the allow-list comparison so
#     '/auth/login/' and '/auth/login' are treated identically.
# ---------------------------------------------------------------------------

_SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

# Exact (METHOD, normalized-path) pairs for the auth flows a read-only demo must
# still permit. We key on the method too (not the path alone) so an exotic verb
# such as ``CONNECT /auth/login`` cannot ride the allow-list — every real auth
# write is a POST. Registration / password-reset / password-change are
# intentionally OMITTED: a public read-only demo uses pre-seeded shared
# accounts, so allowing self-registration or password mutation would let a
# visitor change demo state.
_DEMO_WRITE_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/auth/login"),
        ("POST", "/auth/refresh"),
        ("POST", "/auth/logout"),
    }
)

_DEMO_READ_ONLY_TYPE = "urn:trustedoss:problem:demo-read-only"


def _normalize_path(raw: str) -> str:
    """Collapse a request path to a canonical form for allow-list comparison.

    Defends the allow-list against traversal / separator tricks:
      * back-slashes are folded to forward-slashes,
      * '.'/'..' segments are resolved (posixpath.normpath),
      * a single trailing slash is removed (root '/' is preserved).
    The ASGI server has already percent-decoded scope['path'], so we operate on
    the decoded value directly.
    """
    if not raw:
        return "/"
    candidate = raw.replace("\\", "/")
    # normpath collapses '//', '/./' and resolves '/../'. It also strips a
    # trailing slash except for the root.
    normalized = posixpath.normpath(candidate)
    # normpath turns '' into '.'; guard that and force a leading slash so the
    # comparison is always against an absolute path.
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized


def _is_demo_write_allowed(method: str, path: str) -> bool:
    """True iff a mutating request should be permitted under DEMO_READ_ONLY."""
    upper = method.upper()
    if upper in _SAFE_METHODS:
        return True
    return (upper, _normalize_path(path)) in _DEMO_WRITE_ALLOWLIST


class DemoReadOnlyMiddleware:
    """Reject mutating HTTP requests when DEMO_READ_ONLY is enabled.

    See the module-level section above for the full allow-list / bypass-hardening
    rationale. The flag is read at request time (CLAUDE.md rule #11) so the guard
    can be toggled by env without a rebuild; when the flag is off this middleware
    is a no-op pass-through with negligible overhead.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not demo_read_only():
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "")).upper()
        path = str(scope.get("path", "/"))

        if _is_demo_write_allowed(method, path):
            await self.app(scope, receive, send)
            return

        # Blocked: emit an RFC 7807 403 directly from the middleware. We import
        # the helper lazily to avoid a circular import at module load (errors.py
        # has no dependency on middleware, but keeping the import local matches
        # the "build the response only on the rejection path" intent).
        from core.errors import problem_response

        structlog.get_logger("demo").warning(
            "demo_read_only_blocked", method=method, path=path
        )
        response = problem_response(
            status_code=403,
            title="Read-only demo",
            detail=(
                "This is a read-only live demo. Creating, updating, or deleting "
                "data is disabled. Sign in and explore the pre-seeded projects, "
                "scans, vulnerabilities, and reports."
            ),
            instance=path,
            type_=_DEMO_READ_ONLY_TYPE,
            demo_read_only=True,
        )
        await response(scope, receive, send)
