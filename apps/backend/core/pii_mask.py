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


__all__ = ["mask_pii"]
