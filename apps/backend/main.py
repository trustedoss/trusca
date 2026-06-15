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
    audit_router,
    auth_router,
    compliance_router,
    components_router,
    dashboard_router,
    github_app_router,
    health_router,
    license_policies_router,
    licenses_router,
    notifications_router,
    oauth_router,
    obligations_router,
    policy_gate_router,
    projects_router,
    remediation_router,
    reports_router,
    sbom_router,
    scans_router,
    source_tree_router,
    users_me_router,
    vex_router,
    vulnerabilities_router,
    webhooks_github_router,
    webhooks_gitlab_router,
    ws_router,
)
from core.audit import install_audit_listeners
from core.config import (
    app_env,
    cors_allowed_origins,
    demo_read_only,
    log_level,
    secret_key,
    validate_cors_origins,
)
from core.db import build_engine, build_session_factory
from core.errors import install_exception_handlers
from core.logging import configure_logging
from core.middleware import (
    AuditContextMiddleware,
    DemoReadOnlyMiddleware,
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

    # Marathon bundle 8 (L1) — surface the connected role at boot so
    # operators verifying the install can confirm DML-only mode is
    # active. In APP_ENV=prod with DATABASE_URL_APP set, refuse to
    # start when the runtime ended up connecting as a non-app role
    # (mismatched env wiring → fail loud, not silent regression).
    import os as _os

    from sqlalchemy import text as _sql_text

    async with engine.connect() as _conn:
        _role = (await _conn.execute(_sql_text("SELECT current_user"))).scalar()
    log.info("db.role.connected", role=_role)
    if app_env() == "prod" and _os.getenv("DATABASE_URL_APP") and _role != "trustedoss_app":
        raise RuntimeError(
            f"DATABASE_URL_APP is set in APP_ENV=prod but the runtime "
            f"connected as role={_role!r} (expected 'trustedoss_app'). "
            f"Check docker-compose env wiring for the L1 split."
        )

    try:
        yield
    finally:
        await engine.dispose()
        log.info("backend_stopped")


app = FastAPI(
    title="TRUSCA API",
    version="2.2.0",
    description=(
        "Open-source self-hosted SCA portal — CVE, license compliance, and SBOM "
        "management with EPSS prioritization, VEX consumption, CI build gating, "
        "and Trivy-backed CVE matching with weekly DB refresh + automatic "
        "re-matching on new vulnerability data."
    ),
    lifespan=lifespan,
)

# Order matters for ASGI middlewares — Starlette's `add_middleware` adds
# each new middleware at the OUTSIDE of the stack (last-added is outermost).
# We want SecurityHeadersMiddleware to be the outermost layer so the
# hardening headers wrap *every* response, including:
#   - CORS pre-flight (OPTIONS) responses produced by CORSMiddleware itself,
#   - 4xx/5xx error envelopes emitted by the exception handlers,
#   - WebSocket-upgrade rejections.
# Inner stack: AuditContext → DemoReadOnly → RequestID → CORS → SecurityHeaders
# (outermost). Outermost (read top-to-bottom for request flow): SecurityHeaders
# → CORS → RequestID → DemoReadOnly → AuditContext → app handler.
# DemoReadOnlyMiddleware (v2.1 B5) sits INSIDE RequestIDMiddleware so the
# "request blocked" warning carries the bound request_id, but OUTSIDE the app
# router so a rejected mutation never reaches any endpoint/dependency. It is a
# no-op unless DEMO_READ_ONLY is truthy.
# slowapi rate limiting is applied via the @limiter.limit decorator inside
# routes; we deliberately avoid SlowAPIMiddleware (which is a
# BaseHTTPMiddleware) because it interacts badly with async SQLAlchemy
# (cross-event-loop futures + body re-reading that breaks Pydantic body
# parsing). The decorator + exception handler give us the same 5/min/IP
# guarantee without the side effects.
app.state.limiter = limiter
app.add_middleware(AuditContextMiddleware)
app.add_middleware(DemoReadOnlyMiddleware)
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
    # `if-match` carries the optimistic-concurrency version on the approval
    # transition (PATCH /v1/approvals/{id}/transition) and other ETag-guarded
    # mutations. Production serves SPA + API same-origin (no preflight), but a
    # cross-origin client (split deployment, or local dev on separate ports)
    # needs `if-match` in the allowlist or the browser preflight 400s and the
    # mutation never fires. (Surfaced by the docs-uat cross-origin approvals run.)
    allow_headers=["authorization", "content-type", "if-match", "x-request-id"],
    # PR #14: surface Content-Disposition so the SPA can read the
    # operator-friendly filename of CSV streaming downloads (admin audit
    # export). Without this, axios cannot read the header and the browser
    # falls back to a synthetic filename.
    # `etag` is surfaced for the same optimistic-concurrency reason as the
    # `if-match` request header above: the approvals drawer reads the version
    # from the GET's `ETag` response header (approvalsApi.ts) and echoes it as
    # `If-Match` on the transition PATCH. Cross-origin, the browser hides a
    # response header from JS unless it is in expose_headers, so without this
    # the SPA reads an empty ETag and the PATCH 400s on an empty If-Match.
    expose_headers=["content-disposition", "etag"],
)

