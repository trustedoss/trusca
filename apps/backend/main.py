"""
FastAPI application entrypoint.

Wires together:
- structlog JSON logging
- request_id middleware + audit context middleware
- RFC 7807 exception handlers (and slowapi 429 handler)
- async SQLAlchemy engine bound to app.state during the lifespan
- audit_logs SQLAlchemy event listener
- /health endpoint (used by docker-compose healthchecks and probes)
- /auth router (Phase 1 PR #5 — register/login/refresh/logout/me)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from api.v1 import (
    admin_router,
    api_keys_router,
    approvals_router,
    auth_router,
    components_router,
    licenses_router,
    obligations_router,
    policy_gate_router,
    projects_router,
    sbom_router,
    scans_router,
    vulnerabilities_router,
    webhooks_github_router,
    webhooks_gitlab_router,
    ws_router,
)
from core.audit import install_audit_listeners
from core.config import (
    app_env,
    cors_allowed_origins,
    log_level,
    secret_key,
    validate_cors_origins,
)
from core.db import build_engine, build_session_factory
from core.errors import install_exception_handlers
from core.logging import configure_logging
from core.middleware import (
    AuditContextMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)
from core.ratelimit import limiter, rate_limit_exceeded_handler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(level=log_level())
    log = structlog.get_logger("startup")
    log.info("backend_starting", app_env=app_env())

    # C-1: fail fast if SECRET_KEY is missing/short in non-dev environments.
    # secret_key() raises RuntimeError; we let it propagate so the process
    # crashes on boot rather than booting with a weak key.
    secret_key()

    engine = build_engine()
    app.state.engine = engine
    session_factory = build_session_factory(engine)
    app.state.session_factory = session_factory

    # Install the audit-log SQLAlchemy event listener now that we have a
    # session factory bound. Listeners are deduplicated inside the helper so
    # repeated starts (tests + uvicorn reloader) do not double-fire.
    install_audit_listeners(session_factory)

    try:
        yield
    finally:
        await engine.dispose()
        log.info("backend_stopped")


app = FastAPI(
    title="TrustedOSS Portal API",
    version="0.0.1",
    description="Open-source enterprise SCA portal — auth surface (Phase 1 PR #5).",
    lifespan=lifespan,
)

# Order matters for ASGI middlewares — Starlette's `add_middleware` adds
# each new middleware at the OUTSIDE of the stack (last-added is outermost).
# We want SecurityHeadersMiddleware to be the outermost layer so the
# hardening headers wrap *every* response, including:
#   - CORS pre-flight (OPTIONS) responses produced by CORSMiddleware itself,
#   - 4xx/5xx error envelopes emitted by the exception handlers,
#   - WebSocket-upgrade rejections.
# Inner stack: RequestID → AuditContext → CORS → SecurityHeaders (outermost).
# Outermost (read top-to-bottom for request flow): SecurityHeaders → CORS →
# RequestID → AuditContext → app handler.
# slowapi rate limiting is applied via the @limiter.limit decorator inside
# routes; we deliberately avoid SlowAPIMiddleware (which is a
# BaseHTTPMiddleware) because it interacts badly with async SQLAlchemy
# (cross-event-loop futures + body re-reading that breaks Pydantic body
# parsing). The decorator + exception handler give us the same 5/min/IP
# guarantee without the side effects.
app.state.limiter = limiter
app.add_middleware(AuditContextMiddleware)
app.add_middleware(RequestIDMiddleware)

# H-3: validate CORS configuration before registering the middleware so a
# misconfigured allow-list (wildcard with credentials, or http:// in prod)
# crashes boot instead of silently exposing a permissive policy.
_cors_origins = cors_allowed_origins()
validate_cors_origins(_cors_origins, env=app_env())
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    # H-3: pin methods + headers to the actual surface we use instead of "*".
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["authorization", "content-type", "x-request-id"],
    # PR #14: surface Content-Disposition so the SPA can read the
    # operator-friendly filename of CSV streaming downloads (admin audit
    # export). Without this, axios cannot read the header and the browser
    # falls back to a synthetic filename.
    expose_headers=["content-disposition"],
)

# Added LAST so it becomes the outermost middleware — wraps CORS preflight
# and exception-handler-generated responses too. (security-reviewer F1.)
app.add_middleware(SecurityHeadersMiddleware)

install_exception_handlers(app)
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # type: ignore[arg-type]

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(projects_router)
app.include_router(scans_router)
app.include_router(components_router)
app.include_router(vulnerabilities_router)
app.include_router(licenses_router)
app.include_router(obligations_router)
app.include_router(approvals_router)
app.include_router(sbom_router)
# Phase 5 PR #16: API Key management + Webhook receivers (GitHub / GitLab).
# Webhook endpoints are PUBLIC (no JWT) but each delivery is HMAC-authenticated
# against a per-project shared secret stored in `projects.webhook_secret`.
app.include_router(api_keys_router)
app.include_router(webhooks_github_router)
app.include_router(webhooks_gitlab_router)
# Phase 5 PR #17: build-gate result + SCA PR-comment endpoints. Both routes
# accept JWT or API-key bearer tokens so CI runners can call them.
app.include_router(policy_gate_router)
# Phase 2 PR #9: WebSocket gateway. The router declares the absolute path
# `/ws/scans/{scan_id}` (no prefix) so future ws routes can group themselves
# under the same router without nudging this include.
app.include_router(ws_router)


@app.get("/health", tags=["system"], summary="Liveness probe")
async def health() -> dict[str, str]:
    """Cheap liveness probe used by docker-compose healthchecks."""
    return {"status": "ok"}
