"""
Recursive PII / secret masking — Phase 2 PR #8.

This is a complement to ``core.audit.mask_sensitive_columns`` (which only
walks the top-level column dict). The audit listener serializes JSONB
columns like ``scans.metadata`` and ``scan_components.raw_data`` whole, and
those payloads can contain user-supplied keys that look like credentials.

We must therefore walk the value tree and redact any nested dict / list that
matches a sensitive-key pattern. The output is a deep-copied structure
containing ``"***"`` in place of every redacted value, so callers can write
the result straight into the audit ``diff`` JSONB without further processing.

The helper is intentionally NOT installed globally on the audit listener in
this PR — that would expand the blast radius of a refactor across all the
existing tests. Callers (currently the scan service) opt in by sanitizing
inbound payloads BEFORE they hit the ORM. Phase 3 will fold this helper into
``core/audit.py`` once its existing fixtures get an update.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

# Sensitive key tokens. Compared as case-insensitive substrings — a key like
# "GITHUB_API_KEY", "user.password", or "X-Auth-Token" must all redact.
#
# Order matters only for documentation: longer / more specific tokens first.
_SENSITIVE_TOKENS = (
    "password",
    "passwd",
    "secret",
    "private_key",
    "privatekey",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "id_token",
    "bearer",
    "authorization",
    "auth_token",
    "session_id",
    "session_token",
    "session",
    "cookie",
    "jwt",
    "token",
    "client_secret",
    # Email is PII even though it's not a credential. Mask the value but
    # leave the schema structure intact so audit trails still see "an email
    # was set" without storing the address itself.
    "email",
)

# Maximum recursion depth. Keeps the helper safe against pathological
# nesting; real scan metadata is bounded by ScanCreate's depth=4 guard, and
# DT/cdxgen JSONB rows are size-guarded upstream.
_MAX_DEPTH = 32

_REDACTED = "***"

# Userinfo username emitted by :func:`redact_url_userinfo` in place of a real
# ``user:token`` segment. Centralised so the round-trip guard
# (:func:`url_userinfo_is_redacted`) and the redactor never drift.
REDACTED_USERINFO_USERNAME = "***"


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in _SENSITIVE_TOKENS)


def mask_pii(value: Any, *, _depth: int = 0) -> Any:
    """Return a deep copy of `value` with sensitive fields replaced by ``"***"``.

    - dict: every key is inspected; if it matches a sensitive token the
      whole subtree is replaced with ``"***"``. Otherwise we recurse.
    - list/tuple: we recurse into each element. Tuples become lists in the
      output because the audit JSONB column has no notion of tuples and we
      want a consistent shape.
    - str/int/float/bool/None: returned unchanged.
    - any other type: stringified (defensive — we never want a raw
      datetime/UUID/SQLAlchemy object to crash JSON serialization later).

    Beyond ``_MAX_DEPTH`` levels we collapse to ``"***"`` to short-circuit
    pathological inputs (cycles, deeply nested attacker-controlled JSON).
    """
    if _depth > _MAX_DEPTH:
        return _REDACTED

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key_str = str(k)
            if _is_sensitive_key(key_str):
                out[key_str] = _REDACTED
            else:
                out[key_str] = mask_pii(v, _depth=_depth + 1)
        return out

    if isinstance(value, list | tuple):
        return [mask_pii(item, _depth=_depth + 1) for item in value]

    if isinstance(value, str | int | float | bool) or value is None:
        return value

    # Fallback: stringify (e.g. datetime, UUID). The audit listener already
    # has its own _serialize_value path; this is for callers that bypass it.
    return str(value)


def redact_url_userinfo(url: str) -> str:
    """Return ``url`` with any RFC 3986 userinfo segment replaced by ``***@``.

    Private-repo clones legitimately carry credentials in the userinfo segment
    (e.g. ``https://oauth2:GH_PAT_xxx@github.com/org/repo.git``). These must
    NEVER reach the structlog JSON output (CLAUDE.md §5 PII masking). The
    helper preserves scheme/host/port/path/query so log readers can still see
    where the URL pointed.

    Defensive on parse failure: returns ``"***invalid_url***"`` rather than
    propagating an exception, because callers are typically structlog kwargs
    where raising would tear down the surrounding error handler.
    """
    if not isinstance(url, str) or not url:
        return url if isinstance(url, str) else _REDACTED
    try:
        parts = urlsplit(url)
    except ValueError:
        return "***invalid_url***"
    if not parts.username and not parts.password:
        return url
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    netloc = f"***@{netloc}" if netloc else "***@"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


# Schemes where a credential legitimately lives in the URL userinfo
# (``https://<token>@github.com/...``). SSH/git URLs carry a conventional,
# non-secret ``git@host`` user and authenticate via keys, so masking their
# userinfo would corrupt the displayed URL without any security benefit.
_CREDENTIAL_URL_SCHEMES = frozenset({"http", "https"})


def mask_git_url(url: str | None) -> str | None:
    """Redact an embedded credential from a git URL, scheme-aware (C-2).

    Only ``http`` / ``https`` userinfo is redacted — that is where embedded
    PATs live (``https://<token>@github.com/org/repo``). ``ssh://git@host`` and
    scp-style ``git@host:org/repo`` keep their conventional ``git`` user (not a
    secret), so the URL the operator sees stays accurate.

    None / empty pass through unchanged. On a parse failure we fall back to the
    unconditional :func:`redact_url_userinfo` (which masks) rather than risk
    leaking a malformed-but-credentialed value.
    """
    if not url:
        return url
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return redact_url_userinfo(url)
    if scheme in _CREDENTIAL_URL_SCHEMES:
        return redact_url_userinfo(url)
    return url


def url_userinfo_is_redacted(url: str) -> bool:
    """Return True iff ``url`` carries the redaction marker as its userinfo.

    A masked URL produced by :func:`redact_url_userinfo` has username
    ``***`` and no password. The project-update path uses this to detect a
    client re-submitting a masked git_url (the settings form prefills git_url
    from the masked read response) so it can preserve the real stored
    credential URL instead of overwriting it with the mask.

    Defensive on parse failure: returns False (treat as a normal value) so a
    malformed URL still flows through the usual validation, never silently
    dropped.
    """
    if not isinstance(url, str) or not url:
        return False
    try:
        return urlsplit(url).username == REDACTED_USERINFO_USERNAME
    except ValueError:
        return False


__all__ = [
    "REDACTED_USERINFO_USERNAME",
    "mask_git_url",
    "mask_pii",
    "redact_url_userinfo",
    "url_userinfo_is_redacted",
]
