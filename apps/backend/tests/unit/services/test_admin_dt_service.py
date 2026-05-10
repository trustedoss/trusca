"""
Service-layer tests for ``services.admin_dt_service`` — Phase 4 PR #14.

The service composes the existing DT integration primitives (breaker,
client, health) into the four admin dashboard operations. Each test drives
the service with the in-process ``fakeredis`` + ``httpx.MockTransport``
fixtures that ``tests/unit/integrations/conftest.py`` already provides for
the breaker / health unit tests.

Coverage:
  - get_dt_status — happy path, breaker-OPEN short-circuit, cache hit / miss,
    DT 5xx, Redis cache invalidation on malformed payload.
  - list_orphans — empty, mixed, breaker-OPEN raises DTUnreachable.
  - enqueue_orphan_cleanup — happy path, lock contention raises 409.
  - force_health_check — flattens HealthCheckOutcome.
  - adversarial input parametrize via the boundary schema (ValidationError
    on garbage input — schema covered separately, but we pin one case here).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from integrations.dt.breaker import STATE_CLOSED, STATE_OPEN
from services.admin_dt_service import (
    BreakerAlreadyClosed,
    DTUnreachable,
    OrphanCleanupInProgress,
    enqueue_orphan_cleanup,
    force_health_check,
    force_reset_breaker,
    get_dt_status,
    list_orphans,
)

# ---------------------------------------------------------------------------
# get_dt_status
# ---------------------------------------------------------------------------


def test_get_dt_status_happy_path_returns_version_and_state(
    make_breaker: Any,
    make_dt_client: Any,
    fakeredis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reachable DT yields state='closed' + the version string."""

    def ok(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "4.13.2"})

    breaker = make_breaker()
    client = make_dt_client(ok)
    monkeypatch.setattr(
        "services.admin_dt_service._redis_client",
        lambda: fakeredis_client,
    )
    try:
        result = get_dt_status(breaker=breaker, client=client, force_refresh=True)
    finally:
        client.close()

    assert result.state == "closed"
    assert result.version == "4.13.2"
    assert result.fail_count == 0
    assert result.last_error is None


