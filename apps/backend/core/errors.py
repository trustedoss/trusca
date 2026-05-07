"""
RFC 7807 Problem Details exception handlers.

Quality standard §4 (CLAUDE.md): every 4xx/5xx response MUST use
`application/problem+json` with the required fields:
  - type     (URI; default "about:blank")
  - title    (short, human-readable summary)
  - status   (HTTP status code)
  - detail   (longer explanation, may be null)
  - instance (URI of the specific occurrence — we use request.url.path)

Domain-specific extension fields use snake_case and ride alongside the
standard ones.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import structlog
from fastapi import FastAPI, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

PROBLEM_CONTENT_TYPE = "application/problem+json"

# Sentinel emitted in place of any user-provided value that Pydantic v2's
# ``RequestValidationError.errors()`` echoes back via the ``input`` key.
# CWE-209: returning the offending input verbatim to the caller can leak PII
# (email/full_name typos), pasted credentials (a request that mistakenly puts
# a token in the wrong field), or simply hand a trivial reflected-XSS oracle
# to whoever talks to the API. Pydantic's ``input`` is convenient for local
# debugging but is the wrong default for a hardened HTTP surface.
_REDACTED = "<redacted>"


def _redact_validation_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """
    Replace the ``input`` field on every Pydantic v2 validation error with a
    fixed sentinel. The other diagnostic fields (``loc``, ``msg``, ``type``,
    ``ctx``, ``url``) are preserved verbatim so the client can still react.

    The function is deliberately defensive: ``errors()`` rows are typed as
    ``ErrorDetails`` (TypedDict) but at runtime Pydantic returns a list of
    plain dicts and may add or omit keys across versions. We treat each row as
    an opaque mapping, copy non-``input`` keys, and overwrite ``input``.
    Non-mapping rows are passed through (best effort) so future Pydantic
    surprises do not turn a 422 into a 500.
    """
    out: list[dict[str, Any]] = []
    for entry in errors:
        if not isinstance(entry, dict):
            # Defensive: Pydantic could in principle hand back a non-dict here.
            # Wrap in a minimal envelope so we still return JSON-shaped data.
            out.append({"msg": str(entry), "input": _REDACTED})
            continue
        sanitized = {k: v for k, v in entry.items() if k != "input"}
        # Preserve the key so callers can still see "yes, an input was rejected"
        # without exposing its content.
        if "input" in entry:
            sanitized["input"] = _REDACTED
        out.append(sanitized)
    return out


def problem_response(
    *,
    status_code: int,
    title: str,
    detail: str | None,
    instance: str,
    type_: str = "about:blank",
    **extensions: object,
) -> JSONResponse:
    body: dict[str, object] = {
        "type": type_,
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": instance,
    }
    body.update(extensions)
    return JSONResponse(
        body,
        status_code=status_code,
        media_type=PROBLEM_CONTENT_TYPE,
    )


def install_exception_handlers(app: FastAPI) -> None:
    log = structlog.get_logger("errors")

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        title = exc.detail if isinstance(exc.detail, str) else "HTTP Error"
        return problem_response(
            status_code=exc.status_code,
            title=title,
            detail=title if isinstance(exc.detail, str) else None,
            instance=request.url.path,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Pydantic v2 stores raw exception instances (e.g. ValueError raised
        # from a field_validator) inside `errors()[i].ctx.error`; the default
        # JSON encoder cannot serialize those. jsonable_encoder unwraps them.
        #
        # Security (CWE-209, security-reviewer F2): Pydantic also echoes the
        # offending request value at ``errors[i].input``. The caller already
        # knows what they sent; reflecting it back into the response body is
        # at best noise and at worst a PII / credential leak (a token sent to
        # the wrong field would round-trip to the response headers + log
        # sinks otherwise). We replace ``input`` with a fixed sentinel BEFORE
        # serialization. ``loc`` / ``msg`` / ``type`` are preserved so callers
        # still get actionable validation hints.
        sanitized = _redact_validation_errors(exc.errors())
        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Validation Error",
            detail="One or more request parameters were invalid.",
            instance=request.url.path,
            errors=jsonable_encoder(sanitized),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # log with full traceback; the JSON response stays generic to avoid leaking
        log.error("unhandled_exception", exc_info=exc, path=request.url.path)
        return problem_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Internal Server Error",
            detail="An unexpected error occurred. The incident has been logged.",
            instance=request.url.path,
        )
