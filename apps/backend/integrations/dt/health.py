"""
Dependency-Track health monitor — CLAUDE.md core rule #4.

Strategy:

- A Celery Beat task fires every 60 seconds and calls
  :class:`DTClient.health` (``GET /api/version``).
- On success, we ``record_success()`` on the breaker; if the breaker was
  HALF_OPEN this drives it CLOSED.
- On failure, we ``record_failure()`` on the breaker; consecutive failures
  past the threshold flip CLOSED → OPEN.
- When the breaker has been OPEN for longer than the cooldown AND the
  ``DT_AUTO_RESTART`` flag is enabled, we attempt a documented recovery:
  ``docker restart dtrack-api`` (a subprocess invocation; we never call the
  Docker API directly from here).
- Auto-restart events are written to structlog WARNING level. Phase 6 will
  forward them to the notification queue (email / Slack / Teams) — for PR #8
  the log line is the only exit point.

The monitor itself never imports the FastAPI app. It runs in the Celery
worker context and uses the sync Redis client through the breaker module.
"""

from __future__ import annotations

import shutil
import subprocess  # noqa: S404 — running ``docker`` against a documented service
import time
from dataclasses import dataclass

import structlog

from core.config import dt_auto_restart_enabled, dt_breaker_cooldown_seconds

from . import DTUnavailable
from .breaker import STATE_OPEN, BreakerSnapshot, CircuitBreaker, get_breaker
from .client import DTClient, build_client

log = structlog.get_logger("integrations.dt.health")

# Heartbeat budget — the check itself should be fast; if DT does not respond
# within this window we treat it as a failure even though the underlying
# httpx timeout is larger. Consecutive heartbeat misses drive the breaker.
_HEARTBEAT_TIMEOUT_SECONDS = 5.0

# How long the breaker must stay OPEN before auto-restart is attempted.
# Default: 5 cooldown windows. We deliberately do not auto-restart on the
# first OPEN transition — the incident may resolve itself within the first
# cooldown.
_AUTO_RESTART_AFTER_COOLDOWNS = 5


@dataclass(frozen=True)
class HealthCheckOutcome:
    """Return value of :func:`run_health_check` — useful for tests."""

    healthy: bool
    snapshot_before: BreakerSnapshot
    snapshot_after: BreakerSnapshot
    auto_restart_attempted: bool
    error: str | None


def run_health_check(
    *,
    breaker: CircuitBreaker | None = None,
    client: DTClient | None = None,
) -> HealthCheckOutcome:
    """
    Perform one heartbeat against DT and update breaker state accordingly.

    The dependencies are injectable so the Celery task wrapper can be unit
    tested without a Redis or DT instance — pass a ``CircuitBreaker`` backed
    by ``fakeredis`` and a ``DTClient`` whose ``http`` is a ``MockTransport``.
    """
    breaker = breaker or get_breaker()
    snapshot_before = breaker.snapshot()

    owns_client = client is None
    client = client or build_client()
    healthy = False
    error_msg: str | None = None
    try:
        client.health()
        breaker.record_success()
        healthy = True
    except DTUnavailable as exc:
        breaker.record_failure()
        error_msg = str(exc)
        log.warning("dt_health_check_failed", error=error_msg)
    finally:
        if owns_client:
            client.close()

    snapshot_after = breaker.snapshot()
    auto_restart_attempted = False

    if (
        not healthy
        and snapshot_after.state == STATE_OPEN
        and dt_auto_restart_enabled()
        and _has_been_open_long_enough(snapshot_after)
    ):
        auto_restart_attempted = _attempt_auto_restart()

    log.info(
        "dt_health_check",
        healthy=healthy,
        state_before=snapshot_before.state,
        state_after=snapshot_after.state,
        fail_count=snapshot_after.fail_count,
        auto_restart_attempted=auto_restart_attempted,
    )

    return HealthCheckOutcome(
        healthy=healthy,
        snapshot_before=snapshot_before,
        snapshot_after=snapshot_after,
        auto_restart_attempted=auto_restart_attempted,
        error=error_msg,
    )


# ---------------------------------------------------------------------------
# Auto-restart helpers
# ---------------------------------------------------------------------------


def _has_been_open_long_enough(snapshot: BreakerSnapshot) -> bool:
    """True when the breaker has stayed OPEN past the auto-restart threshold."""
    if snapshot.opened_at is None:
        return False
    elapsed = time.time() - snapshot.opened_at
    threshold = dt_breaker_cooldown_seconds() * _AUTO_RESTART_AFTER_COOLDOWNS
    return elapsed >= threshold


def _attempt_auto_restart() -> bool:
    """
    Attempt ``docker restart dtrack-api``.

    Returns True on success, False otherwise. Failures are logged but do NOT
    raise — the monitor task should not crash a Celery worker just because
    Docker is unreachable. Phase 6 will surface failures via notifications.
    """
    if shutil.which("docker") is None:
        log.warning("dt_auto_restart_docker_missing")
        return False
    log.warning("dt_auto_restart_attempt", target="dtrack-api")
    # ``docker`` is resolved via $PATH inside the worker container
    # (Dockerfile.worker installs the docker CLI); we keep the bare command
    # name so an operator can override the binary location via the worker's
    # PATH without code changes. The S607 suppression below is the
    # corresponding ruff acknowledgement.
    try:
        result = subprocess.run(  # noqa: S603
            ["docker", "restart", "dtrack-api"],  # noqa: S607
            capture_output=True,
            check=False,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        log.error("dt_auto_restart_timeout")
        return False
    except OSError as exc:
        log.error("dt_auto_restart_os_error", error=str(exc))
        return False

    if result.returncode != 0:
        log.error(
            "dt_auto_restart_failed",
            returncode=result.returncode,
            stderr=result.stderr.decode("utf-8", errors="replace")[:500],
        )
        return False
    log.warning("dt_auto_restart_succeeded")
    return True


__all__ = [
    "HealthCheckOutcome",
    "run_health_check",
]
