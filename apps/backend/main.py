# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
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
    about_router,
    admin_router,
    api_keys_router,
    approvals_router,
    audit_router,
    auth_router,
    compliance_router,
    component_intake_router,
    components_router,
    dashboard_router,
    external_packages_router,
    gate_policies_router,
    github_app_router,
    health_router,
    inventory_router,
    license_policies_router,
    licenses_router,
    metrics_router,
    notice_templates_router,
    notification_routing_router,
    notifications_router,
    oauth_router,
    obligations_router,
    organization_verdicts_router,
    policy_gate_router,
    projects_router,
    remediation_router,
    report_format_templates_router,
    reports_router,
    saved_searches_router,
    sbom_router,
    scan_schedules_router,
    scans_router,
    search_results_router,
    search_router,
    service_accounts_router,
    source_tree_router,
    transition_approvals_router,
    user_anonymisation_router,
    users_me_router,
    vex_router,
    vulnerabilities_router,
    webhooks_github_router,
    webhooks_gitlab_router,
    ws_router,
)
from core.audit import install_audit_listeners
from core.config import (
    api_key_hmac_secret,
    app_env,
    cors_allowed_origins,
    demo_allow_sandbox_scans,
    demo_read_only,
    log_level,
    secret_key,
    validate_cors_origins,
    validate_demo_sandbox_limits,
)
from core.connection_budget import current_process_budget, log_if_over_budget
from core.db import build_engine, build_session_factory
from core.errors import install_exception_handlers
from core.logging import configure_logging
from core.login_throttle import close_client as close_login_throttle_client
from core.middleware import (
    AuditContextMiddleware,
    DemoReadOnlyMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)
