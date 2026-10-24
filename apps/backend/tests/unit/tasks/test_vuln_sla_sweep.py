"""
Unit tests for ``tasks.vuln_sla_sweep`` (no DB), X1 step 2.

Covers the window arithmetic (``_window_start`` / ``_parse_anchor``), the
in-app payload builder, the toggle short-circuit, the enqueue fan-out
(recorded ``delay``), and the order of the run body (the anchor moves only
after the work is done). The selection contract itself is SQL since #569 and
lives in ``tests/integration/test_vuln_sla_sweep_db.py`` together with the
missed-day lifecycle.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

import tasks.vuln_sla_sweep as sweep_module
from tasks.vuln_sla_sweep import (
    _DEFAULT_WINDOW,
    _MAX_CATCH_UP,
    _build_in_app_payload,
    _enqueue_notifications,
    _parse_anchor,
    _window_start,
)

_NOW = datetime(2026, 7, 25, 2, 45, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# _window_start
# ---------------------------------------------------------------------------


def test_no_anchor_uses_the_trailing_day() -> None:
    assert _window_start(None, now=_NOW) == _NOW - timedelta(hours=24)


def test_default_window_is_one_beat_interval() -> None:
    assert _DEFAULT_WINDOW == timedelta(hours=24)


def test_anchor_from_the_previous_tick_starts_the_window() -> None:
    anchor = _NOW - timedelta(hours=23, minutes=59)
    assert _window_start(anchor, now=_NOW) == anchor


def test_anchor_after_a_missed_day_reaches_back_over_the_gap() -> None:
    """The point of #569: two days since the last completed tick means a
    two-day window, not the trailing 24h."""
    anchor = _NOW - timedelta(days=2)
    assert _window_start(anchor, now=_NOW) == anchor


def test_anchor_at_the_catch_up_limit_is_kept() -> None:
    anchor = _NOW - _MAX_CATCH_UP
    assert _window_start(anchor, now=_NOW) == anchor


def test_anchor_past_the_catch_up_limit_is_clamped() -> None:
    anchor = _NOW - _MAX_CATCH_UP - timedelta(seconds=1)
    assert _window_start(anchor, now=_NOW) == _NOW - _MAX_CATCH_UP


def test_catch_up_limit_is_a_week() -> None:
    assert _MAX_CATCH_UP == timedelta(days=7)


@pytest.mark.parametrize("ahead", [timedelta(0), timedelta(hours=3)])
def test_anchor_not_before_now_falls_back_to_the_trailing_day(
    ahead: timedelta,
) -> None:
    """An anchor at or after ``now`` (restored snapshot, clock moved back)
    would give an empty or inverted window; it is treated as absent."""
    assert _window_start(_NOW + ahead, now=_NOW) == _NOW - timedelta(hours=24)


# ---------------------------------------------------------------------------
# _parse_anchor
# ---------------------------------------------------------------------------


def test_parse_round_trips_what_the_sweep_stores() -> None:
    assert _parse_anchor(_NOW.isoformat()) == _NOW


def test_parse_normalises_offsets_to_utc() -> None:
    kst = _NOW.astimezone(timezone(timedelta(hours=9)))
    parsed = _parse_anchor(kst.isoformat())
    assert parsed == _NOW
    assert parsed is not None and parsed.utcoffset() == timedelta(0)


def test_parse_accepts_bytes() -> None:
    assert _parse_anchor(_NOW.isoformat().encode()) == _NOW


@pytest.mark.parametrize("raw", [None, "", "yesterday", "2026-07-25T02:45:00"])
def test_parse_rejects_missing_garbage_and_naive_values(raw: Any) -> None:
    """A naive timestamp is rejected rather than guessed at: reading it in
    the wrong zone would shift the window by hours in either direction."""
    assert _parse_anchor(raw) is None


# ---------------------------------------------------------------------------
# _build_in_app_payload
# ---------------------------------------------------------------------------


def test_payload_singular_and_link_free_title() -> None:
    title, body = _build_in_app_payload(
        project_name="portal",
        by_severity={"critical": 1},
        window_start=_NOW - timedelta(hours=24),
    )
    assert title == "SLA breach: 1 finding overdue in portal"
    assert body == (
        "1 open finding crossed the remediation SLA since 2026-07-24 02:45 UTC "
        "(critical 1). Review the overdue list and triage or remediate."
    )


def test_payload_names_the_start_of_a_longer_span() -> None:
    """After a missed day the span is two days; the body must not claim 24h."""
    _title, body = _build_in_app_payload(
        project_name="portal",
        by_severity={"high": 2},
        window_start=_NOW - timedelta(days=2),
    )
    assert "since 2026-07-23 02:45 UTC" in body
    assert "24h" not in body


def test_payload_plural_worst_first_breakdown() -> None:
    title, body = _build_in_app_payload(
        project_name="portal",
        by_severity={"low": 2, "critical": 1, "high": 3},
        window_start=_NOW,
    )
    assert title == "SLA breach: 6 findings overdue in portal"
    assert "(critical 1, high 3, low 2)" in body


def test_payload_unexpected_severity_still_renders() -> None:
    _title, body = _build_in_app_payload(
        project_name="portal",
        by_severity={"critical": 1, "future_sev": 2},
        window_start=_NOW,
    )
    assert "(critical 1, future_sev 2)" in body


def test_status_vocabulary_mirrors_gate() -> None:
    """Contract (hardening rule #2): the sweep imports the gate's closed set;
    assert the import identity so a future local copy cannot drift."""
    from services.policy_gate import _CLOSED_FINDING_STATUSES

    assert sweep_module._CLOSED_FINDING_STATUSES is _CLOSED_FINDING_STATUSES


# ---------------------------------------------------------------------------
# Toggle + run-body ordering
# ---------------------------------------------------------------------------


def test_run_sweep_disabled_never_opens_a_session_or_moves_the_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VULN_SLA_ALERTS_ENABLED", "false")

    def _explode(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the disabled sweep must not touch the DB or Redis")

    monkeypatch.setattr(sweep_module, "sync_session_scope", _explode)
    monkeypatch.setattr(sweep_module, "_redis_client", _explode)

    summary = sweep_module._run_sweep()
    assert summary["skipped"] is True
    assert summary["skipped_reason"] == "disabled"
    assert summary["notifications_enqueued"] == 0


class _FakeRedis:
    def __init__(self, value: str | None = None, *, fail: bool = False) -> None:
        self.value = value
        self.fail = fail
        self.sets: list[str] = []

    def get(self, _key: str) -> str | None:
        if self.fail:
            raise ConnectionError("redis down")
        return self.value

    def set(self, _key: str, value: str) -> None:
        if self.fail:
            raise ConnectionError("redis down")
        self.sets.append(value)
        self.value = value


class _NullSession:
    def __enter__(self) -> _NullSession:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


def _stub_run(
    monkeypatch: pytest.MonkeyPatch,
    redis: _FakeRedis,
    *,
    breached: Any = None,
    order: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Wire ``_run_sweep`` to fakes; returns the list ``_breached_counts``
    calls are recorded into."""
    monkeypatch.setenv("VULN_SLA_ALERTS_ENABLED", "true")
    monkeypatch.setattr(sweep_module, "_redis_client", lambda: redis)
    monkeypatch.setattr(sweep_module, "sync_session_scope", _NullSession)
    calls: list[dict[str, Any]] = []

    def _counts(_session: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        if isinstance(breached, Exception):
            raise breached
        return breached or {}

    def _enqueue(descriptors: list[dict[str, Any]]) -> int:
        if order is not None:
            order.append(f"enqueue:{len(descriptors)}")
        return len(descriptors)

    monkeypatch.setattr(sweep_module, "_breached_counts", _counts)
    monkeypatch.setattr(sweep_module, "_enqueue_notifications", _enqueue)
    monkeypatch.setattr(sweep_module, "_team_member_user_ids", lambda _s, _t: [uuid.uuid4()])
    if order is not None:
        original_set = redis.set

        def _recording_set(key: str, value: str) -> None:
            order.append("anchor")
            original_set(key, value)

        monkeypatch.setattr(redis, "set", _recording_set)
    return calls


def test_run_reads_the_anchor_and_stores_now(monkeypatch: pytest.MonkeyPatch) -> None:
    anchor = _NOW - timedelta(days=2)
    redis = _FakeRedis(anchor.isoformat())
    calls = _stub_run(monkeypatch, redis)

    summary = sweep_module._run_sweep(now=_NOW)

    assert calls == [{"window_start": anchor, "now": _NOW}]
    assert redis.sets == [_NOW.isoformat()]
    assert summary["window_start"] == anchor.isoformat()
    assert summary["window_end"] == _NOW.isoformat()


def test_anchor_is_stored_after_the_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    pid = uuid.uuid4()
    redis = _FakeRedis(None)
    _stub_run(
        monkeypatch,
        redis,
        breached={pid: ("portal", uuid.uuid4(), {"critical": 1})},
        order=order,
    )

    summary = sweep_module._run_sweep(now=_NOW)

    assert order == ["enqueue:1", "anchor"]
    assert summary["projects_breached"] == 1
    assert summary["findings_crossed"] == 1


def test_a_failed_read_leaves_the_anchor_for_the_next_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the Celery entry point, which swallows the error: the span
    this run should have covered is still owed."""
    anchor = _NOW - timedelta(days=1)
    redis = _FakeRedis(anchor.isoformat())
    _stub_run(monkeypatch, redis, breached=RuntimeError("db gone"))
    monkeypatch.setattr(sweep_module, "datetime", _FrozenDatetime)

    summary = sweep_module.vuln_sla_sweep.run()

    assert summary["skipped"] is True
    assert summary["skipped_reason"] == "unexpected:RuntimeError"
    assert redis.sets == []
    assert redis.value == anchor.isoformat()


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> _FrozenDatetime:  # type: ignore[override]
        return cls.fromtimestamp(_NOW.timestamp(), tz=tz)


def test_redis_unavailable_degrades_to_the_trailing_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _FakeRedis(fail=True)
    calls = _stub_run(monkeypatch, redis)

    summary = sweep_module._run_sweep(now=_NOW)

    assert calls == [{"window_start": _NOW - timedelta(hours=24), "now": _NOW}]
    assert summary["skipped"] is False


@pytest.mark.parametrize("token", ["false", "0", "no", "FALSE", " No "])
def test_toggle_falsy_tokens_disable(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import vuln_sla_alerts_enabled

    monkeypatch.setenv("VULN_SLA_ALERTS_ENABLED", token)
    assert vuln_sla_alerts_enabled() is False


@pytest.mark.parametrize("token", ["true", "1", "yes", "", "typo"])
def test_toggle_everything_else_enables(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails OPEN: a typo keeps the sweep on (LICENSE_FETCH_ENABLED rule)."""
    from core.config import vuln_sla_alerts_enabled

    monkeypatch.setenv("VULN_SLA_ALERTS_ENABLED", token)
    assert vuln_sla_alerts_enabled() is True


# ---------------------------------------------------------------------------
# Enqueue fan-out
# ---------------------------------------------------------------------------


def _descriptor(project_id: str | None = None) -> dict[str, Any]:
    return {
        "kind": "vuln_sla_breach",
        "context": {"project_name": "portal", "breach_count": "1"},
        "user_id": str(uuid.uuid4()),
        "title": "SLA breach: 1 finding overdue in portal",
        "body": "1 open finding crossed the remediation SLA since 2026-07-24 02:45 UTC.",
        "link": "/projects/x?tab=vulnerabilities&sla=overdue",
        "project_id": project_id or str(uuid.uuid4()),
    }


def test_enqueue_records_in_app_only_call_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tasks.notify as notify_module

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _record(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(notify_module.send_notification_task, "delay", _record)

    d = _descriptor()
    assert _enqueue_notifications([d]) == 1
    (args, kwargs) = calls[0]
    assert args == (d["kind"], d["context"], [], [])  # channels=[] -> in-app only
    assert kwargs["user_id"] == d["user_id"]
    assert kwargs["in_app_title"] == d["title"]
    assert kwargs["in_app_link"] == d["link"]
    assert kwargs["in_app_target_table"] == "projects"
    assert kwargs["in_app_target_id"] == d["project_id"]


def test_enqueue_swallows_per_descriptor_broker_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tasks.notify as notify_module

    attempts: list[str] = []

    def _flaky(*args: Any, **kwargs: Any) -> None:
        attempts.append(str(kwargs.get("user_id")))
        if len(attempts) == 1:
            raise RuntimeError("broker down")

    monkeypatch.setattr(notify_module.send_notification_task, "delay", _flaky)

    enqueued = _enqueue_notifications([_descriptor(), _descriptor()])
    assert enqueued == 1  # first failed, second went through; never raises
    assert len(attempts) == 2
