"""
Regression tests for the RFC 7807 exception handlers — security-reviewer F2.

Closes CWE-209 (Generation of Error Message Containing Sensitive Information):
Pydantic v2's ``RequestValidationError.errors()`` includes an ``input`` field
that echoes the offending user-provided value back to the client. The default
behaviour of FastAPI passes that through into the 422 response body. A user
who pastes a credential into the wrong field, or simply types a long PII
string, will see the value reflected in the JSON error body — which is then
captured by client logs, browser history, error reporting tooling, etc.

The fix is in ``core/errors.py:_redact_validation_errors``: every error row's
``input`` key is replaced with the sentinel ``"<redacted>"`` before
serialization. The diagnostic fields (``loc``, ``msg``, ``type``) are kept so
the response is still actionable.

These tests pin the contract directly against ``_redact_validation_errors``
(unit) and against the live FastAPI app (integration) for both adversarial
inputs (PII shapes, oversized strings, control characters) and benign ones
(typed coercion failure).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from core.errors import (
    _REDACTED,
    PROBLEM_CONTENT_TYPE,
    _redact_validation_errors,
    install_exception_handlers,
)

# ---------------------------------------------------------------------------
# Unit — _redact_validation_errors
# ---------------------------------------------------------------------------


def test_redact_drops_input_msg_ctx_replaces_with_sentinel() -> None:
    """input, msg, and ctx are all replaced with the sentinel (M1 — CWE-209).

    field_validator implementations can embed the bad value in the message
    string (``f"invalid email: {value}"``).  Sanitizing all three fields
    prevents PII / credential fragments from round-tripping back to clients
    even when custom validators are used.  Structural fields (loc, type, url)
    are kept so callers can still identify which field failed.
    """
    errors = [
        {
            "type": "string_too_short",
            "loc": ("body", "password"),
            "msg": "String should have at least 12 characters",
            "input": "hunter2",  # raw credential — must NOT round-trip
            "ctx": {"min_length": 12},
        }
    ]
    sanitized = _redact_validation_errors(errors)
    assert len(sanitized) == 1
    assert sanitized[0]["input"] == _REDACTED
    assert sanitized[0]["msg"] == _REDACTED
    assert sanitized[0]["ctx"] == _REDACTED
    # Structural fields preserved so callers know *which* field failed.
    assert sanitized[0]["loc"] == ("body", "password")
    assert sanitized[0]["type"] == "string_too_short"


def test_redact_sanitizes_msg_even_without_input_key() -> None:
    """Rows without an input key still have msg/ctx sanitized."""
    errors = [
        {
            "type": "missing",
            "loc": ("body", "email"),
            "msg": "Field required",
        }
    ]
    sanitized = _redact_validation_errors(errors)
    assert len(sanitized) == 1
    assert sanitized[0]["msg"] == _REDACTED
    assert "input" not in sanitized[0]
    assert sanitized[0]["loc"] == ("body", "email")
    assert sanitized[0]["type"] == "missing"


def test_redact_handles_non_dict_rows_defensively() -> None:
    """A non-mapping row (defensive path) is wrapped, not crashed."""
    sanitized = _redact_validation_errors(["unexpected"])
    assert len(sanitized) == 1
    assert sanitized[0]["input"] == _REDACTED
    assert sanitized[0]["msg"] == _REDACTED


def test_redact_does_not_mutate_caller_list() -> None:
    """Side-effect freedom: the helper must not mutate the input."""
    original: list[dict[str, Any]] = [
        {"type": "x", "loc": ("a",), "msg": "m", "input": "secret"}
    ]
    snapshot = [dict(row) for row in original]
    _redact_validation_errors(original)
    assert original == snapshot


# ---------------------------------------------------------------------------
# Integration — full FastAPI app round-trip
# ---------------------------------------------------------------------------


class _ValidateBody(BaseModel):
    """Module-level body model — see fixture comment for why this is hoisted.

    The constraints are deliberately niche so that any of the adversarial
    values below trigger a validation error: ``password`` must be exactly
    a SHA-256 hex digest. Real passwords (and PII / CRLF / RTL / SQL /
    JS-scheme strings) all fail this constraint, which forces the response
    through the redaction path we want to test.
    """

    email: str = Field(min_length=5)
    # 64 hex chars only. None of the adversarial inputs match.
    password: str = Field(pattern=r"^[0-9a-f]{64}$")


@pytest.fixture
def client() -> TestClient:
    """
    Build a tiny FastAPI app whose only purpose is to surface validation
    errors and force them through the production exception handler.

    We do NOT mount the full ``main:app`` — keeping the surface minimal makes
    the test independent of unrelated startup work (DB pool, Redis, etc.).

    The body model is defined at module scope (``_ValidateBody``) on purpose:
    FastAPI's "is this a Pydantic body model?" check pulls the type's
    ``__module__`` to disambiguate body vs. query parameters, and a class
    defined inside a fixture can be misclassified as a query bag.
    """
    app = FastAPI()
    install_exception_handlers(app)

    @app.post("/validate")
    async def _validate(body: _ValidateBody) -> dict[str, str]:
        return {"ok": body.email}

    return TestClient(app, raise_server_exceptions=False)


# Adversarial inputs (memory: feedback_adversarial_input_parametrize). Each
# pair is a value that MUST be redacted in the response body. These are
# untrusted values our 422 path could otherwise echo verbatim.
@pytest.mark.parametrize(
    "label,payload",
    [
        ("plaintext_credential_lookalike", {"email": "ok@example.com", "password": "hunter2"}),
        ("javascript_scheme", {"email": "ok@example.com", "password": "javascript:alert(1)"}),
        ("oversized_string", {"email": "ok@example.com", "password": "A" * 5000}),
        ("crlf_injection", {"email": "ok@example.com", "password": "abc\r\nSet-Cookie: x=y"}),
        ("null_bytes", {"email": "ok@example.com", "password": "abc\x00def"}),
        ("rtl_override", {"email": "ok@example.com", "password": "‮secret"}),
        ("sql_keywords", {"email": "ok@example.com", "password": "OR 1=1 --"}),
        ("pii_email_in_password", {"email": "ok@example.com", "password": "alice@example.com"}),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_validation_error_redacts_input_for_adversarial_payload(
    client: TestClient, label: str, payload: dict[str, str]
) -> None:
    """
    Every adversarial value MUST be replaced with ``<redacted>`` in the 422
    response body. The diagnostic ``loc`` / ``msg`` / ``type`` fields stay.
    """
    response = client.post("/validate", json=payload)
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)

    body = response.json()
    assert body["status"] == 422
    assert "errors" in body

    raw = response.text  # exact serialized response
    # The adversarial value must NOT appear anywhere in the body. The
    # sentinel takes its place.
    sensitive_value = payload["password"]
    if len(sensitive_value) >= 6:  # avoid trivially-collidable substrings
        assert sensitive_value not in raw, (
            f"adversarial value for {label!r} echoed back: {raw!r}"
        )

    # The redaction sentinel is what we expect to see for the password row.
    password_rows = [
        e
        for e in body["errors"]
        if isinstance(e, dict) and "loc" in e and "password" in tuple(e["loc"])
    ]
    assert password_rows, f"missing password validation row in {body['errors']!r}"
    for row in password_rows:
        # ``input`` is either redacted or absent — never the raw value.
        assert row.get("input", _REDACTED) == _REDACTED


def test_validation_error_diagnostic_fields_still_useful(client: TestClient) -> None:
    """
    Structural fields (type, loc) are intact — clients can still see WHICH
    field failed and the error category (type), just not the value (input),
    message template (msg), or validator context (ctx).
    """
    response = client.post("/validate", json={"email": "ok@example.com", "password": "short"})
    assert response.status_code == 422
    body = response.json()
    rows = body["errors"]
    pwd_rows = [r for r in rows if "password" in tuple(r.get("loc", ()))]
    assert pwd_rows
    row = pwd_rows[0]
    assert row.get("type")  # Pydantic v2 error tag, e.g. string_pattern_mismatch
    assert row.get("loc") == ["body", "password"]
    # msg and ctx are sanitized (M1); input is absent or redacted
    assert row.get("msg") == _REDACTED
    assert row.get("input", _REDACTED) == _REDACTED


def test_validation_error_keeps_problem_envelope(client: TestClient) -> None:
    """RFC 7807 envelope is intact — title / detail / status / instance."""
    response = client.post("/validate", json={"email": "x", "password": "x"})
    assert response.status_code == 422
    body = response.json()
    assert body["title"] == "Validation Error"
    assert body["status"] == 422
    assert body["detail"] == "One or more request parameters were invalid."
    assert body["instance"] == "/validate"
    assert body["type"] == "about:blank"