from core.openapi import install_openapi
from core.ratelimit import limiter, rate_limit_exceeded_handler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(level=log_level())
    log = structlog.get_logger("startup")
    log.info("backend_starting", app_env=app_env())

    # C-1: fail fast if SECRET_KEY is missing/short/a template value in non-dev
    # environments. secret_key() raises RuntimeError; we let it propagate so the
    # process crashes on boot rather than booting with a weak key.
    secret_key()

    # The other signing key, checked here for the same reason. It is otherwise
    # read lazily, on the first request that presents an API key, so a
    # deployment configured with a template value booted healthy, passed its
    # health probe and its install smoke, and then 500ed every CI call it was
    # asked to authenticate. The failure landed on the pipelines rather than on
    # whoever made the change, at an arbitrary later time.
    api_key_hmac_secret()

    # M-1 (security review): if the public-demo sandbox carve-out is on but the
    # safe-limit overlay was not applied, crash the boot rather than silently
    # serving the sandbox with production defaults. No-op when the carve-out is
    # off (production, plain read-only demo). Raises RuntimeError on violation.
    validate_demo_sandbox_limits()

    engine = build_engine()
    app.state.engine = engine
    session_factory = build_session_factory(engine)
    app.state.session_factory = session_factory

    # Install the audit-log SQLAlchemy event listener now that we have a
    # session factory bound. Listeners are deduplicated inside the helper so
    # repeated starts (tests + uvicorn reloader) do not double-fire.
    install_audit_listeners(session_factory)

    # ER49: surface what the connected role can DO at boot, so an operator
    # verifying the install can see whether the runtime is limited to DML.
    #
    # This replaces a check that refused to start when DATABASE_URL_APP was set
    # and the role was not `trustedoss_app`. That variable is populated on every
    # deployment (compose defaults it from DATABASE_URL; the chart always writes
    # it), so the check fired on deployments that had never configured
    # separation and could not fire on the collapse it was written for, where
    # the DSN user and current_user are both the owner and agree. See
    # core.db_role for the full reasoning.
    from sqlalchemy import text as _sql_text

    from core.db_role import (
        DDL_PROBE_SQL,
        evaluate_db_role,
        require_db_role_separation,
    )

    async with engine.connect() as _conn:
        _role = (await _conn.execute(_sql_text("SELECT current_user"))).scalar()
        # W2: connection-budget boot check. Read Postgres' OWN configured
        # ceiling (not a guess) so the warning is accurate even when an
        # operator has raised max_connections for their tier. See
        # core.connection_budget for the formula and the CONN_BUDGET_* env
        # vars this estimate depends on.
        _max_conns_row = await _conn.execute(_sql_text("SHOW max_connections"))
        _max_connections = int(_max_conns_row.scalar() or 0)
    log.info("db.role.connected", role=_role)

    # The probe gets its OWN connection, and that is the whole point rather
    # than tidiness. A failing statement aborts the transaction it runs in, so
    # sharing the connection above meant a probe error left every following
    # statement raising InFailedSQLTransactionError and the app refusing to
    # start. The check would then have become the outage it exists to warn
    # about. Discarding a poisoned connection costs one round trip.
    try:
        async with engine.connect() as _probe_conn:
            _holds_ddl = bool(
                (await _probe_conn.execute(_sql_text(DDL_PROBE_SQL))).scalar()
            )
    except Exception as _probe_exc:  # noqa: BLE001 - must never fail the boot
        # A missing table, a restricted role or a connection fault makes the
        # answer unknown, not fatal. evaluate_db_role decides what unknown
        # means, and only strict mode treats it as a refusal.
        log.warning("db.role.probe_failed", error=str(_probe_exc)[:200])
        _holds_ddl = None

    _verdict = evaluate_db_role(
        role=str(_role),
        holds_ddl=_holds_ddl,
        strict=require_db_role_separation(),
    )
    getattr(log, _verdict.level)(_verdict.event, detail=_verdict.message)
    if _verdict.fatal:
        raise RuntimeError(_verdict.message)

    if _max_connections > 0:
        _budget = current_process_budget(max_connections=_max_connections)
        log_if_over_budget(log, _budget)

    # ER56: how many findings the CURRENT SLA windows leave overdue, logged
    # beside the windows that produced the number.
    #
    # The windows are environment variables, so narrowing one takes a restart,
    # and the restart is therefore the moment the policy changes. Everything
    # that goes overdue by that change went overdue in the past, which is what
    # `vuln_sla_sweep`'s trailing window is built to exclude, so nobody is
    # told. This does not tell anybody either, but it leaves something to find:
    # two consecutive starts read as "the windows went 30 to 7 and the overdue
    # count went 40 to 380". The windows are in the same line on purpose,
    # because a count without them says nothing about why it moved.
    #
    # Counted with one aggregate rather than by reading rows, and that is what
    # makes the number exact. A row scan has to bound itself somewhere, and a
    # bound erases the signal precisely where it matters: a deployment already
    # over the cap reports the same truncated figure before and after the
    # change, so the two lines this exists to be compared against read the
    # same. The deployments with the most findings are the ones where
    # narrowing a window is the biggest surprise.
    #
    # Its own connection, and never fatal, for the reasons in the role probe
    # above: this is an observation, not a gate, and a census that failed the
    # boot would be worse than no census.
    # What our own outbound HTTPS calls will trust, stated on every boot.
    # An operator behind a private certificate authority changes this, and the
    # same variable that ADDS one for the Go tools REPLACES it here, so a
    # deployment can end up with working scans and silently unverifiable
    # feeds. Reported unconditionally rather than only on suspicion: trusting
    # only a private authority is a legitimate configuration, and a warning
    # that fires on a correct setup is one somebody turns off.
    from core.tls_trust import log_trust_store

    log_trust_store(process="api")

    try:
        from core.config import vuln_sla_days
        from services.vulnerability_service import overdue_counts_by_severity

        async with session_factory() as _census_session:
            _counts = await overdue_counts_by_severity(_census_session)
        log.info(
            "vuln_sla.overdue_at_boot",
            windows={
                sev: vuln_sla_days(sev)
                for sev in ("critical", "high", "medium", "low")
            },
            overdue=_counts,
            total=sum(_counts.values()),
        )
    except Exception as _census_exc:  # noqa: BLE001 - must never fail the boot
        log.warning("vuln_sla.overdue_census_failed", error=str(_census_exc)[:200])

    # E22b: while a key rotation is in progress, how many stored secrets are
    # still on an older key. Zero is the condition for removing the old key,
    # and removing it early makes those rows unreadable for good.
    #
    # The person who runs the re-encryption and the person who edits the
    # environment are often not the same person, and the command's output was
    # only ever on the first one's terminal. This puts the number where the
    # second one is already looking.
    #
    # Only when more than one key is configured. With one key there is nothing
    # to be stale relative to, and scanning every encrypted column on every
    # boot to report a guaranteed zero is work that buys nothing.
    try:
        from core.crypto import configured_keys
        from services.key_rotation_service import count_stale

        _keys = len(configured_keys())
        if _keys > 1:
            async with session_factory() as _rotation_session:
                _rotation = await count_stale(_rotation_session)
            log.info(
                "key_rotation.stale_at_boot",
                keys=_keys,
                stale=_rotation.stale_total,
                by_column={
                    c.column.label: c.stale for c in _rotation.columns if c.stale
                },
                detail=(
                    "rows still encrypted under an older key. Removing the "
                    "oldest key while this is above zero makes them "
                    "permanently unreadable."
                    if _rotation.stale_total
                    else "nothing is on an older key; removing the oldest is safe"
                ),
            )
    except Exception as _rotation_exc:  # noqa: BLE001 - must never fail the boot
        log.warning("key_rotation.census_failed", error=str(_rotation_exc)[:200])

    try:
        yield
    finally:
        await engine.dispose()
        # The sign-in throttle keeps one Redis client for the process rather
        # than opening one per request on the pre-authentication path; this is
        # where it gets closed.
        await close_login_throttle_client()
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
# and exception-handler-generated responses too. (security review finding.)
app.add_middleware(SecurityHeadersMiddleware)

