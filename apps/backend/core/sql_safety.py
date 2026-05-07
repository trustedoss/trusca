"""
SQL safety helpers shared across services.

`escape_like` neutralises PostgreSQL ``LIKE`` wildcards (`%`, `_`, `\\`) in
user-supplied search strings. Companion to SQLAlchemy ``.ilike(pattern,
escape='\\\\')`` — call this on the user input *before* wrapping the pattern in
``%...%``, then pass ``escape='\\\\'`` so the literal backslash we emit acts as
the ESCAPE character.

The helper used to live as a module-level ``_escape_like`` in
``services.vulnerability_service`` and was cross-imported by
``services.license_service`` and ``services.obligation_service``. Promoting
it to ``core`` removes the cross-module leading-underscore import and keeps
the policy in one place.
"""

from __future__ import annotations

import re

_LIKE_ESCAPE_RE = re.compile(r"([\\%_])")


def escape_like(value: str) -> str:
    """Escape PostgreSQL LIKE wildcards.

    Returns *value* with each occurrence of ``\\``, ``%``, or ``_`` prefixed
    by a single backslash. The caller is expected to pass ``escape='\\\\'`` to
    SQLAlchemy's ``.ilike(...)`` so the emitted backslash is interpreted as
    the escape character (PostgreSQL's default for LIKE is none).
    """
    return _LIKE_ESCAPE_RE.sub(r"\\\1", value)


__all__ = ["escape_like"]
