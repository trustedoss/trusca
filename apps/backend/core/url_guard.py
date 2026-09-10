# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
SSRF guard for user-supplied URLs. Phase 2 PR #8, generalized for #385.

A scan worker fetches the project's `git_url` to feed cdxgen / ORT. That URL
arrives from user input (`POST /v1/projects` body, later GitHub webhook
metadata) and therefore needs to be filtered before the worker performs any
network I/O against it. #385 added a second caller with the same shape: a
finding's `ticket_url`, fetched on demand to read the ticket's state back.
Both go through the SAME resolve-and-screen core
(:func:`_validate_and_resolve`); the scheme allow-list differs (git needs
`ssh`/`git`/`git+ssh` on top of `http(s)`, a ticket URL never does), but the
IP-safety check, which is the part a mistake would actually be dangerous in,
is written once.

The git-URL guard runs at TWO layers:

1. **Schema boundary** — `schemas.scan.ProjectCreate.git_url` invokes
   :func:`validate_git_url` so a malformed or dangerous URL never reaches
   the database. Failures surface as 422 problem+json via the Pydantic
   validation handler.
2. **Worker boundary** — the source-scan task re-validates immediately
   before invoking `git clone` (defence in depth: the row could have been
   updated since creation, or a future ingest path may bypass the schema).

Reject categories (security review reviewed):

- Schemes other than http/https/ssh/git/git+ssh — blocks ``file://``,
  ``data:``, ``javascript:``, ``gopher://``, etc.
- Hostnames resolving to RFC 1918 / loopback / link-local / multicast
  ranges — blocks lateral movement to internal services.
- Cloud instance-metadata endpoints (AWS / GCP / Azure / Alibaba / OCI) by
  literal IP and by canonical hostname — blocks IMDSv1 credential theft.
- URLs longer than 2048 chars — matches the schema column cap and avoids
  pathological inputs.

DNS rebinding (PR #9 closure of I-1):

  :func:`validate_git_url` continues to behave exactly as it did in PR #8 —
  it returns the normalized URL string. For the worker fetch path we now
  expose :func:`validate_git_url_with_ip`, which returns
  ``(normalized_url, resolved_ip)`` so the caller can pin the IP into the
  ``git`` invocation (``git -c http.curloptResolve=host:port:ip clone ...``)
  and close the TOCTOU window between DNS check and connect. The DNS
  resolution happens *once*, inside the validator, and the same address is
  the one the worker forces ``git`` to dial — there is no second resolve.

  Trade-off: when DNS returns multiple A records (round-robin, anycast),
  we pin the *first* address only. Operators who need the failover
  behaviour can opt out of pinning at the call site, but the safe default
  is "one DNS answer, one connection" because anything else re-opens the
  rebinding window.

The ticket-URL guard (#385), :func:`validate_http_url`, reuses the same
resolve-and-screen core with an ``http``/``https``-only scheme allow-list
and no SCP-form parsing (a ticket URL is never written in SCP shorthand).
It does NOT yet have an IP-pinned variant: the caller (a user-triggered
"refresh ticket status" button, not an unattended poller) re-resolves and
re-screens at connect time via the same validator, which is the level of
protection :func:`validate_git_url` shipped with in PR #8, before PR #9
closed the DNS-rebinding TOCTOU window for the (higher-volume, unattended)
git-fetch path. Whether the ticket path needs the same IP-pinning treatment
before it ships is exactly the security review #385 requires before merge.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Match the column cap on `projects.git_url` (schemas.scan.ProjectCreate).
_MAX_URL_LENGTH = 2048

# We accept the same Git transports the schema layer permits. ``git+ssh``
# variants from Python package metadata are folded into ``ssh``.
_GIT_ALLOWED_SCHEMES = frozenset(
    {
        "http",
        "https",
        "git",
        "ssh",
        "git+ssh",
    }
)

# A ticket URL is a browser link (Jira / GitHub / GitLab issue page) or an
# API endpoint derived from one, never an SSH transport, so the allow-list
# is narrower than git's.
_HTTP_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Cloud / on-prem instance-metadata endpoints that must never be reached
# from a worker. We list both the IP form (for direct IP URLs) and the
# canonical hostname (for DNS-based URLs that the resolver might miss when
# the host has a public-IP A record but resolves to a metadata route).
_METADATA_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata",
        "metadata.aws.amazon.com",
        "metadata.azure.com",
        "metadata.oraclecloud.com",
    }
)