install_exception_handlers(app)
# The spec has to describe what those handlers return; see core/openapi.py.
install_openapi(app)
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
# Cross-project global search (H-2). Team isolation enforced in
# services.search_service via the core.authz.team_scope_filter choke-point
# (super-admin → all projects; otherwise → own teams only).
app.include_router(search_router)
# Paged/faceted search for the full search page (S3). Separate from the
# palette endpoint above so its response shape stays fixed.
app.include_router(search_results_router)
# Saved searches — user-scoped bookmarks for the search page (S3).
app.include_router(saved_searches_router)
# Pre-adoption catalog lookup (deps.dev): packages / advisories nothing has
# scanned yet. Always registered; core.config.external_package_lookup_enabled
# gates each call at 404 rather than at router inclusion.
app.include_router(external_packages_router)

# Organization-wide component inventory (S2). Same team-isolation
# choke-point as search; every route fans out across projects.
app.include_router(inventory_router)

app.include_router(scans_router)
app.include_router(scan_schedules_router)
app.include_router(components_router)
app.include_router(vulnerabilities_router)
app.include_router(licenses_router)
# v2.2 Track C (c1): per-team / org dynamic license policy CRUD. The policy GATE
# wiring that consults these rows (and SPDX compound/adversarial hardening) is c2;
# this PR ships the data model + CRUD surface only.
app.include_router(license_policies_router)
# B1/B2: the same org/team shape for what blocks a build. Separate table and
# router from the licence policy because the two answer different questions
# even though both feed one verdict.
app.include_router(gate_policies_router)
app.include_router(notice_templates_router)
app.include_router(notification_routing_router)
# N10: reserved in PUBLIC_PATHS long before anything served it. Off by
# default, and off answers 404 so a deployment that publishes nothing looks
# like one without the feature.
app.include_router(metrics_router)
app.include_router(organization_verdicts_router)
app.include_router(service_accounts_router)
app.include_router(transition_approvals_router)
app.include_router(user_anonymisation_router)
app.include_router(obligations_router)
# W9-#58: Compliance unified grid (licenses × obligations in one view). The
# legacy /licenses and /obligations endpoints remain for the existing drawers;
# this endpoint is the single read backing the redesigned Compliance tab.
app.include_router(compliance_router)
app.include_router(component_intake_router)
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
# N22: organization defaults for the report's header/label/column selection.
app.include_router(report_format_templates_router)
# Scan-gap G3.2: source-tree viewer (list dir + read file) over the per-scan
# tarball preserved in G3.1.
app.include_router(source_tree_router)
# v2.2-b2: npm manifest-remediation dry-run (compute the edited package.json +
# diff for vulnerable npm deps; no PR, no persistence — that is b3).
app.include_router(remediation_router)
# Phase 5 PR #16: API Key management + Webhook receivers (GitHub / GitLab).
# Webhook endpoints are PUBLIC (no JWT) but each delivery is HMAC-authenticated
# against a per-project shared secret stored, as ciphertext, in
# `projects.webhook_secret_encrypted`.
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
# License notices + product identity, readable from the portal itself.
# JWT-gated but role-free: every user of a deployment may read its notices.
app.include_router(about_router)
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

    Also surfaces ``demo_sandbox_scans`` — only true when the read-only demo has
    the opt-in sandbox carve-out enabled (both ``DEMO_READ_ONLY`` and
    ``DEMO_ALLOW_SANDBOX_SCANS``). The SPA uses it to re-enable the bounded
    scan / SBOM-ingest affordances on the Demo Sandbox project while keeping
    every other write disabled.
    """
    read_only = demo_read_only()
    return {
        "status": "ok",
        "demo_read_only": read_only,
        "demo_sandbox_scans": read_only and demo_allow_sandbox_scans(),
    }