# Added LAST so it becomes the outermost middleware — wraps CORS preflight
# and exception-handler-generated responses too. (security-reviewer F1.)
app.add_middleware(SecurityHeadersMiddleware)

install_exception_handlers(app)
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # type: ignore[arg-type]

app.include_router(auth_router)
# Phase 8 PR #23: OAuth (GitHub + Google) demo SaaS sign-in. Endpoints live
# under /auth/oauth/{provider}/* and are PUBLIC (no JWT) — the whole point
# of OAuth is that the caller is anonymous.
app.include_router(oauth_router)
app.include_router(admin_router)
app.include_router(projects_router)
# Portfolio overview aggregate for the app-root Dashboard page. Read-only,
# JWT-required; every aggregate is scoped to the caller's accessible projects
# inside services.dashboard_service (super-admin → all; otherwise → own teams).
app.include_router(dashboard_router)
app.include_router(scans_router)
app.include_router(components_router)
app.include_router(vulnerabilities_router)
app.include_router(licenses_router)
# v2.2 Track C (c1): per-team / org dynamic license policy CRUD. The policy GATE
# wiring that consults these rows (and SPDX compound/adversarial hardening) is c2;
# this PR ships the data model + CRUD surface only.
app.include_router(license_policies_router)
app.include_router(obligations_router)
# W9-#58: Compliance unified grid (licenses × obligations in one view). The
# legacy /licenses and /obligations endpoints remain for the existing drawers;
# this endpoint is the single read backing the redesigned Compliance tab.
app.include_router(compliance_router)
app.include_router(approvals_router)
# M-3: team-scoped audit read. super_admin sees all; team_admin sees only the
# teams where they hold team_admin (scope enforced server-side from
# team_roles). The super-admin-only /v1/admin/audit (+ CSV export) stays as-is.
app.include_router(audit_router)
app.include_router(sbom_router)
# v2.1 Track A (A1): VEX document export (OpenVEX / CycloneDX-VEX) derived from
# the project's current finding triage. Read-only; basis for the A2 import
# round-trip test.
app.include_router(vex_router)
# Scan-gap G2: vulnerability PDF report download.
app.include_router(reports_router)
# Scan-gap G3.2: source-tree viewer (list dir + read file) over the per-scan
# tarball preserved in G3.1.
app.include_router(source_tree_router)
# v2.2-b2: npm manifest-remediation dry-run (compute the edited package.json +
# diff for vulnerable npm deps; no PR, no persistence — that is b3).
app.include_router(remediation_router)
# Phase 5 PR #16: API Key management + Webhook receivers (GitHub / GitLab).
# Webhook endpoints are PUBLIC (no JWT) but each delivery is HMAC-authenticated
# against a per-project shared secret stored in `projects.webhook_secret`.
app.include_router(api_keys_router)
# v2.2-b1: GitHub App credential storage + token-minting foundation. Team-scoped
# CRUD for a GitHub App's reversibly-encrypted PEM private key (Fernet at rest)
# and per-project installation opt-in links. Every endpoint requires JWT auth;
# fine-grained team_admin/member RBAC is enforced in services.github_app_service.
app.include_router(github_app_router)
app.include_router(webhooks_github_router)
app.include_router(webhooks_gitlab_router)
# Phase 5 PR #17: build-gate result + SCA PR-comment endpoints. Both routes
# accept JWT or API-key bearer tokens so CI runners can call them.
app.include_router(policy_gate_router)
# Chore A2: in-app notification center + per-user notification preferences.
# /v1/notifications and /v1/users/me/notification-prefs.
app.include_router(notifications_router)
app.include_router(users_me_router)
# Phase 2 PR #9: WebSocket gateway. The router declares the absolute path
# `/ws/scans/{scan_id}` (no prefix) so future ws routes can group themselves
# under the same router without nudging this include.
app.include_router(ws_router)
# v2.1 Track B (B1): PUBLIC, unauthenticated readiness probe GET /health/ready.
# It asserts the Postgres schema is at the Alembic HEAD (CLAUDE.md rule #12 —
# this is an explicit public exception, grouped under the OpenAPI `public` tag).
# Liveness (/health below) only proves the process is up; readiness gates worker
# / beat startup on a migrated schema. See api/v1/health.py for the contract.
app.include_router(health_router)


@app.get("/health", tags=["public"], summary="Liveness probe — PUBLIC, unauthenticated")
async def health() -> dict[str, object]:
    """Cheap PURE-LIVENESS probe used by docker-compose / k8s liveness checks.

    PUBLIC / unauthenticated (CLAUDE.md rule #12 explicit exception). This proves
    only that the uvicorn process is accepting requests — it does NOT touch the
    database and says nothing about schema state. For "is the schema migrated and
    safe to serve traffic / start workers", use GET /health/ready (api/v1/health.py).

    v2.1 Track B (B5): also surfaces ``demo_read_only`` so the SPA can render the
    read-only banner and disable write actions without needing a separate build.
    The flag is resolved at request time (CLAUDE.md rule #11), so the same image
    behaves correctly whether DEMO_READ_ONLY is set or not.
    """
    return {"status": "ok", "demo_read_only": demo_read_only()}
