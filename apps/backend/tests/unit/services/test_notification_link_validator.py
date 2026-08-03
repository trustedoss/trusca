"""
Unit tests for ``services.notification_service._validate_link`` /
``_safe_link`` — Marathon bundle 4 (S / L1).

The Notification.link value is rendered into an SPA ``<a href>``; any
attacker-controlled value here that the validator misses becomes a
stored-XSS or open-redirect primitive against the user receiving the
alert. The validator policy:

  - same-origin paths only — must start with a single ``/`` and not ``//``.
  - reject scheme injections (javascript:, data:, file:, mailto:).
  - reject control bytes (NUL, CR, LF) — log + header injection.
  - reject path traversal (``..`` segments) — directs to wrong screen.
  - fragments (``#``) rejected outright; query strings allowed ONLY when
    the query matches the strict charset ``[A-Za-z0-9_=&-]`` (X1 step 2
    amendment — SLA deep links / approvals ``?id=`` need a query, and the
    charset makes every known open-redirect / scheme payload inexpressible:
    no ``/`` for ``//evil``, no ``:`` for ``javascript:``, no ``.`` for
    hostnames, no ``%`` for percent-smuggling). Every pre-amendment
    adversarial case below is still rejected verbatim.

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
        # X1 SLA breach deep link (the amendment's motivating case).
        "/projects/9b02d841-8363-481c-9130-720550021642?tab=vulnerabilities&sla=overdue",
        # Approvals alert — built since the approvals feature shipped, but
        # silently degraded to NULL under the blanket ``?`` ban.
        "/approvals?id=9b02d841-8363-481c-9130-720550021642",
        # Charset edges: empty query, bare key, underscore/dash/equals/amp.
        "/projects?",
        "/projects?tab=vulnerabilities",
        "/projects?a_b-c=1&d=2",
        # Charset boundary documentation (moved out of the reject test so
        # every test asserts in ONE direction): without ':' / '.' these
        # cannot form a scheme or a hostname — admitting them is safe.
        "/projects?next=javascript",
        "/projects?host=evil-example-com",
    ],
)
def test_validate_link_accepts_allowlisted_query(value: str) -> None:
    """Query strings pass ONLY under the strict ``[A-Za-z0-9_=&-]`` charset."""
    from services.notification_service import _validate_link

    assert _validate_link(value) == value


def test_validate_link_strips_trailing_whitespace_contract() -> None:
    """``strip()`` runs BEFORE the fullmatch: a trailing newline is removed
    and the SANITIZED value is what gets stored — no CR/LF ever survives
    into the row (the ``$`` anchor's newline leniency is therefore moot)."""
    from services.notification_service import _validate_link

    assert _validate_link("/projects?tab=vulnerabilities\n") == (
        "/projects?tab=vulnerabilities"
    )


def test_validate_link_path_percent_acceptance_is_a_conscious_contract() -> None:
    """PIN (security review, low severity, X1 step 2): the ``%``/``.``/``:`` bans are
    QUERY-scoped — the path component accepts percent sequences, as it always
    has. Safe under the consumption contract (React Router ``navigate()``
    only — same-origin pushState, no href/location/e-mail sink). If this
    assertion bothers you because you added a NEW consumer of ``link``,
    tighten the validator first — do not silently rely on this behavior."""
    from services.notification_service import _validate_link

    assert _validate_link("/p/%2F%2Fevil") == "/p/%2F%2Fevil"


@pytest.mark.parametrize(
    "value",
    [
        # Redirect targets are inexpressible: '/' banned inside the query.
        "/projects?return_to=/evil",
        "/projects?next=/a/b",
        # Scheme payloads need ':' — banned.
        "/projects?next=javascript:alert(1)",
        # Hostnames need '.' — banned.
        "/projects?host=evil.example.com",
        # Percent-smuggling needs '%' — banned (upper, lower, double-encoded).
        "/projects?next=%2F%2Fevil",
        "/projects?next=%2f%2fevil",
        "/projects?next=%252F%252Fevil",
        # '+' / space / quotes / angle brackets — banned.
        "/projects?q=a+b",
        "/projects?q=a b",
        '/projects?q="x"',
        "/projects?q=<script>",
        # userinfo-confusion '@' and parameter-splitting ';' — banned.
        "/projects?u=a@evil",
        "/projects?a=1;b=2",
        # Backslash inside the query — banned.
        "/projects?r=\\evil",
        # Control chars / CRLF INSIDE the query (the pre-amendment cases
        # only covered the path side) — banned, and interior CR/LF is a
        # reject, not a strip (only leading/trailing whitespace strips).
        "/projects?a=1\r\nSet-Cookie: x=1",
        "/projects?a=\x00b",
        "/projects?a=\tb",
        # Unicode confusables for '/' and '.' (U+FF0F, U+FF0E, U+2044) —
        # the charset is ASCII-literal, no NFKC consumer exists.
        "/projects?next=／／evil",
        "/projects?host=evil．example．com",
        "/projects?next=⁄⁄evil",
        # Second '?' — banned ('?' not in the query charset).
        "/projects?a=1?b=2",
        # Fragment after a valid (or empty) query — '#' rejected everywhere.
        "/projects?tab=vulnerabilities#//evil.example.com",
        "/projects?#",
    ],
)
def test_validate_link_query_charset_rejects_attack_shapes(value: str) -> None:
    """Single-direction reject battery for the query charset (the harmless
    boundary forms live in the accept test above)."""
    from services.notification_service import _validate_link

    assert _validate_link(value) is None, (
        f"expected validator to reject {value!r} as unsafe"
    )


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
        # Query-borne open-redirect / scheme payloads (M1 follow-up; still
        # rejected verbatim after the X1 charset amendment — '/', ':' and
        # '.' are outside the query allowlist) + fragments (always banned).
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
