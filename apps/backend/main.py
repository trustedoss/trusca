"""
FastAPI application entrypoint.

Wires together:
- structlog JSON logging
- request_id middleware
- RFC 7807 exception handlers
- async SQLAlchemy engine bound to app.state during the lifespan
- /health endpoint (used by docker-compose healthchecks and probes)

Phase 0 PR #2 deliberately keeps the surface tiny — domain routers (auth,
projects, scans, …) land in their respective Phases.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import (
    app_env,
    cors_allowed_origins,
    log_level,
)
from core.db import build_engine, build_session_factory
from core.errors import install_exception_handlers
from core.logging import configure_logging
from core.middleware import RequestIDMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(level=log_level())
    log = structlog.get_logger("startup")
    log.info("backend_starting", app_env=app_env())

    engine = build_engine()
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)

    try:
        yield
    finally:
        await engine.dispose()
        log.info("backend_stopped")


app = FastAPI(
    title="TrustedOSS Portal API",
    version="0.0.1",
    description="Open-source enterprise SCA portal — bootstrap (Phase 0 PR #2).",
    lifespan=lifespan,
)

app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_exception_handlers(app)


@app.get("/health", tags=["system"], summary="Liveness probe")
async def health() -> dict[str, str]:
    """Cheap liveness probe used by docker-compose healthchecks."""
    return {"status": "ok"}
