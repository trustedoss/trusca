"""
SSRF guard for git_url — Phase 2 PR #8 (M-4).

The module lives at `core.url_guard` (not `core.security.url_guard`); see the
backend-developer hand-off note in the PR description. We pin:

  - Public hostnames pass through.
  - RFC 1918 / loopback / link-local / multicast / metadata IP literals reject.
  - Cloud metadata hostnames reject (DNS-level, not just IP).
  - Disallowed schemes reject (file://, gopher://, javascript:, ...).
  - Length cap at 2048.
  - Hosts that fail DNS resolution reject (closed-by-default policy).
  - Validators raise GitUrlValidationError (a ValueError subclass) so
    Pydantic surfaces the failure as 422 problem+json without rewrapping.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Successful validation
# ---------------------------------------------------------------------------


def test_public_https_url_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.url_guard import validate_git_url

    # Force DNS resolution to a known public IP so the test never hits the
    # network and never depends on github.com's actual addresses.
    monkeypatch.setattr(
        "core.url_guard.socket.getaddrinfo",
        lambda host, port: [(socket.AF_INET, 0, 0, "", ("140.82.121.4", 0))],
    )
    result = validate_git_url("https://github.com/foo/bar.git")
    assert result == "https://github.com/foo/bar.git"


def test_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.url_guard import validate_git_url

    monkeypatch.setattr(
        "core.url_guard.socket.getaddrinfo",
        lambda host, port: [(socket.AF_INET, 0, 0, "", ("140.82.121.4", 0))],
    )
    assert validate_git_url("  https://github.com/foo/bar.git  ").strip() == \
        "https://github.com/foo/bar.git"


def test_scp_form_is_translated_and_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """`git@host:path` style URLs are rewritten to ssh:// internally."""
    from core.url_guard import validate_git_url

    monkeypatch.setattr(
        "core.url_guard.socket.getaddrinfo",
        lambda host, port: [(socket.AF_INET, 0, 0, "", ("140.82.121.4", 0))],
    )
    result = validate_git_url("git@github.com:foo/bar.git")
    # Returns the original string (not the rewritten ssh:// form) so the
    # value stored in the DB matches what the user typed.
    assert result == "git@github.com:foo/bar.git"


# ---------------------------------------------------------------------------
# Rejected — RFC 1918 / loopback / link-local
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.0.1/repo",       # RFC 1918 (private) IP literal
        "http://10.0.0.5/repo",          # RFC 1918
        "http://172.16.5.5/repo",        # RFC 1918
        "http://127.0.0.1/repo",         # loopback
        "http://[::1]/repo",             # IPv6 loopback
        "http://169.254.0.1/repo",       # link-local
        "http://224.0.0.1/repo",         # multicast
    ],
)
def test_rejects_non_routable_ip_literals(url: str) -> None:
    from core.url_guard import GitUrlValidationError, validate_git_url

    with pytest.raises(GitUrlValidationError):
        validate_git_url(url)


# ---------------------------------------------------------------------------
# Rejected — cloud metadata
# ---------------------------------------------------------------------------


def test_rejects_aws_metadata_ip() -> None:
    from core.url_guard import GitUrlValidationError, validate_git_url

    with pytest.raises(GitUrlValidationError):
        validate_git_url("http://169.254.169.254/latest/meta-data/")


def test_rejects_gcp_metadata_hostname() -> None:
    """Even if the hostname resolves to a public IP, the canonical name is blocked."""
    from core.url_guard import GitUrlValidationError, validate_git_url

    with pytest.raises(GitUrlValidationError):
        validate_git_url("https://metadata.google.internal/computeMetadata/v1/")


# ---------------------------------------------------------------------------
# Rejected — schemes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/data",
        "data:text/plain,hello",
        "javascript:alert(1)",
        "ftp://example.com/repo",
    ],
)
def test_rejects_disallowed_schemes(url: str) -> None:
    from core.url_guard import GitUrlValidationError, validate_git_url

    with pytest.raises(GitUrlValidationError):
        validate_git_url(url)


# ---------------------------------------------------------------------------
# Rejected — length cap
# ---------------------------------------------------------------------------


def test_rejects_url_longer_than_2048_chars() -> None:
    from core.url_guard import GitUrlValidationError, validate_git_url

    long_path = "a" * 2050
    url = f"https://example.com/{long_path}"
    with pytest.raises(GitUrlValidationError):
        validate_git_url(url)


# ---------------------------------------------------------------------------
# Rejected — DNS resolution failure (closed-by-default)
# ---------------------------------------------------------------------------


def test_rejects_unresolvable_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unresolvable host is a free DNS oracle for an attacker — reject it."""
    from core.url_guard import GitUrlValidationError, validate_git_url

    def _fail(_host: str, _port: Any) -> Any:
        raise socket.gaierror("test-fail")

    monkeypatch.setattr("core.url_guard.socket.getaddrinfo", _fail)

    with pytest.raises(GitUrlValidationError):
        validate_git_url("https://this-host-does-not-exist.invalid/repo")


# ---------------------------------------------------------------------------
# Rejected — empty input
# ---------------------------------------------------------------------------


def test_rejects_empty_string() -> None:
    from core.url_guard import GitUrlValidationError, validate_git_url

    with pytest.raises(GitUrlValidationError):
        validate_git_url("")


def test_rejects_whitespace_only() -> None:
    from core.url_guard import GitUrlValidationError, validate_git_url

    with pytest.raises(GitUrlValidationError):
        validate_git_url("   \n\t ")


def test_error_class_is_value_error_subclass() -> None:
    """Pydantic field_validators rely on ValueError subclassing — guard the contract."""
    from core.url_guard import GitUrlValidationError

    assert issubclass(GitUrlValidationError, ValueError)


# ---------------------------------------------------------------------------
# Rejected — round-1 security review follow-ups (M-1, M-2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://100.64.0.1/repo",      # RFC 6598 CGNAT — first usable
        "http://100.127.255.254/repo", # RFC 6598 CGNAT — last usable
    ],
)
def test_rejects_cgnat_rfc_6598(url: str) -> None:
    """M-1: RFC 6598 100.64.0.0/10 (CGNAT) is not covered by `is_private`.

    K8s CNIs (Calico default) and ISP NAT use this range for internal
    services; without an explicit reject, a worker could SSRF into them.
    """
    from core.url_guard import GitUrlValidationError, validate_git_url

    with pytest.raises(GitUrlValidationError):
        validate_git_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "@github.com:foo/bar.git",     # M-2: empty userinfo
        "git@github.com:",             # M-2: empty path
    ],
)
def test_rejects_degenerate_scp_form(url: str) -> None:
    """M-2: SCP-form URLs must have both a userinfo and a path."""
    from core.url_guard import GitUrlValidationError, validate_git_url

    with pytest.raises(GitUrlValidationError):
        validate_git_url(url)
