# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""core.openapi.describe_security (C7).

Every auth dependency in this codebase reads the Authorization header
straight off Request rather than through a fastapi.security class, so
FastAPI had nothing to base components.securitySchemes on and the served
spec never described how to authenticate at all. These pin the fix: the
scheme is registered once, and every operation carries the right
`security` value for whether core.openapi.PUBLIC_PATHS says it needs a
token.
"""

from __future__ import annotations

from typing import Any

from core.openapi import (
    BEARER_SECURITY_SCHEME_NAME,
    describe_security,
)


def _schema(paths: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"paths": paths}


def test_registers_the_bearer_scheme() -> None:
    schema = describe_security(_schema({}))

    scheme = schema["components"]["securitySchemes"][BEARER_SECURITY_SCHEME_NAME]
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"


def test_protected_operation_requires_the_bearer_scheme() -> None:
    schema = describe_security(
        _schema({"/v1/projects": {"get": {"responses": {}}}})
    )

    op = schema["paths"]["/v1/projects"]["get"]
    assert op["security"] == [{BEARER_SECURITY_SCHEME_NAME: []}]


def test_public_path_carries_no_security_requirement() -> None:
    schema = describe_security(_schema({"/health": {"get": {"responses": {}}}}))

    op = schema["paths"]["/health"]["get"]
    assert op["security"] == []


def test_credential_path_is_unauthenticated_too() -> None:
    """/auth/login takes no bearer token even though it answers 401 on a
    wrong password: that 401 is a separate concern from what this function
    describes (see describe_error_responses' CREDENTIAL_PATHS)."""
    schema = describe_security(
        _schema({"/auth/login": {"post": {"responses": {}}}})
    )

    op = schema["paths"]["/auth/login"]["post"]
    assert op["security"] == []


def test_non_http_methods_and_non_operation_entries_are_left_alone() -> None:
    schema = describe_security(
        _schema(
            {
                "/v1/projects": {
                    "get": {"responses": {}},
                    "parameters": [{"name": "not an operation"}],
                }
            }
        )
    )

    assert "security" not in schema["paths"]["/v1/projects"]["parameters"]


def test_the_live_app_schema_documents_every_operation() -> None:
    """End-to-end: app.openapi() actually runs describe_security via
    install_openapi, and every real operation gets a security list."""
    from main import app

    schema = app.openapi()

    assert BEARER_SECURITY_SCHEME_NAME in schema["components"]["securitySchemes"]

    missing = [
        f"{method.upper()} {path}"
        for path, methods in schema["paths"].items()
        for method, op in methods.items()
        if method in {"get", "post", "put", "patch", "delete"}
        and isinstance(op, dict)
        and "security" not in op
    ]
    assert not missing, f"operations with no security declared: {missing}"
