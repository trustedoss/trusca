"""
Dependency-Track integration package.

CLAUDE.md core rule #4: every outbound DT call passes through the circuit
breaker in :mod:`integrations.dt.breaker`. The breaker's state is held in
Redis so multiple Celery worker processes share a consistent view; the
:mod:`integrations.dt.health` module is the authoritative heartbeat.

Submodules:

- ``client``  — synchronous httpx client with ``X-API-Key`` auth.
- ``breaker`` — Redis-backed CLOSED / OPEN / HALF_OPEN state machine.
- ``health``  — 60-second heartbeat task, optional auto-restart.

Errors raised from any of the above are subclasses of :class:`DTError`,
allowing tasks to ``except DTError`` for a coarse "DT is broken" handler.
"""

from __future__ import annotations


class DTError(RuntimeError):
    """Base class for any Dependency-Track integration error."""


class DTClientError(DTError):
    """A 4xx response from DT (auth, validation, not found). Not retryable."""


class DTUnavailable(DTError):
    """A 5xx response, network failure, or breaker-OPEN short-circuit."""


class DTBreakerOpen(DTUnavailable):
    """The breaker is OPEN and the call was not attempted."""


__all__ = [
    "DTBreakerOpen",
    "DTClientError",
    "DTError",
    "DTUnavailable",
]
