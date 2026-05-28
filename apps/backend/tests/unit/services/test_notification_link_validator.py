"""
Unit tests for ``services.notification_service._validate_link`` /
``_safe_link`` — Marathon bundle 4 (S / L1).

The Notification.link value is rendered into an SPA ``<a href>``; any
attacker-controlled value here that the validator misses becomes a
stored-XSS or open-redirect primitive against the user receiving the
alert. The validator policy (memory ``feedback_adversarial_input_parametrize``):

  - same-origin paths only — must start with a single ``/`` and not ``//``.
  - reject scheme injections (javascript:, data:, file:, mailto:).
  - reject control bytes (NUL, CR, LF) — log + header injection.
  - reject path traversal (``..`` segments) — directs to wrong screen.
  - reject query strings (``?``) and fragments (``#``) — open-redirect
    primitive when a downstream route consumes ``?return_to=`` etc.

Rejected values become ``None`` (the SPA renders the alert as plain
text) — fail-safe rather than fail-loud. The user still sees the alert.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "value",
    [
        "/projects",
        "/projects/01H1234567890ABC",
        "/admin/dt",
        "/notifications",
        "/projects/abc",
        "/.well-known/security.txt",
    ],
)
def test_validate_link_accepts_same_origin_path(value: str) -> None:
    from services.notification_service import _validate_link

    assert _validate_link(value) == value


@pytest.mark.parametrize(
    "value",
    [
        # Protocol / scheme injection.
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "mailto:attacker@example.com",
        "ftp://attacker.example.com/file",
        # Off-origin redirects (protocol-relative).
        "//evil.example.com/phish",
        "//evil.example.com",
        # Backslash variants — old WebKit treats as protocol-relative.
        "/\\evil.example.com",
        # Bare absolute URL.
        "http://evil.example.com",
        "https://evil.example.com/phish",
        # Relative path (not anchored to /).
        "projects",
        "../etc/passwd",
        "foo/bar",
        # Path traversal.
        "/projects/../../etc/passwd",
        "/projects/..",
        "/../etc/passwd",
        # Control bytes (CRLF / NUL).
        "/projects\r\nSet-Cookie: x=y",
        "/projects\nLocation: //evil.example.com",
        "/projects\x00",
        "/projects\x07",
        # Query / fragment — open-redirect primitive (M1 follow-up).
        "/projects?return_to=//evil.example.com",
        "/projects?next=javascript:alert(1)",
        "/dashboard#//evil.example.com",
        "/?next=https://evil.example.com",
        # Whitespace-only / empty.
        "",
        "   ",
    ],
)
def test_validate_link_rejects_unsafe_value(value: str) -> None:
    from services.notification_service import _validate_link

    assert _validate_link(value) is None, (
        f"expected validator to reject {value!r} as unsafe"
    )


@pytest.mark.parametrize("value", [None, 123, ["/projects"], {"href": "/projects"}])
def test_validate_link_rejects_non_string_input(value: object) -> None:
    from services.notification_service import _validate_link

    assert _validate_link(value) is None  # type: ignore[arg-type]


def test_safe_link_truncates_oversized_path() -> None:
    """Pipeline: validate → truncate at 512 chars."""
    from services.notification_service import _safe_link

    payload = "/projects/" + ("a" * 600)
    out = _safe_link(payload)
    assert out is not None
    assert len(out) <= 512


def test_safe_link_rejected_path_returns_none() -> None:
    """Validator failure short-circuits truncate."""
    from services.notification_service import _safe_link

    assert _safe_link("javascript:alert(1)") is None
    assert _safe_link("//evil.example.com") is None
    # M1 follow-up: query/fragment also rejected.
    assert _safe_link("/projects?return_to=//evil") is None
    assert _safe_link("/dashboard#//evil") is None
