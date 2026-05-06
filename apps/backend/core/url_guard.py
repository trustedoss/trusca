"""
SSRF guard for user-supplied Git URLs — Phase 2 PR #8.

A scan worker fetches the project's `git_url` to feed cdxgen / ORT. That URL
arrives from user input (`POST /v1/projects` body, later GitHub webhook
metadata) and therefore needs to be filtered before the worker performs any
network I/O against it. This module is the single entry point for that check.

The guard runs at TWO layers:

1. **Schema boundary** — `schemas.scan.ProjectCreate.git_url` invokes
   :func:`validate_git_url` so a malformed or dangerous URL never reaches
   the database. Failures surface as 422 problem+json via the Pydantic
   validation handler.
2. **Worker boundary** — the source-scan task re-validates immediately
   before invoking `git clone` (defence in depth: the row could have been
   updated since creation, or a future ingest path may bypass the schema).

Reject categories (security-reviewer reviewed):

- Schemes other than http/https/ssh/git/git+ssh — blocks ``file://``,
  ``data:``, ``javascript:``, ``gopher://``, etc.
- Hostnames resolving to RFC 1918 / loopback / link-local / multicast
  ranges — blocks lateral movement to internal services.
- Cloud instance-metadata endpoints (AWS / GCP / Azure / Alibaba / OCI) by
  literal IP and by canonical hostname — blocks IMDSv1 credential theft.
- URLs longer than 2048 chars — matches the schema column cap and avoids
  pathological inputs.

DNS rebinding is mitigated by re-validation at fetch time but is not
fully closed in this layer; the worker invokes ``git clone`` over a
single transport so a TOCTOU window remains. A follow-up PR may pin the
resolved IP into the clone command (``git -c http.proxy=...`` or
``--config http.curloptResolve=...``); flagged in MEMORY.
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
_ALLOWED_SCHEMES = frozenset(
    {
        "http",
        "https",
        "git",
        "ssh",
        "git+ssh",
    }
)

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


class GitUrlValidationError(ValueError):
    """Raised when a git URL fails the SSRF guard.

    Inherits from ``ValueError`` so Pydantic field_validators can re-raise
    without wrapping; FastAPI's RequestValidationError handler (core/errors.py)
    turns the resulting 422 into RFC 7807 problem+json.
    """


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
    # M-2 (security-reviewer round 1): SCP form requires *both* a userinfo
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


def _resolve_host_addresses(host: str) -> list[ipaddress._BaseAddress]:
    """Resolve `host` to all of its IP addresses.

    A literal IP is returned directly (no DNS hit). On resolution failure we
    raise GitUrlValidationError — letting the worker attempt a clone against
    an unresolvable hostname gives the attacker a free DNS oracle.
    """
    # If `host` is a literal IP, ip_address parses it directly.
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:  # gaierror, herror
        raise GitUrlValidationError(
            f"git_url host {host!r} could not be resolved: {exc}"
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
        raise GitUrlValidationError(
            f"git_url host {host!r} resolved to no usable IP addresses"
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
    # M-1 (security-reviewer round 1): RFC 6598 CGNAT range. Some K8s CNIs
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


def validate_git_url(url: str) -> str:
    """Validate a Git URL is safe to fetch from a worker.

    Returns the normalized URL on success, or raises
    :class:`GitUrlValidationError` with a human-readable reason.

    The check is intentionally synchronous and blocking on DNS — a few
    milliseconds at project-creation time is acceptable, and avoiding async
    here keeps the helper usable from sync Celery code as well.
    """
    if not isinstance(url, str) or not url.strip():
        raise GitUrlValidationError("git_url must be a non-empty string")

    candidate = url.strip()
    if len(candidate) > _MAX_URL_LENGTH:
        raise GitUrlValidationError(
            f"git_url exceeds {_MAX_URL_LENGTH} characters"
        )

    # Translate the SCP-style form (git@host:path) into ssh:// before parsing.
    # urlsplit treats scp form as path-only, which would let internal hosts
    # slip past the scheme/host checks otherwise.
    parsed_input = _strip_scp_form(candidate) or candidate

    parts = urlsplit(parsed_input)
    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise GitUrlValidationError(
            f"git_url scheme {scheme!r} is not allowed; "
            f"use one of {sorted(_ALLOWED_SCHEMES)}"
        )

    host = (parts.hostname or "").lower()
    if not host:
        raise GitUrlValidationError("git_url is missing a host component")

    # Hostname-level metadata block (covers DNS records that point at metadata
    # routers without ever resolving to a public IP).
    if host in _METADATA_HOSTNAMES:
        raise GitUrlValidationError(
            f"git_url host {host!r} targets a cloud metadata endpoint"
        )

    # Resolve and screen every IP the host maps to. We screen ALL addresses
    # so a multi-record DNS response with one bad entry is rejected.
    for ip in _resolve_host_addresses(host):
        if _is_dangerous_address(ip):
            raise GitUrlValidationError(
                f"git_url host {host!r} resolves to a non-routable or metadata"
                f" address ({ip})"
            )

    return candidate


__all__ = ["GitUrlValidationError", "validate_git_url"]