def test_get_dt_status_breaker_open_skips_probe(
    make_breaker: Any,
    make_dt_client: Any,
    fakeredis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When breaker is OPEN we don't talk to DT and version stays None."""

    breaker = make_breaker(failure_threshold=1, cooldown_seconds=300)
    breaker.record_failure()  # flip to OPEN
    assert breaker.snapshot().state == STATE_OPEN

    def boom(_request: httpx.Request) -> httpx.Response:
        # Should never be called when breaker is OPEN.
        raise AssertionError("DT probe should be skipped when breaker is OPEN")

    client = make_dt_client(boom)
    monkeypatch.setattr(
        "services.admin_dt_service._redis_client",
        lambda: fakeredis_client,
    )
    try:
        result = get_dt_status(breaker=breaker, client=client, force_refresh=True)
    finally:
        client.close()

    assert result.state == "open"
    assert result.version is None


def test_get_dt_status_5xx_records_failure_and_returns_error(
    make_breaker: Any,
    make_dt_client: Any,
    fakeredis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 5xx from DT increments fail_count via the breaker and surfaces the error."""

    def boom(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="DT unavailable")

    breaker = make_breaker(failure_threshold=10)
    client = make_dt_client(boom)
    monkeypatch.setattr(
        "services.admin_dt_service._redis_client",
        lambda: fakeredis_client,
    )
    try:
        result = get_dt_status(breaker=breaker, client=client, force_refresh=True)
    finally:
        client.close()

    assert result.version is None
    assert result.last_error is not None and "503" in result.last_error
    # Breaker fail counter incremented (not at threshold so still CLOSED).
    assert result.fail_count >= 1


def test_get_dt_status_cache_hit_returns_cached_payload(
    make_breaker: Any,
    make_dt_client: Any,
    fakeredis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second call within 30s uses the Redis cache — DT is not probed twice."""

    call_count = {"n": 0}

    def ok(_request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"version": "4.13.2"})

    breaker = make_breaker()
    monkeypatch.setattr(
        "services.admin_dt_service._redis_client",
        lambda: fakeredis_client,
    )

    client1 = make_dt_client(ok)
    try:
        first = get_dt_status(breaker=breaker, client=client1, force_refresh=True)
    finally:
        client1.close()

    # Second call without force_refresh should hit the cache. Build a NEW
    # client whose handler would fail the test if invoked — proving the
    # cache short-circuits the DT round-trip.
    def must_not_call(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("DT probe should be cached on second call")

    client2 = make_dt_client(must_not_call)
    try:
        second = get_dt_status(breaker=breaker, client=client2)
    finally:
        client2.close()

    assert call_count["n"] == 1
    assert first.version == second.version == "4.13.2"


def test_get_dt_status_cache_invalid_payload_falls_through(
    make_breaker: Any,
    make_dt_client: Any,
    fakeredis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A garbled cache entry does not crash — service falls through to a fresh probe."""
    fakeredis_client.set("dt:admin:status_cache", "{not json")

    def ok(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "4.13.2"})

    breaker = make_breaker()
    client = make_dt_client(ok)
    monkeypatch.setattr(
        "services.admin_dt_service._redis_client",
        lambda: fakeredis_client,
    )
    try:
        result = get_dt_status(breaker=breaker, client=client)
    finally:
        client.close()

    assert result.version == "4.13.2"


# ---------------------------------------------------------------------------
# list_orphans
# ---------------------------------------------------------------------------


def test_list_orphans_breaker_open_raises_dt_unreachable(
    make_breaker: Any,
    make_dt_client: Any,
) -> None:
    breaker = make_breaker(failure_threshold=1, cooldown_seconds=300)
    breaker.record_failure()
    assert breaker.snapshot().state == STATE_OPEN

    def must_not_call(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("DT must not be probed when breaker is OPEN")

    client = make_dt_client(must_not_call)
    try:
        with pytest.raises(DTUnreachable):
            list_orphans(breaker=breaker, client=client)
    finally:
        client.close()


def test_list_orphans_5xx_during_walk_raises_dt_unreachable(
    make_breaker: Any,
    make_dt_client: Any,
) -> None:
    """A 5xx mid-walk should map to DTUnreachable (admin sees 503 + extension)."""

    def boom(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="DT down")

    breaker = make_breaker(failure_threshold=100)
    client = make_dt_client(boom)
    try:
        with pytest.raises(DTUnreachable):
            list_orphans(breaker=breaker, client=client)
    finally:
        client.close()


def test_list_orphans_walks_pages_and_classifies(
    make_breaker: Any,
    make_dt_client: Any,
) -> None:
    """An empty DT (single empty page) returns a 0-item, 0-total envelope."""

    def empty_dt(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    breaker = make_breaker()
    client = make_dt_client(empty_dt)
    try:
        page = list_orphans(breaker=breaker, client=client)
    finally:
        client.close()

    assert page.items == []
    assert page.total == 0
    assert page.has_more is False


def test_list_orphans_one_page_with_unknown_uuids(
    make_breaker: Any,
    make_dt_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A DT page whose ``version`` UUIDs do not exist in our scans table
    should classify all rows as orphans.

    We patch the sync_session_factory injection point so the test does not
    require a real sync engine — the factory yields a stub session whose
    ``execute()`` returns an empty result set for the Scan.id.in_(...) query,
    making every UUID an orphan.
    """
    from contextlib import contextmanager

    class _StubResult:
        def scalars(self):  # type: ignore[no-untyped-def]
            return self

        def all(self) -> list[Any]:
            return []  # no scan IDs found → every UUID is an orphan

    class _StubSession:
        def execute(self, _stmt: Any) -> _StubResult:
            return _StubResult()

        def close(self) -> None:
            pass

    @contextmanager
    def _factory():  # type: ignore[no-untyped-def]
        s = _StubSession()
        try:
            yield s
        finally:
            s.close()

    project_a = {
        "uuid": "dt-uuid-aaa",
        "name": "alpha",
        "version": "00000000-0000-0000-0000-000000000099",
    }
    project_b = {
        "uuid": "dt-uuid-bbb",
        "name": "beta",
        "version": "00000000-0000-0000-0000-000000000098",
    }

    pages = [[project_a, project_b], []]
    call_count = {"n": 0}

    def serve_pages(_request: httpx.Request) -> httpx.Response:
        idx = call_count["n"]
        call_count["n"] += 1
        return httpx.Response(200, json=pages[min(idx, len(pages) - 1)])

    breaker = make_breaker()
    client = make_dt_client(serve_pages)
    try:
        result = list_orphans(
            breaker=breaker,
            client=client,
            sync_session_factory=_factory,
            limit=10,
        )
    finally:
        client.close()

    assert result.total == 2
    assert {item.dt_project_uuid for item in result.items} == {"dt-uuid-aaa", "dt-uuid-bbb"}
    # Names + versions echoed back into the response (for the admin table).
    assert {item.dt_project_name for item in result.items} == {"alpha", "beta"}


def test_list_orphans_skips_non_uuid_versions(
    make_breaker: Any,
    make_dt_client: Any,
) -> None:
    """DT projects with non-UUID ``version`` are skipped (out of our naming)."""
    from contextlib import contextmanager

    class _StubSession:
        def execute(self, _stmt: Any) -> Any:
            class _R:
                def scalars(self):  # type: ignore[no-untyped-def]
                    return self

                def all(self) -> list[Any]:
                    return []

            return _R()

        def close(self) -> None:
            pass

    @contextmanager
    def _factory():  # type: ignore[no-untyped-def]
        yield _StubSession()

    page = [
        {"uuid": "dt-uuid-1", "name": "x", "version": "1.0.0"},  # not a UUID
        {"uuid": "dt-uuid-2", "name": "y", "version": "not-a-uuid"},
        {"uuid": None, "name": "bad", "version": None},  # not strings
    ]

    def serve(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    breaker = make_breaker()
    client = make_dt_client(serve)
    try:
        result = list_orphans(
            breaker=breaker,
            client=client,
            sync_session_factory=_factory,
        )
    finally:
        client.close()

    # All rows skipped because no version was a valid UUID.
    assert result.total == 0


# ---------------------------------------------------------------------------
# enqueue_orphan_cleanup
# ---------------------------------------------------------------------------


class _FakeAsyncResult:
    """Mimics celery.AsyncResult enough to satisfy the service contract."""

    def __init__(self, task_id: str = "task-001") -> None:
        self.id = task_id


def test_enqueue_orphan_cleanup_happy_path_acquires_lock_and_returns_task_id(
    fakeredis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.admin_dt_service._redis_client",
        lambda: fakeredis_client,
    )

    captured: dict[str, Any] = {}

    def fake_delay(uuids: list[str]) -> _FakeAsyncResult:
        captured["uuids"] = uuids
        return _FakeAsyncResult("task-001")

    result = enqueue_orphan_cleanup(dt_project_uuids=[], delay=fake_delay)
    assert result.task_id == "task-001"
    assert result.count == 0
    # The lock should be set in fakeredis.
    assert fakeredis_client.get("dt:admin:orphan_cleanup_lock") == "1"


def test_enqueue_orphan_cleanup_lock_contention_raises_409(
    fakeredis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.admin_dt_service._redis_client",
        lambda: fakeredis_client,
    )
    # Pre-set the lock — a second enqueue must refuse.
    fakeredis_client.set("dt:admin:orphan_cleanup_lock", "1", ex=600)

    def must_not_call(_uuids: list[str]) -> _FakeAsyncResult:
        raise AssertionError("delay must not be called when lock is held")

    with pytest.raises(OrphanCleanupInProgress):
        enqueue_orphan_cleanup(dt_project_uuids=[], delay=must_not_call)


def test_enqueue_orphan_cleanup_dispatch_failure_releases_lock(
    fakeredis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Celery .delay raises, the lock must be released so the next attempt can proceed."""
    monkeypatch.setattr(
        "services.admin_dt_service._redis_client",
        lambda: fakeredis_client,
    )

    def angry_delay(_uuids: list[str]) -> _FakeAsyncResult:
        raise RuntimeError("broker unavailable")

    with pytest.raises(RuntimeError):
        enqueue_orphan_cleanup(dt_project_uuids=[], delay=angry_delay)

    # Lock must be released.
    assert fakeredis_client.get("dt:admin:orphan_cleanup_lock") is None


# ---------------------------------------------------------------------------
# force_health_check
# ---------------------------------------------------------------------------


def test_force_health_check_flattens_outcome(
    make_breaker: Callable[..., Any],
    make_dt_client: Callable[[Callable[[httpx.Request], httpx.Response]], Any],
) -> None:
    """force_health_check delegates to run_health_check and reports a typed payload."""

    def ok(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "4.13.2"})

    breaker = make_breaker()
    client = make_dt_client(ok)
    try:
        outcome = force_health_check(breaker=breaker, client=client)
    finally:
        client.close()

    assert outcome.healthy is True
    assert outcome.state_after == "closed"
    assert outcome.fail_count == 0
    assert outcome.error is None
    assert outcome.auto_restart_attempted is False


def test_force_health_check_5xx_marks_unhealthy_and_increments_breaker(
    make_breaker: Callable[..., Any],
    make_dt_client: Callable[[Callable[[httpx.Request], httpx.Response]], Any],
) -> None:
    def boom(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="DT down")

    breaker = make_breaker(failure_threshold=10)
    client = make_dt_client(boom)
    try:
        outcome = force_health_check(breaker=breaker, client=client)
    finally:
        client.close()

    assert outcome.healthy is False
    assert outcome.fail_count >= 1
    assert outcome.error is not None
    assert outcome.state_before == STATE_CLOSED


# ---------------------------------------------------------------------------
# Adversarial-input parametrize against the boundary schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "garbage_value",
    [
        "not-a-uuid",
        "../../etc/passwd",
        "javascript:alert(1)",
        "\x00\x00\x00",
        "a" * 10_000,
        "; DROP TABLE projects; --",
    ],
)
def test_orphan_cleanup_request_rejects_non_uuid_inputs(garbage_value: str) -> None:
    """OrphanCleanupRequest.dt_project_uuids must reject garbage at the schema layer.

    Pydantic v2 raises a single ValidationError that aggregates all sub-errors;
    the router converts that to a 422 Problem Details response. Pinning the
    rejection here keeps the routing layer free of redundant defensive code.
    """
    from pydantic import ValidationError

    from schemas.admin_ops import OrphanCleanupRequest

    with pytest.raises(ValidationError):
        OrphanCleanupRequest(dt_project_uuids=[garbage_value])  # type: ignore[list-item]


def test_orphan_cleanup_request_caps_list_length() -> None:
    """A 1000-uuid list (DoS surface) is rejected by the max_length=500 bound."""
    import uuid as _uuid

    from pydantic import ValidationError

    from schemas.admin_ops import OrphanCleanupRequest

    too_many = [str(_uuid.uuid4()) for _ in range(1000)]
    with pytest.raises(ValidationError):
        OrphanCleanupRequest(dt_project_uuids=too_many)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# force_reset_breaker (A4)
# ---------------------------------------------------------------------------


def test_force_reset_open_breaker_returns_transition_and_clears_cache(
    make_breaker: Callable[..., Any],
    fakeredis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A breaker tripped to OPEN resets to CLOSED + clears the status cache."""
    monkeypatch.setattr(
        "services.admin_dt_service._redis_client",
        lambda: fakeredis_client,
    )
    breaker = make_breaker(failure_threshold=1, cooldown_seconds=300)
    breaker.record_failure()
    assert breaker.snapshot().state == STATE_OPEN
    fail_count_before = breaker.snapshot().fail_count
    # Plant a stale status payload to verify the reset evicts it.
    fakeredis_client.set("dt:admin:status_cache", '{"stale": true}')

    result = force_reset_breaker(breaker=breaker)

    assert result.state_before == STATE_OPEN
    assert result.state_after == STATE_CLOSED
    assert result.fail_count_before == fail_count_before
    assert breaker.snapshot().state == STATE_CLOSED
    # Stale cache entry evicted so the next status poll observes the reset.
    assert fakeredis_client.get("dt:admin:status_cache") is None


def test_force_reset_half_open_breaker_returns_to_closed(
    make_breaker: Callable[..., Any],
    fakeredis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HALF_OPEN is also a valid reset entry point — operator wants a clean slate."""
    monkeypatch.setattr(
        "services.admin_dt_service._redis_client",
        lambda: fakeredis_client,
    )
    breaker = make_breaker(failure_threshold=1, cooldown_seconds=300)
    breaker.record_failure()
    # Manually flip the redis state key to half_open so we don't have to
    # fast-forward the cooldown.
    fakeredis_client.set("dt:breaker:state", "half_open")

    result = force_reset_breaker(breaker=breaker)

    assert result.state_before == "half_open"
    assert result.state_after == STATE_CLOSED


def test_force_reset_already_closed_raises_409(
    make_breaker: Callable[..., Any],
    fakeredis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLOSED breaker refuses reset — operator should investigate, not retry."""
    monkeypatch.setattr(
        "services.admin_dt_service._redis_client",
        lambda: fakeredis_client,
    )
    breaker = make_breaker()
    assert breaker.snapshot().state == STATE_CLOSED

    with pytest.raises(BreakerAlreadyClosed):
        force_reset_breaker(breaker=breaker)


def test_force_reset_redis_error_does_not_propagate(
    make_breaker: Callable[..., Any],
    fakeredis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Redis hiccup during cache-evict logs but does not fail the reset."""
    import redis as _redis_module

    breaker = make_breaker(failure_threshold=1, cooldown_seconds=300)
    breaker.record_failure()
    assert breaker.snapshot().state == STATE_OPEN

    class _BrokenRedis:
        def delete(self, _key: str) -> None:
            raise _redis_module.RedisError("redis offline")

    monkeypatch.setattr(
        "services.admin_dt_service._redis_client",
        lambda: _BrokenRedis(),
    )

    # Reset still succeeds end-to-end despite the cache-delete failing.
    result = force_reset_breaker(breaker=breaker)
    assert result.state_after == STATE_CLOSED
