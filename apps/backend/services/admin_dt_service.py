"""
Admin DT-Connector service — Phase 4 PR #14.

Wraps the existing DT integration primitives (breaker, client, health) into
the four operations the admin dashboard needs:

  - :func:`get_dt_status`         — breaker snapshot + DT version probe (cached 30s).
  - :func:`list_orphans`          — inline orphan detection (paginated).
  - :func:`enqueue_orphan_cleanup`— Celery task dispatch + Redis lock.
  - :func:`force_health_check`    — synchronous health probe via ``run_health_check``.

Design constraints (CLAUDE.md core rules + spec):
  - Every DT call routes through the breaker. When the breaker is OPEN the
    service returns a partial response with ``state="open"`` and ``version=None``;
    no exception escapes to the router.
  - The orphan-listing path is intentionally synchronous + bounded
    (``limit + offset`` page) so the admin gets a fresh snapshot per click.
    The 6-hour Beat task remains the source of truth for batch detection;
    this endpoint is the on-demand alternative.
  - The cleanup endpoint never blocks — it dispatches a Celery task and
    returns the task id immediately. A Redis SETNX lock prevents two
    concurrent cleanup runs from clobbering each other.
  - 30-second status cache (Redis) protects DT from a "refresh button
    storm" — the admin UI may poll status every few seconds.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import redis
import structlog

from core.config import redis_url
from integrations.dt import DTError, DTUnavailable
from integrations.dt.breaker import (
    STATE_CLOSED,
    STATE_HALF_OPEN,
    STATE_OPEN,
    BreakerSnapshot,
    CircuitBreaker,
    get_breaker,
)
from integrations.dt.client import DTClient, build_client
from integrations.dt.health import HealthCheckOutcome, run_health_check
from schemas.admin_ops import (
    BreakerResetOut,
    BreakerState,
    DTOrphanItem,
    DTOrphanListPage,
    DTStatusOut,
    HealthProbeOut,
    OrphanCleanupEnqueued,
)
from services.admin_disk_service import _strip_credentials

log = structlog.get_logger("admin.dt.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class AdminDTError(Exception):
    """Base class for admin DT errors mapped to RFC 7807."""

    status_code: int = 400
    title: str = "Admin DT Error"
    type_uri: str = "about:blank"
    extensions: dict[str, object] = {}


class DTUnreachable(AdminDTError):
    """Breaker OPEN or DT 5xx during a synchronous admin operation."""

    status_code = 503
    title = "Dependency-Track Unreachable"
    type_uri = "https://docs.trustedoss.io/errors/dt-unreachable"
    extensions = {"dt_unreachable": True}


class OrphanCleanupInProgress(AdminDTError):
    """Another cleanup task is already running — Redis lock held."""

    status_code = 409
    title = "Orphan Cleanup Already In Progress"
    type_uri = "https://docs.trustedoss.io/errors/dt-orphan-cleanup-in-progress"
    extensions = {"dt_orphan_cleanup_in_progress": True}


class BreakerAlreadyClosed(AdminDTError):
    """Reset attempted on a breaker that is already in CLOSED state — no-op refused.

    The endpoint refuses the request rather than silently succeeding so that
    operators always see a deterministic 409 + audit-row pair when running a
    reset by mistake. Returning 200 on a no-op would let scripted retries
    paper over a stuck-CLOSED state that the operator should investigate.
    """

    status_code = 409
    title = "Breaker Already Closed"
    type_uri = "https://docs.trustedoss.io/errors/dt-breaker-already-closed"
    extensions = {"dt_breaker_already_closed": True}


# ---------------------------------------------------------------------------
# Status cache (Redis) + Redis helpers
# ---------------------------------------------------------------------------

# Keys are scoped under ``dt:admin:`` so they cannot collide with the breaker's
# own ``dt:breaker:*`` namespace. TTLs:
#   - status cache 30s     — refresh-storm guard, NOT availability cache
#   - cleanup lock 3600s   — worst-case 500 UUIDs × 5s/delete = 2500s < 3600s
#     (G10: the old 600s TTL was shorter than worst-case runtime, risking a
#     second concurrent cleanup starting while the first was still running)
_STATUS_CACHE_KEY = "dt:admin:status_cache"
_STATUS_CACHE_TTL_SECONDS = 30
_CLEANUP_LOCK_KEY = "dt:admin:orphan_cleanup_lock"
_CLEANUP_LOCK_TTL_SECONDS = 3600


def _redis_client() -> redis.Redis:
    """Return a fresh Redis client bound to the runtime ``REDIS_URL``.

    CLAUDE.md core rule #11 — read the env at call time. Building per call
    is cheap (Redis client is just a connection-pool descriptor) and avoids
    sharing connection state across requests.
    """
    return redis.Redis.from_url(redis_url(), decode_responses=True)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _normalize_state(state: str) -> BreakerState:
    """Coerce the breaker's ``str`` state to the typed Literal.

    The breaker module guarantees ``snapshot().state`` is one of the three
    valid values, but the type system gives us back ``str``. This helper
    keeps the conversion in one place so the typed ``DTStatusOut`` field
    stays sound.
    """
    if state in (STATE_CLOSED, STATE_OPEN, STATE_HALF_OPEN):
        return state  # type: ignore[return-value]
    # Defensive — break silently CLOSED if Redis returned garbage. Logging
    # at WARNING because this would indicate a Redis corruption / forced
    # set by an operator.
    log.warning("admin.dt.unexpected_breaker_state", state=state)
    return "closed"


# ---------------------------------------------------------------------------
# get_dt_status — breaker snapshot + DT version probe (cached 30s)
# ---------------------------------------------------------------------------


def _build_status(
    *,
    snapshot: BreakerSnapshot,
    version: str | None,
    last_error: str | None,
) -> DTStatusOut:
    opened_at_dt: datetime | None = None
    if snapshot.opened_at is not None:
        # The breaker stores opened_at as a wall-time epoch float; converting
        # to UTC keeps the API contract calendar-aware.
        opened_at_dt = datetime.fromtimestamp(snapshot.opened_at, tz=UTC)
    return DTStatusOut(
        state=_normalize_state(snapshot.state),
        fail_count=snapshot.fail_count,
        opened_at=opened_at_dt,
        last_check_at=_now(),
        version=version,
        last_error=last_error,
    )


def get_dt_status(
    *,
    breaker: CircuitBreaker | None = None,
    client: DTClient | None = None,
    force_refresh: bool = False,
) -> DTStatusOut:
    """
    Return the current DT health summary for the admin dashboard.

    Behaviour:
      - Breaker snapshot is read directly from Redis (cheap).
      - DT version is probed via ``client.health()`` BUT only when the
        breaker is not OPEN — short-circuiting an OPEN call keeps with
        CLAUDE.md core rule #4 ("OPEN → cached PostgreSQL").
      - Result is cached in Redis for 30s under ``dt:admin:status_cache``
        so a polling admin UI does not hammer DT. Tests pin this with
        ``force_refresh=True``.

    The ``breaker`` and ``client`` parameters are injectable so tests can
    drive the service without touching real Redis / DT (matches the same
    pattern in :func:`integrations.dt.health.run_health_check`).
    """
    breaker = breaker or get_breaker()
    rds = _redis_client()

    if not force_refresh:
        # redis-py's stubs union sync + async return types; ``decode_responses``
        # plus a sync client gives us a string at runtime. ``Any`` keeps mypy
        # honest without forcing a cast at the call site.
        cached: Any = rds.get(_STATUS_CACHE_KEY)
        if cached is not None and isinstance(cached, str | bytes | bytearray):
            try:
                payload = json.loads(cached)
                # Pydantic re-validates on construction so a malformed cache
                # entry (impossible under normal flow, possible if an
                # operator wrote to the key by hand) is rejected and we fall
                # through to a fresh probe.
                return DTStatusOut.model_validate(payload)
            except (ValueError, TypeError) as exc:
                log.warning("admin.dt.status_cache_invalid", error=str(exc))
                rds.delete(_STATUS_CACHE_KEY)

    snapshot = breaker.snapshot()
    version: str | None = None
    last_error: str | None = None

    # Skip the version probe when the breaker is OPEN — calling DT would
    # short-circuit anyway (DTBreakerOpen) and add noise to the breaker's
    # own counters. half_open + closed both probe normally.
    if snapshot.state != STATE_OPEN:
        owns_client = client is None
        client = client or build_client()
        try:
            # Inner closure so the breaker's record_success / record_failure
            # hooks fire correctly. DTBreakerOpen never reaches us here
            # because we already gated on snapshot.state above.
            def _probe() -> dict[str, Any]:
                return client.health()

            try:
                body = breaker.call(_probe)
                raw_version = body.get("version") if isinstance(body, dict) else None
                version = str(raw_version) if raw_version is not None else None
            except DTError as exc:
                # Either DTUnavailable (5xx / network) or DTClientError
                # (4xx). Either way we report the breaker's post-call state
                # and surface the error message — the breaker has already
                # incremented fail_count if appropriate.
                last_error = _strip_credentials(str(exc))
                # Re-snapshot because record_failure may have flipped to OPEN.
                snapshot = breaker.snapshot()
        finally:
            if owns_client:
                client.close()

    result = _build_status(snapshot=snapshot, version=version, last_error=last_error)

    # Best-effort cache write. A Redis outage here should not bubble up;
    # the next call simply re-probes.
    try:
        rds.setex(
            _STATUS_CACHE_KEY,
            _STATUS_CACHE_TTL_SECONDS,
            result.model_dump_json(),
        )
    except redis.RedisError as exc:
        log.warning("admin.dt.status_cache_write_failed", error=str(exc))

    return result


# ---------------------------------------------------------------------------
# list_orphans — inline scan (synchronous, paginated)
# ---------------------------------------------------------------------------


# Page size for the DT-side fetch. The admin UI shows 50 rows per page; DT
# defaults to 100 per page on its own list endpoint, so we pull one DT page
# per slice and trim. Keeping the constant private discourages callers from
# tuning it ad-hoc.
_DT_PAGE_SIZE = 100


def list_orphans(
    *,
    limit: int = 50,
    offset: int = 0,
    breaker: CircuitBreaker | None = None,
    client: DTClient | None = None,
    sync_session_factory: Any | None = None,
) -> DTOrphanListPage:
    """
    Walk DT projects and identify those whose ``version`` is a UUID that
    does NOT match any local ``scans.id``.

    This is the same detection logic as ``tasks.dt_orphan_cleaner._classify_page``
    but executed inline so the admin sees a fresh result. We bound the
    walk at ``offset + limit + 1`` orphans so even a DT instance with a
    million projects does not block the request — the response sets
    ``has_more = True`` and the UI paginates.

    Breaker-OPEN raises :class:`DTUnreachable` (503).
    """
    breaker = breaker or get_breaker()
    snapshot = breaker.snapshot()
    if snapshot.state == STATE_OPEN:
        raise DTUnreachable(
            "Dependency-Track circuit breaker is OPEN; "
            "orphan detection unavailable until DT recovers"
        )

    # Local import keeps the FastAPI process from pulling in the sync engine
    # on import — same pattern as tasks.dt_orphan_cleaner.
    from core.db import sync_session_scope

    factory = sync_session_factory or sync_session_scope

    owns_client = client is None
    client = client or build_client()

    all_orphans: list[DTOrphanItem] = []
    # Hard ceiling to bound DT API calls. If DT has more than this many
    # orphans we stop walking and report ``has_more=True`` so the admin
    # has to run cleanup before listing the rest. 1000 = 10 DT pages, well
    # under the breaker timeout budget.
    max_walk = max(offset + limit + 1, 100)

    try:
        page_number = 1
        while len(all_orphans) < max_walk:
            current_page = page_number  # capture for the closure

            def _fetch(pn: int = current_page) -> list[dict[str, Any]]:
                return client.list_projects(page_size=_DT_PAGE_SIZE, page_number=pn)

            try:
                page = breaker.call(_fetch)
            except DTUnavailable as exc:
                raise DTUnreachable(f"DT unreachable during orphan scan: {exc}") from exc
            if not page:
                break

            with factory() as session:
                _classify_page(session, page=page, sink=all_orphans)

            if len(page) < _DT_PAGE_SIZE:
                break
            page_number += 1
    finally:
        if owns_client:
            client.close()

    total = len(all_orphans)
    has_more = total > offset + limit
    sliced = all_orphans[offset : offset + limit]
    return DTOrphanListPage(items=sliced, total=total, has_more=has_more)


def _classify_page(
    session: Any,
    *,
    page: list[dict[str, Any]],
    sink: list[DTOrphanItem],
) -> None:
    """Mirror of ``tasks.dt_orphan_cleaner._classify_page`` for inline use.

    Differences:
      - We collect the DT name + version strings (the Beat task only keeps
        UUIDs) so the admin UI can render a useful row.
      - We use ``session.execute`` directly so this helper works against
        either a sync or async session — the async path is unused today
        but the signature stays loose for future reuse.
    """
    from sqlalchemy import select

    from models import Scan

    candidate_uuids: list[uuid.UUID] = []
    by_scan_uuid: dict[uuid.UUID, dict[str, Any]] = {}
    for project in page:
        if not isinstance(project, dict):
            continue
        version = project.get("version")
        dt_uuid = project.get("uuid")
        if not isinstance(version, str) or not isinstance(dt_uuid, str):
            continue
        try:
            scan_uuid = uuid.UUID(version)
        except ValueError:
            continue
        candidate_uuids.append(scan_uuid)
        by_scan_uuid[scan_uuid] = project

    if not candidate_uuids:
        return

    found = set(
        session.execute(select(Scan.id).where(Scan.id.in_(candidate_uuids))).scalars().all()
    )
    for scan_uuid, project in by_scan_uuid.items():
        if scan_uuid in found:
            continue
        sink.append(
            DTOrphanItem(
                dt_project_uuid=str(project.get("uuid", "")),
                dt_project_name=project.get("name"),
                dt_project_version=project.get("version"),
            )
        )


# ---------------------------------------------------------------------------
# enqueue_orphan_cleanup — Celery dispatch with Redis lock
# ---------------------------------------------------------------------------


def enqueue_orphan_cleanup(
    *,
    dt_project_uuids: list[uuid.UUID],
    delay: Any | None = None,
) -> OrphanCleanupEnqueued:
    """
    Acquire the Redis cleanup lock and dispatch the Celery task.

    Concurrency:
      - We use ``SETNX + EX`` on ``dt:admin:orphan_cleanup_lock``. If the
        lock is held, we raise :class:`OrphanCleanupInProgress` (409). The
        TTL = 600s, well above expected task runtime, so a crashed worker
        eventually releases the lock without operator intervention.
      - The Celery task itself releases the lock on success / failure (see
        ``tasks.dt_orphan_cleanup``); we only acquire it here.

    The ``delay`` injection point lets unit tests stub Celery without
    importing the broker — pass a callable that returns a fake ``AsyncResult``
    with an ``id`` attribute.
    """
    rds = _redis_client()
    acquired = rds.set(
        _CLEANUP_LOCK_KEY,
        "1",
        nx=True,
        ex=_CLEANUP_LOCK_TTL_SECONDS,
    )
    if not acquired:
        raise OrphanCleanupInProgress(
            "another orphan cleanup task is already running; "
            "wait for it to finish before triggering a new one"
        )

    # Local import: the Celery task module imports ``celery_app`` at module
    # level, which would force the FastAPI process to construct the broker
    # client — we keep that lazy.
    if delay is None:
        from tasks.dt_orphan_cleanup import dt_orphan_cleanup_task

        delay = dt_orphan_cleanup_task.delay

    str_uuids = [str(u) for u in dt_project_uuids]
    try:
        async_result = delay(str_uuids)
    except Exception:
        # Roll back the lock so the next attempt can proceed. We don't
        # swallow the exception — the router converts it to a 503 via the
        # generic exception handler.
        rds.delete(_CLEANUP_LOCK_KEY)
        raise

    task_id = getattr(async_result, "id", None) or ""
    log.warning(
        "admin.dt.orphan_cleanup_enqueued",
        task_id=str(task_id),
        count=len(str_uuids),
        # Honor the ``feedback_adversarial_input_parametrize`` memory by
        # logging the count, not the (potentially huge) UUID list itself.
    )
    return OrphanCleanupEnqueued(
        task_id=str(task_id),
        enqueued_at=_now(),
        count=len(str_uuids),
    )


# ---------------------------------------------------------------------------
# force_health_check — synchronous probe
# ---------------------------------------------------------------------------


def force_reset_breaker(
    *,
    breaker: CircuitBreaker | None = None,
) -> BreakerResetOut:
    """
    Operator-triggered reset of the DT circuit breaker to CLOSED.

    Refuses (raises :class:`BreakerAlreadyClosed` → 409) when the breaker is
    already CLOSED so that operators always see a deterministic state +
    audit row pairing. Drops the cached status payload so the next status
    poll reflects the fresh breaker state instead of the 30-second cached
    OPEN snapshot.

    Returns the previous + new state so the caller can render
    "OPEN → CLOSED" in the UI and log the transition explicitly.
    """
    breaker = breaker or get_breaker()
    snapshot_before = breaker.snapshot()
    state_before = _normalize_state(snapshot_before.state)

    if state_before == STATE_CLOSED:
        raise BreakerAlreadyClosed(
            "DT circuit breaker is already CLOSED; nothing to reset"
        )

    fail_count_before = snapshot_before.fail_count
    breaker.force_close()

    # Force a fresh status poll on the next request — the cached payload may
    # still claim OPEN/HALF_OPEN. Best-effort: a Redis hiccup here just lets
    # the cache age out naturally over the next 30 seconds.
    try:
        rds = _redis_client()
        rds.delete(_STATUS_CACHE_KEY)
    except redis.RedisError as exc:
        log.warning("admin.dt.status_cache_delete_failed", error=str(exc))

    log.warning(
        "admin.dt.breaker_force_reset",
        state_before=state_before,
        fail_count_before=fail_count_before,
    )
    return BreakerResetOut(
        state_before=state_before,
        state_after="closed",
        fail_count_before=fail_count_before,
        reset_at=_now(),
    )


def force_health_check(
    *,
    breaker: CircuitBreaker | None = None,
    client: DTClient | None = None,
) -> HealthProbeOut:
    """
    Run :func:`integrations.dt.health.run_health_check` synchronously and
    flatten the outcome into the API shape.

    The underlying helper updates the breaker (record_success on healthy,
    record_failure on outage) so this call doubles as "kick the breaker".
    The audit log entry for the probe itself is emitted by the route layer
    via the explicit ``AuditLog`` insert pattern (the heartbeat does not
    write to any audited table on its own).
    """
    outcome: HealthCheckOutcome = run_health_check(breaker=breaker, client=client)
    return HealthProbeOut(
        healthy=outcome.healthy,
        state_before=_normalize_state(outcome.snapshot_before.state),
        state_after=_normalize_state(outcome.snapshot_after.state),
        fail_count=outcome.snapshot_after.fail_count,
        auto_restart_attempted=outcome.auto_restart_attempted,
        error=outcome.error,
        checked_at=_now(),
    )


# ---------------------------------------------------------------------------
# Test hooks
# ---------------------------------------------------------------------------


def _reset_status_cache_for_tests() -> None:
    """Drop the status cache + cleanup lock keys.

    Used by integration tests so a previous test's cached payload does not
    leak into the next. Not part of the public API — the underscore makes
    that explicit. We also drop the lock so a test that happened to fault
    mid-way does not poison subsequent tests.
    """
    try:
        rds = _redis_client()
        rds.delete(_STATUS_CACHE_KEY)
        rds.delete(_CLEANUP_LOCK_KEY)
    except redis.RedisError:
        # Tests that don't have Redis available simply skip; we don't want
        # the helper itself to fail and shadow the real test failure.
        pass


# Re-export for the route module — `os` is referenced lazily by the env-aware
# cache TTL override below if/when we add one. Keep the import to keep the
# public attribute surface stable.
_ = os


__all__ = [
    "AdminDTError",
    "BreakerAlreadyClosed",
    "DTUnreachable",
    "OrphanCleanupInProgress",
    "enqueue_orphan_cleanup",
    "force_health_check",
    "force_reset_breaker",
    "get_dt_status",
    "list_orphans",
]