_METADATA_IPS = frozenset(
    {
        "169.254.169.254",  # AWS, GCP, Azure, OCI canonical
        "100.100.100.200",  # Alibaba Cloud
        "192.0.0.192",      # Oracle Cloud (legacy)
        "fd00:ec2::254",    # AWS IPv6 link-local-ish
    }
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class UrlValidationError(ValueError):
    """Raised when a URL fails the shared SSRF guard core.

    Inherits from ``ValueError`` so Pydantic field_validators can re-raise
    without wrapping; FastAPI's RequestValidationError handler (core/errors.py)
    turns the resulting 422 into RFC 7807 problem+json. Callers catch their
    OWN subclass below rather than this one, so a git-URL catch site can
    never accidentally swallow a ticket-URL failure or vice versa.
    """


class GitUrlValidationError(UrlValidationError):
    """Raised when a git URL fails the SSRF guard."""


class TicketUrlValidationError(UrlValidationError):
    """Raised when a finding's ticket URL fails the SSRF guard (#385)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_scp_form(url: str) -> str | None:
    """Translate ``git@host:path`` (SCP form) to a urlsplit-friendly URL.

    Returns ``None`` if the input is not in SCP form.
    """
    if "://" in url:
        return None
    # SCP form requires `<userinfo>@<host>:<path>` with no scheme.
    if "@" not in url:
        return None
    userinfo, _, rest = url.partition("@")
    host, sep, path = rest.partition(":")
    if not sep or not host:
        return None
    # M-2 (security review round 1): SCP form requires *both* a userinfo
    # segment AND a non-empty path. `@host:foo` and `git@host:` look syntactically
    # similar to SCP but are degenerate — git would error out on the worker.
    # Reject early so the schema layer surfaces a 422 instead of letting the
    # worker burn time on a doomed clone.
    if not userinfo or not path:
        return None
    # Reject host segments that look like IPv6 (which the SCP form does not
    # support) — those should always use an explicit ssh:// scheme.
    if ":" in host:
        return None
    return f"ssh://{userinfo}@{host}/{path}"


def _resolve_host_addresses(
    host: str, *, field_name: str, error_cls: type[UrlValidationError]
) -> list[ipaddress._BaseAddress]:
    """Resolve `host` to all of its IP addresses.

    A literal IP is returned directly (no DNS hit). On resolution failure we
    raise ``error_cls``; letting the caller act on an unresolvable hostname
    gives the attacker a free DNS oracle.
    """
    # If `host` is a literal IP, ip_address parses it directly.
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:  # gaierror, herror
        raise error_cls(
            f"{field_name} host {host!r} could not be resolved: {exc}"
        ) from exc

    out: list[ipaddress._BaseAddress] = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_str = sockaddr[0]
        try:
            out.append(ipaddress.ip_address(ip_str))
        except ValueError:
            continue
    if not out:
        raise error_cls(
            f"{field_name} host {host!r} resolved to no usable IP addresses"
        )
    return out


_CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")  # RFC 6598


def _is_dangerous_address(ip: ipaddress._BaseAddress) -> bool:
    """True if the IP is in any non-routable / metadata range we reject."""
    if ip.is_loopback:
        return True
    if ip.is_private:
        return True
    if ip.is_link_local:
        return True
    if ip.is_multicast:
        return True
    if ip.is_reserved:
        return True
    if ip.is_unspecified:
        return True
    # M-1 (security review round 1): RFC 6598 CGNAT range. Some K8s CNIs
    # (Calico default) and ISP NAT use 100.64.0.0/10 for internal services;
    # `is_private` does not cover it.
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_V4:
        return True
    if str(ip) in _METADATA_IPS:
        return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _validate_and_resolve(
    url: str,
    *,
    field_name: str,
    error_cls: type[UrlValidationError],
    allowed_schemes: frozenset[str],
    strip_scp_form: bool,
) -> tuple[str, ipaddress._BaseAddress]:
    """Shared core of every ``validate_*`` function in this module.

    Returns ``(normalized_url, first_safe_ip)``. Raises ``error_cls`` on any
    rejection, with messages naming ``field_name`` so a git-URL failure and a
    ticket-URL failure read distinctly even though they share this body.

    Splitting the implementation out lets an IP-pin variant return the
    address without re-doing DNS, which is the whole point of that layer
    (PR #9 / I-1, git-only today). A second resolve here would re-open the
    TOCTOU window that closes.
    """
    if not isinstance(url, str) or not url.strip():
        raise error_cls(f"{field_name} must be a non-empty string")

    candidate = url.strip()
    if len(candidate) > _MAX_URL_LENGTH:
        raise error_cls(f"{field_name} exceeds {_MAX_URL_LENGTH} characters")

    # Translate the SCP-style form (git@host:path) into ssh:// before parsing.
    # urlsplit treats scp form as path-only, which would let internal hosts
    # slip past the scheme/host checks otherwise. Only git URLs are ever
    # written in this shorthand.
    parsed_input = (_strip_scp_form(candidate) if strip_scp_form else None) or candidate

    parts = urlsplit(parsed_input)
    scheme = (parts.scheme or "").lower()
    if scheme not in allowed_schemes:
        raise error_cls(
            f"{field_name} scheme {scheme!r} is not allowed; "
            f"use one of {sorted(allowed_schemes)}"
        )

    host = (parts.hostname or "").lower()
    if not host:
        raise error_cls(f"{field_name} is missing a host component")

    # Hostname-level metadata block (covers DNS records that point at metadata
    # routers without ever resolving to a public IP).
    if host in _METADATA_HOSTNAMES:
        raise error_cls(f"{field_name} host {host!r} targets a cloud metadata endpoint")

    # Resolve and screen every IP the host maps to. We screen ALL addresses
    # so a multi-record DNS response with one bad entry is rejected.
    addresses = _resolve_host_addresses(host, field_name=field_name, error_cls=error_cls)
    for ip in addresses:
        if _is_dangerous_address(ip):
            raise error_cls(
                f"{field_name} host {host!r} resolves to a non-routable or metadata"
                f" address ({ip})"
            )

    # Pin the FIRST returned address. _resolve_host_addresses iterates
    # getaddrinfo in OS order, which already prefers IPv6 if the platform
    # is dual-stacked; we honour that. Round-robin DNS is intentionally
    # collapsed to a single answer — see module docstring trade-off note.
    return candidate, addresses[0]


def validate_git_url(url: str) -> str:
    """Validate a Git URL is safe to fetch from a worker.

    Returns the normalized URL on success, or raises
    :class:`GitUrlValidationError` with a human-readable reason.

    The check is intentionally synchronous and blocking on DNS — a few
    milliseconds at project-creation time is acceptable, and avoiding async
    here keeps the helper usable from sync Celery code as well.

    This is the schema-layer entry point. The worker-side fetch path uses
    :func:`validate_git_url_with_ip` so it can pin the resolved address
    into the ``git`` invocation and close the DNS-rebinding TOCTOU window.
    """
    normalized, _ip = _validate_and_resolve(
        url,
        field_name="git_url",
        error_cls=GitUrlValidationError,
        allowed_schemes=_GIT_ALLOWED_SCHEMES,
        strip_scp_form=True,
    )
    return normalized


def validate_http_url(url: str) -> str:
    """Validate an http(s) URL is safe to fetch on demand (#385).

    Returns the normalized URL on success, or raises
    :class:`TicketUrlValidationError` with a human-readable reason. Shares
    every IP-safety check :func:`validate_git_url` uses; the only
    difference is the narrower ``http``/``https``-only scheme allow-list
    and no SCP-form parsing.

    Not IP-pinned (see the module docstring's #385 note); the caller
    re-validates at connect time via this same function rather than reusing
    a resolved address from an earlier call.
    """
    normalized, _ip = _validate_and_resolve(
        url,
        field_name="ticket_url",
        error_cls=TicketUrlValidationError,
        allowed_schemes=_HTTP_ALLOWED_SCHEMES,
        strip_scp_form=False,
    )
    return normalized


def validate_git_url_with_ip(url: str) -> tuple[str, str]:
    """Validate a Git URL and return ``(normalized_url, resolved_ip_string)``.

    Worker-side defence-in-depth (PR #9 / I-1 closure):

    The schema layer already ran :func:`validate_git_url` before the row
    was persisted, but the worker may run minutes (or, on retry, hours)
    after that — a hostname could have rotated to a private address in
    that window. We re-validate here AND surface the resolved IP so the
    fetch step can pass ``git -c http.curloptResolve=host:port:ip`` and
    guarantee the connection lands on the address we screened.

    The ``resolved_ip`` is a string suitable for direct use in
    ``http.curloptResolve`` (IPv4 dotted form or IPv6 bracketed-form
    consumers handle separately). Callers that want to skip IP-pinning
    can ignore the second element.
    """
    normalized, ip = _validate_and_resolve(
        url,
        field_name="git_url",
        error_cls=GitUrlValidationError,
        allowed_schemes=_GIT_ALLOWED_SCHEMES,
        strip_scp_form=True,
    )
    return normalized, str(ip)


__all__ = [
    "GitUrlValidationError",
    "TicketUrlValidationError",
    "UrlValidationError",
    "validate_git_url",
    "validate_git_url_with_ip",
    "validate_http_url",
]
