"""
Unit tests for ``tasks.vuln_sla_sweep`` (no DB), X1 step 2.

Covers the window arithmetic (``_window_start`` / ``_parse_anchor``), the
in-app payload builder, the toggle short-circuit, the enqueue fan-out
(recorded ``delay``), and what the run body returns for the task-run
recorder (the next run's anchor, or a skip that must not become one). The
selection contract itself is SQL since #569 and lives in
``tests/integration/test_vuln_sla_sweep_db.py`` together with the anchor read
from ``task_runs`` and the missed-day lifecycle.
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


def test_run_sweep_disabled_never_opens_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """And returns no ``detail_code``: the recorder writes ``skipped`` and the
    row is never an anchor."""
    monkeypatch.setenv("VULN_SLA_ALERTS_ENABLED", "false")

    def _explode(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the disabled sweep must not touch the DB")

    monkeypatch.setattr(sweep_module, "sync_session_scope", _explode)

    summary = sweep_module._run_sweep()
    assert summary["skipped"] is True
    assert summary["skipped_reason"] == "disabled"
    assert summary["notifications_enqueued"] == 0
    assert "detail_code" not in summary


class _NullSession:
    def __enter__(self) -> _NullSession:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


def _stub_run(
    monkeypatch: pytest.MonkeyPatch,
    anchor: datetime | None,
    *,
    breached: Any = None,
) -> list[dict[str, Any]]:
    """Wire ``_run_sweep`` to fakes; returns the list ``_breached_counts``
    calls are recorded into."""
    monkeypatch.setenv("VULN_SLA_ALERTS_ENABLED", "true")
    monkeypatch.setattr(sweep_module, "sync_session_scope", _NullSession)
    monkeypatch.setattr(sweep_module, "_load_anchor", lambda _s: anchor)
    calls: list[dict[str, Any]] = []

    def _counts(_session: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        if isinstance(breached, Exception):
            raise breached
        return breached or {}

    monkeypatch.setattr(sweep_module, "_breached_counts", _counts)
    monkeypatch.setattr(sweep_module, "_enqueue_notifications", len)
    monkeypatch.setattr(sweep_module, "_team_member_user_ids", lambda _s, _t: [uuid.uuid4()])
    return calls


def test_run_starts_at_the_anchor_and_reports_now_as_the_window_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The returned ``detail_params.window_end`` is what the recorder stores
    and the next run reads, so it has to be this run's ``now``."""
    anchor = _NOW - timedelta(days=2)
    pid = uuid.uuid4()
    calls = _stub_run(
        monkeypatch, anchor, breached={pid: ("portal", uuid.uuid4(), {"critical": 1})}
    )

    summary = sweep_module._run_sweep(now=_NOW)

    assert calls == [{"window_start": anchor, "now": _NOW}]
    assert summary["detail_code"] == sweep_module._DETAIL_CODE
    assert summary["detail_params"] == {
        "window_start": anchor.isoformat(),
        "window_end": _NOW.isoformat(),
        "projects_breached": 1,
        "findings_crossed": 1,
        "notifications_enqueued": 1,
    }
    assert "skipped_reason" in summary and summary["skipped_reason"] is None


def test_no_anchor_starts_at_the_trailing_day(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_run(monkeypatch, None)

    sweep_module._run_sweep(now=_NOW)

    assert calls == [{"window_start": _NOW - timedelta(hours=24), "now": _NOW}]


def test_a_failed_read_is_reported_as_a_skip_without_a_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the Celery entry point, which swallows the error. The summary
    must carry a ``skipped_reason`` (the recorder then writes ``skipped``) and
    no ``detail_code``, or the run would become the anchor for a span it never
    covered."""
    _stub_run(monkeypatch, _NOW - timedelta(days=1), breached=RuntimeError("db gone"))

    summary = sweep_module.vuln_sla_sweep.run()

    assert summary["skipped"] is True
    assert summary["skipped_reason"] == "unexpected:RuntimeError"
    assert "detail_code" not in summary
    assert "detail_params" not in summary


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
