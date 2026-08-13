# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Make the generated OpenAPI spec describe the errors we actually return.

Every error leaves this API through ``core.errors.problem_response``, which
emits RFC 7807 ``application/problem+json``. None of that reached the spec:
FastAPI declares a 422 with ``application/json`` and its own
``HTTPValidationError``, and nothing else. So the document promised one shape
and the server sent another, on every endpoint.

Schema-based fuzzing (``.github/workflows/schemathesis-fuzz.yml``) reported it
as 42 undocumented content types and 62 undocumented status codes. The counts
are misleading: it is not 104 separate mistakes, it is one gap repeated
everywhere, which is why this is fixed in one place rather than across 100+
route decorators.

What gets declared, and on what basis:

* ``422`` - already emitted by FastAPI's validation handler, so it exists on
  every operation that takes input. Only the media type and schema are
  corrected here.
* ``401`` - on everything behind the auth dependency, plus the credential
  endpoints in ``CREDENTIAL_PATHS``. Those take no token but answer 401 when
  the credentials are wrong, which the first pass at this missed.
* ``403`` - on every authenticated operation. Team scoping can reject a caller
  on a collection route as readily as on a single resource, which the first
  pass also missed by keying this to path parameters.
* ``404`` - on operations with a path parameter (the resource may not exist,
  or may not be visible to this caller) and on ``/v1/admin/*``, where
  ``require_super_admin_or_404`` deliberately answers 404 rather than
  confirming a route exists.

Declaring a status an endpoint never actually returns is harmless for a client
and for the fuzzer; failing to declare one it does return is what breaks
generated clients and contract tests. Where the choice is not obvious this
errs toward declaring.

Left alone on purpose: 400, 409 and 503 showed up on a handful of operations
each, and unlike the above they are specific to what those endpoints do. They
belong in the route decorators, next to the code that raises them.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from core.errors import PROBLEM_CONTENT_TYPE

PROBLEM_SCHEMA_NAME = "ProblemDetail"

# RFC 7807 members. `problem_response` also merges caller-supplied extensions
# (for example `oauth_provider_disabled`), which is why this is not sealed with
# additionalProperties: false.
PROBLEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "Problem Detail",
    "description": (
        "RFC 7807 problem detail. Every error response from this API uses this "
        "shape with the media type application/problem+json."
    ),
    "properties": {
        "type": {
            "type": "string",
            "description": "URI identifying the problem type.",
            "example": "about:blank",
        },
        "title": {"type": "string", "description": "Short, human-readable summary."},
        "status": {"type": "integer", "description": "HTTP status code."},
        "detail": {
            "type": ["string", "null"],
            "description": "Explanation specific to this occurrence.",
        },
        "instance": {
            "type": "string",
            "description": "URI reference identifying the specific occurrence.",
        },
    },
    "required": ["type", "title", "status", "instance"],
}

# Routes that answer without a credential. Everything else goes through the
# auth dependency and can return 401.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/health/ready",
        "/health/live",
        "/metrics",
        "/auth/login",
        "/auth/register",
        "/auth/refresh",
        "/auth/password-reset",
        "/auth/password-reset/confirm",
    }
)

# Public in the sense that they take no bearer token, but they still answer 401
# when the credentials they are given do not check out.
CREDENTIAL_PATHS: frozenset[str] = frozenset(
    {
        "/auth/login",
        "/auth/refresh",
        "/auth/password-reset/confirm",
    }
)

_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def _problem_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            PROBLEM_CONTENT_TYPE: {
                "schema": {"$ref": f"#/components/schemas/{PROBLEM_SCHEMA_NAME}"}
            }
        },
    }


def _is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    # OAuth redirect endpoints are entered by a browser without a bearer token.
    return path.startswith("/auth/oauth/")


def describe_error_responses(schema: dict[str, Any]) -> dict[str, Any]:
    """Add the problem+json error responses to every operation in `schema`."""
    schema.setdefault("components", {}).setdefault("schemas", {})[
        PROBLEM_SCHEMA_NAME
    ] = PROBLEM_SCHEMA

    for path, methods in schema.get("paths", {}).items():
        has_path_param = "{" in path
        is_admin = path.startswith("/v1/admin/")
        for method, operation in methods.items():
            if method.lower() not in _METHODS or not isinstance(operation, dict):
                continue
            responses = operation.setdefault("responses", {})

            # FastAPI's own 422 points at HTTPValidationError as application/json.
            # The handler that actually runs returns problem+json.
            if "422" in responses:
                responses["422"] = _problem_response("Validation error")

            authenticated = not _is_public(path)
            if authenticated or path in CREDENTIAL_PATHS:
                responses.setdefault("401", _problem_response("Not authenticated"))

            if authenticated:
                responses.setdefault(
                    "403", _problem_response("Not permitted for this caller")
                )

            if has_path_param or is_admin:
                responses.setdefault("404", _problem_response("Not found"))

    return schema


def install_openapi(app: FastAPI) -> None:
    """Replace `app.openapi` so the served spec carries the error responses."""

    def _openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        app.openapi_schema = describe_error_responses(schema)
        return app.openapi_schema

    app.openapi = _openapi  # type: ignore[method-assign]
