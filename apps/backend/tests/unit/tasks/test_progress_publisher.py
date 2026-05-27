"""
Unit tests for `tasks._progress` — Phase 2 PR #9.

The publisher is fire-and-forget by design (the DB row is the source of
truth for scan progress). These tests pin:

  - The published payload shape: ``{"percent": int, "step": str, "ts": iso8601}``.
  - Channel naming is delegated to ``core.config.scan_progress_channel`` —
    we assert end-to-end by subscribing on the same fakeredis instance.
  - Percent clamping: a misconfigured caller cannot poison the UI with a
    negative or >100 number.
  - Failure isolation: when ``redis.publish`` raises, the function returns
    cleanly (never propagates) so a flaky broker cannot crash a scan.
  - Lazy singleton + reset hook: rotating ``REDIS_URL`` rebuilds the client.

Why fakeredis instead of MagicMock: the publisher commits us to a real
pub/sub contract (channel + payload), and we want the tests to fail if a
future refactor accidentally swaps to ``rpush`` or changes the channel
name. fakeredis is a contract-level fixture; MagicMock would just rubber
stamp whatever the implementation does.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fakeredis_module() -> Any:
    """Import fakeredis lazily; skip the suite gracefully when absent."""
    return pytest.importorskip("fakeredis")


@pytest.fixture
def fake_redis(fakeredis_module: Any) -> Iterator[Any]:
    """Yield a fresh fakeredis FakeStrictRedis (decode_responses=False).

    decode_responses=False matches the production publisher — bytes
    payloads on the wire, JSON.loads on the subscriber side.
    """
    client = fakeredis_module.FakeStrictRedis(decode_responses=False)
    try:
        yield client
    finally:
        try:
            client.flushall()
        finally:
            client.close()


@pytest.fixture
def patched_publisher(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> Iterator[Any]:
    """Replace the singleton client with a fakeredis instance for the test.

    The publisher uses a module-level lazy singleton; we bypass the
    ``redis.Redis.from_url`` call by monkey-patching ``_get_client`` to
    return our fake client directly. We also reset the singleton afterward
    so test ordering does not leak.
    """
    from tasks import _progress

    monkeypatch.setattr(_progress, "_get_client", lambda: fake_redis)
    yield _progress
    _progress.reset_publisher_for_tests()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def _capture_publishes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bytes]]:
    """Wrap ``redis.Redis.publish`` so we can assert calls without pubsub.

    fakeredis' pubsub delivery has timing edges in single-threaded test
    runners (the subscriber thread does not always drain before the test
    asserts). Capturing publish calls directly is deterministic and still
    pins channel + payload — the contract we actually care about.
    """
    captured: list[tuple[str, bytes]] = []

    from tasks import _progress

    real_get_client = _progress._get_client

    class _Wrapper:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def publish(self, channel: Any, message: Any) -> int:
            ch = channel.decode("utf-8") if isinstance(channel, bytes) else str(channel)
            body = message if isinstance(message, bytes) else str(message).encode("utf-8")
            captured.append((ch, body))
            result: int = int(self._inner.publish(channel, message))
            return result

    monkeypatch.setattr(_progress, "_get_client", lambda: _Wrapper(real_get_client()))
    return captured


def test_publish_progress_emits_expected_payload_on_channel(
    patched_publisher: Any, fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A publish hits ``scan:<scan_id>:progress`` with the documented JSON envelope."""
    captured = _capture_publishes(monkeypatch)
    scan_id = uuid.uuid4()

    patched_publisher.publish_progress(scan_id, step="cdxgen", percent=25)

    assert len(captured) == 1
    channel, body = captured[0]
    assert channel == f"scan:{scan_id}:progress"
    payload = json.loads(body.decode("utf-8"))
    assert payload["percent"] == 25
    assert payload["step"] == "cdxgen"
    # P2 #8c — progress frames now carry an explicit ``type`` discriminator
    # so the FE can fan out progress vs log frames on a single channel.
    # Older clients that ignore the field still see the historical envelope.
    assert payload["type"] == "progress"
    assert isinstance(payload["ts"], str) and "T" in payload["ts"]


