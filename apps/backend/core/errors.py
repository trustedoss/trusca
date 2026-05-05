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

import structlog
from fastapi import FastAPI, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

PROBLEM_CONTENT_TYPE = "application/problem+json"


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
        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Validation Error",
            detail="One or more request parameters were invalid.",
            instance=request.url.path,
            errors=jsonable_encoder(exc.errors()),
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