def test_publish_progress_accepts_uuid_and_string(
    patched_publisher: Any, fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both `uuid.UUID` and stringified scan IDs land on the same channel."""
    captured = _capture_publishes(monkeypatch)
    scan_uuid = uuid.uuid4()

    patched_publisher.publish_progress(scan_uuid, step="bootstrap", percent=0)
    patched_publisher.publish_progress(str(scan_uuid), step="finalize", percent=100)

    assert len(captured) == 2
    assert captured[0][0] == captured[1][0]
    assert captured[0][0] == f"scan:{scan_uuid}:progress"
    assert json.loads(captured[0][1].decode("utf-8"))["step"] == "bootstrap"
    assert json.loads(captured[1][1].decode("utf-8"))["step"] == "finalize"


# ---------------------------------------------------------------------------
# Clamping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("input_percent", "expected"),
    [
        (-5, 0),
        (0, 0),
        (50, 50),
        (100, 100),
        (150, 100),
        (10**6, 100),
    ],
)
def test_publish_progress_clamps_percent(
    patched_publisher: Any,
    fake_redis: Any,
    monkeypatch: pytest.MonkeyPatch,
    input_percent: int,
    expected: int,
) -> None:
    """A negative or oversized percent is squashed into [0, 100]."""
    captured = _capture_publishes(monkeypatch)
    patched_publisher.publish_progress(uuid.uuid4(), step="cdxgen", percent=input_percent)
    assert len(captured) == 1
    body = json.loads(captured[0][1].decode("utf-8"))
    assert body["percent"] == expected


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


def test_publish_progress_swallows_redis_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken Redis client must NOT crash the caller — log + return."""
    from tasks import _progress

    class _BrokenClient:
        def publish(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("simulated broker outage")

    monkeypatch.setattr(_progress, "_get_client", lambda: _BrokenClient())

    # No exception leaks. The test passes if this returns normally.
    _progress.publish_progress(uuid.uuid4(), step="cdxgen", percent=25)

    _progress.reset_publisher_for_tests()


def test_publish_progress_swallows_serialization_errors(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    """Even non-redis errors (e.g. JSON encode) cannot crash the scan."""
    from tasks import _progress

    monkeypatch.setattr(_progress, "_get_client", lambda: fake_redis)

    class _Boom:
        def __str__(self) -> str:
            raise RuntimeError("cannot stringify scan_id")

    # `step` is a string, but if str(scan_id) blows up inside the publisher
    # we still want a clean return, not a propagated exception. The except
    # in publish_progress catches Exception, so this exercise is valid.
    _progress.publish_progress(_Boom(), step="cdxgen", percent=25)  # type: ignore[arg-type]

    _progress.reset_publisher_for_tests()


# ---------------------------------------------------------------------------
# Lazy singleton + reset hook
# ---------------------------------------------------------------------------


def test_get_client_returns_same_instance_within_a_url(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    """Two consecutive calls reuse one connection — the whole point of the singleton."""
    from tasks import _progress

    monkeypatch.setenv("REDIS_URL", "redis://primary.local:6379/0")
    monkeypatch.setattr(
        "tasks._progress.redis.Redis.from_url",
        lambda *_args, **_kwargs: fake_redis,
    )
    _progress.reset_publisher_for_tests()

    a = _progress._get_client()
    b = _progress._get_client()
    assert a is b

    _progress.reset_publisher_for_tests()


def test_reset_publisher_for_tests_drops_cached_client(
    monkeypatch: pytest.MonkeyPatch, fakeredis_module: Any
) -> None:
    """After a reset, the next call rebuilds the client (different instance).

    We use two distinct fakeredis clients so identity, not equality, asserts.
    """
    from tasks import _progress

    monkeypatch.setenv("REDIS_URL", "redis://only-one-url.local:6379/0")
    instances = iter(
        [
            fakeredis_module.FakeStrictRedis(decode_responses=False),
            fakeredis_module.FakeStrictRedis(decode_responses=False),
        ]
    )
    monkeypatch.setattr(
        "tasks._progress.redis.Redis.from_url",
        lambda *_args, **_kwargs: next(instances),
    )

    _progress.reset_publisher_for_tests()
    first = _progress._get_client()
    _progress.reset_publisher_for_tests()
    second = _progress._get_client()

    assert first is not second

    _progress.reset_publisher_for_tests()


def test_get_client_rebuilds_on_redis_url_rotation(
    monkeypatch: pytest.MonkeyPatch, fakeredis_module: Any
) -> None:
    """CLAUDE.md core rule #11 — env changes must not be cached past their lifetime.

    When ``REDIS_URL`` changes between calls (e.g. an operator rotated the
    broker host), we must rebuild the client instead of reusing the stale one.
    """
    from tasks import _progress

    instances = iter(
        [
            fakeredis_module.FakeStrictRedis(decode_responses=False),
            fakeredis_module.FakeStrictRedis(decode_responses=False),
        ]
    )
    monkeypatch.setattr(
        "tasks._progress.redis.Redis.from_url",
        lambda *_args, **_kwargs: next(instances),
    )

    _progress.reset_publisher_for_tests()

    monkeypatch.setenv("REDIS_URL", "redis://broker-a.local:6379/0")
    first = _progress._get_client()
    monkeypatch.setenv("REDIS_URL", "redis://broker-b.local:6379/0")
    second = _progress._get_client()

    assert first is not second

    _progress.reset_publisher_for_tests()


# ---------------------------------------------------------------------------
# Now-iso helper
# ---------------------------------------------------------------------------


def test_now_iso_is_utc_iso8601() -> None:
    """The timestamp string is parseable as a UTC datetime."""
    from datetime import datetime

    from tasks._progress import _now_iso

    raw = _now_iso()
    parsed = datetime.fromisoformat(raw)
    assert parsed.tzinfo is not None
    # offset 0 == UTC. (datetime.fromisoformat preserves tzinfo.)
    assert parsed.utcoffset() is not None
    assert parsed.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# P2 #8c — publish_log
# ---------------------------------------------------------------------------


def test_publish_log_emits_log_frame_on_same_channel(
    patched_publisher: Any, fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Log frames share the progress channel and carry ``type: "log"``."""
    captured = _capture_publishes(monkeypatch)
    scan_id = uuid.uuid4()
    patched_publisher.reset_log_counter(scan_id)

    patched_publisher.publish_log(
        scan_id, stage="cdxgen", stream="stdout", line="resolving packages…"
    )

    assert len(captured) == 1
    channel, body = captured[0]
    assert channel == f"scan:{scan_id}:progress"
    payload = json.loads(body.decode("utf-8"))
    assert payload["type"] == "log"
    assert payload["stage"] == "cdxgen"
    assert payload["stream"] == "stdout"
    assert payload["line"] == "resolving packages…"
    assert isinstance(payload["ts"], str) and "T" in payload["ts"]


def test_publish_log_normalises_unknown_stream_to_stdout(
    patched_publisher: Any, fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo / hostile stream label degrades to ``stdout`` rather than blocks."""
    captured = _capture_publishes(monkeypatch)
    scan_id = uuid.uuid4()
    patched_publisher.reset_log_counter(scan_id)

    patched_publisher.publish_log(
        scan_id, stage="scancode", stream="garbage", line="hi"
    )

    payload = json.loads(captured[0][1].decode("utf-8"))
    assert payload["stream"] == "stdout"


def test_publish_log_truncates_long_lines(
    patched_publisher: Any,
    fake_redis: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single mega-line is truncated at SCAN_LOG_LINE_MAX_LEN and marked."""
    monkeypatch.setenv("SCAN_LOG_LINE_MAX_LEN", "80")
    captured = _capture_publishes(monkeypatch)
    scan_id = uuid.uuid4()
    patched_publisher.reset_log_counter(scan_id)

    long_line = "x" * 5_000
    patched_publisher.publish_log(
        scan_id, stage="cdxgen", stream="stdout", line=long_line
    )

    payload = json.loads(captured[0][1].decode("utf-8"))
    assert len(payload["line"]) <= 80
    assert payload["line"].endswith("…(truncated)")
    # The truncation suffix must NOT be omitted — without it the consumer
    # cannot tell a truncated line apart from a real 80-char line.


def test_publish_log_enforces_per_scan_budget(
    patched_publisher: Any, fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Over-budget lines are dropped on the publisher side (no Redis call)."""
    monkeypatch.setenv("SCAN_LOG_MAX_LINES_PER_SCAN", "3")
    captured = _capture_publishes(monkeypatch)
    scan_id = uuid.uuid4()
    patched_publisher.reset_log_counter(scan_id)

    for i in range(10):
        patched_publisher.publish_log(
            scan_id, stage="cdxgen", stream="stdout", line=f"line {i}"
        )

    # Only the first 3 lines made it onto the wire.
    assert len(captured) == 3
    for _ch, body in captured:
        assert json.loads(body.decode("utf-8"))["type"] == "log"


def test_publish_log_kill_switch_via_zero_budget(
    patched_publisher: Any, fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SCAN_LOG_MAX_LINES_PER_SCAN=0 acts as a kill switch — nothing publishes."""
    monkeypatch.setenv("SCAN_LOG_MAX_LINES_PER_SCAN", "0")
    captured = _capture_publishes(monkeypatch)
    scan_id = uuid.uuid4()
    patched_publisher.reset_log_counter(scan_id)

    patched_publisher.publish_log(
        scan_id, stage="cdxgen", stream="stdout", line="should not appear"
    )

    assert captured == []


def test_publish_log_swallows_redis_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken Redis client must NOT crash the caller — log + return."""
    from tasks import _progress

    class _BrokenClient:
        def publish(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("simulated broker outage")

    monkeypatch.setattr(_progress, "_get_client", lambda: _BrokenClient())
    _progress.reset_log_counter("unit-broken-publish")

    # No exception leaks. The test passes if this returns normally.
    _progress.publish_log(
        "unit-broken-publish",
        stage="cdxgen",
        stream="stdout",
        line="anything",
    )

    _progress.reset_publisher_for_tests()


def test_reset_log_counter_clears_budget(
    patched_publisher: Any, fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-running the same scan resets the per-scan budget."""
    monkeypatch.setenv("SCAN_LOG_MAX_LINES_PER_SCAN", "2")
    captured = _capture_publishes(monkeypatch)
    scan_id = uuid.uuid4()

    patched_publisher.reset_log_counter(scan_id)
    for i in range(5):
        patched_publisher.publish_log(
            scan_id, stage="cdxgen", stream="stdout", line=f"a{i}"
        )
    assert len(captured) == 2

    # Re-run: budget resets, two more lines may publish.
    patched_publisher.reset_log_counter(scan_id)
    for i in range(5):
        patched_publisher.publish_log(
            scan_id, stage="cdxgen", stream="stdout", line=f"b{i}"
        )
    assert len(captured) == 4


def test_publish_log_isolates_scans_from_each_other(
    patched_publisher: Any, fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One scan exhausting its budget must not silence a sibling scan."""
    monkeypatch.setenv("SCAN_LOG_MAX_LINES_PER_SCAN", "1")
    captured = _capture_publishes(monkeypatch)
    scan_a = uuid.uuid4()
    scan_b = uuid.uuid4()
    patched_publisher.reset_log_counter(scan_a)
    patched_publisher.reset_log_counter(scan_b)

    # scan_a uses its single slot.
    patched_publisher.publish_log(scan_a, stage="cdxgen", stream="stdout", line="a1")
    patched_publisher.publish_log(scan_a, stage="cdxgen", stream="stdout", line="a2")
    # scan_b has its own slot.
    patched_publisher.publish_log(scan_b, stage="cdxgen", stream="stdout", line="b1")

    assert len(captured) == 2
    channels = {ch for ch, _ in captured}
    assert f"scan:{scan_a}:progress" in channels
    assert f"scan:{scan_b}:progress" in channels


def test_truncate_line_kill_switch_returns_empty() -> None:
    """``_truncate_line(_, limit<=0)`` short-circuits to empty string."""
    from tasks._progress import _truncate_line

    assert _truncate_line("hello world", 0) == ""
    assert _truncate_line("hello world", -1) == ""


def test_truncate_line_passes_through_when_under_limit() -> None:
    from tasks._progress import _truncate_line

    assert _truncate_line("short", 80) == "short"
