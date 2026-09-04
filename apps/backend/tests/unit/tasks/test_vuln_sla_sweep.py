"""
Unit tests for ``tasks.vuln_sla_sweep`` (no DB) — X1 step 2.

Covers the pure selection contract (``_select_breached``: window boundaries,
gate open-status vocabulary, no-SLA severities), the in-app payload builder,
the toggle short-circuit, and the enqueue fan-out (recorded ``delay``).
The DB-backed halves (candidate query, membership fan-out, prefs gating)
live in ``tests/integration/test_vuln_sla_sweep_db.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

import tasks.vuln_sla_sweep as sweep_module
from tasks.vuln_sla_sweep import (
    _build_in_app_payload,
    _enqueue_notifications,
    _select_breached,
)

_NOW = datetime(2026, 7, 25, 2, 45, 0, tzinfo=UTC)
_WINDOW = timedelta(hours=24)


def _row(
    *,
    project_id: uuid.UUID | None = None,
    status: str = "new",
    severity: str = "critical",
    due_delta: timedelta,
    due_on: date | None = None,
) -> tuple[uuid.UUID, str, str, datetime, date | None]:
    """Candidate row whose POLICY due date sits at ``_NOW + due_delta``.

    ``first_detected`` is derived backwards through ``vuln_sla_days`` so the
    test reads in due-date space (the window contract's native coordinates).

    ``due_on`` is the date somebody wrote down (ER28a). It defaults to None, so
    every case written before that existed still describes a finding whose only
    deadline is the policy's.
    """
    from core.config import vuln_sla_days

    days = vuln_sla_days(severity)
    assert days is not None, "use _row only for SLA-carrying severities"
    first_detected = _NOW + due_delta - timedelta(days=days)
    return (project_id or uuid.uuid4(), status, severity, first_detected, due_on)


# ---------------------------------------------------------------------------
# _select_breached — window boundaries (now - window <= due < now)
# ---------------------------------------------------------------------------


def test_due_inside_window_is_selected() -> None:
    pid = uuid.uuid4()
    rows = [_row(project_id=pid, due_delta=-timedelta(hours=1))]
    assert _select_breached(rows, now=_NOW, window=_WINDOW) == {pid: {"critical": 1}}


def test_due_exactly_at_window_start_is_included() -> None:
    """due == now - window belongs to THIS tick (closed lower bound)."""
    pid = uuid.uuid4()
    rows = [_row(project_id=pid, due_delta=-_WINDOW)]
    assert _select_breached(rows, now=_NOW, window=_WINDOW) == {pid: {"critical": 1}}


def test_due_exactly_at_now_is_excluded() -> None:
    """due == now has not crossed yet — tomorrow's tick owns it (open upper
    bound). Without this, a due date landing exactly on a tick would alert
    twice (this tick and the next one's closed lower bound)."""
    rows = [_row(due_delta=timedelta(0))]
    assert _select_breached(rows, now=_NOW, window=_WINDOW) == {}


def test_due_older_than_window_is_excluded() -> None:
    """An aged breach was already observed by an earlier tick — the window is
    the dedup, so it must stay silent now."""
    rows = [_row(due_delta=-_WINDOW - timedelta(seconds=1))]
    assert _select_breached(rows, now=_NOW, window=_WINDOW) == {}


def test_due_in_future_is_excluded() -> None:
    rows = [_row(due_delta=timedelta(hours=1))]
    assert _select_breached(rows, now=_NOW, window=_WINDOW) == {}


# ---------------------------------------------------------------------------
# _select_breached — status vocabulary (gate's closed set)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("closed", ["not_affected", "fixed", "false_positive"])
def test_closed_statuses_are_excluded(closed: str) -> None:
    rows = [_row(status=closed, due_delta=-timedelta(hours=1))]
    assert _select_breached(rows, now=_NOW, window=_WINDOW) == {}


@pytest.mark.parametrize("open_status", ["new", "analyzing", "exploitable", "suppressed"])
def test_open_statuses_are_included(open_status: str) -> None:
    """``suppressed`` is deliberately OPEN — the gate and upgrade engine count
    it as unresolved work, and so does the SLA sweep."""
    pid = uuid.uuid4()
    rows = [_row(project_id=pid, status=open_status, due_delta=-timedelta(hours=1))]
    assert _select_breached(rows, now=_NOW, window=_WINDOW) == {pid: {"critical": 1}}


def test_status_vocabulary_mirrors_gate() -> None:
    """Contract (hardening rule #2): the sweep imports the gate's closed set —
    assert the import identity so a future local copy cannot drift."""
    from services.policy_gate import _CLOSED_FINDING_STATUSES

    assert sweep_module._CLOSED_FINDING_STATUSES is _CLOSED_FINDING_STATUSES


# ---------------------------------------------------------------------------
# _select_breached — SLA-less severities + aggregation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("severity", ["info", "unknown"])
def test_no_sla_severities_are_excluded(severity: str) -> None:
    # No due date exists for these; hand a first_detected directly.
    rows = [(uuid.uuid4(), "new", severity, _NOW - timedelta(days=400), None)]
    assert _select_breached(rows, now=_NOW, window=_WINDOW) == {}


def test_aggregation_groups_by_project_and_severity() -> None:
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    rows = [
        _row(project_id=p1, severity="critical", due_delta=-timedelta(hours=2)),
        _row(project_id=p1, severity="critical", due_delta=-timedelta(hours=3)),
        _row(project_id=p1, severity="high", due_delta=-timedelta(hours=4)),
        _row(project_id=p2, severity="low", due_delta=-timedelta(hours=5)),
        # noise: outside window / closed / no SLA
        _row(project_id=p1, due_delta=-timedelta(days=3)),
        _row(project_id=p2, status="fixed", due_delta=-timedelta(hours=1)),
        (p2, "new", "info", _NOW - timedelta(days=400), None),
    ]
    assert _select_breached(rows, now=_NOW, window=_WINDOW) == {
        p1: {"critical": 2, "high": 1},
        p2: {"low": 1},
    }


def test_env_override_moves_the_due_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """The window check reads ``vuln_sla_days`` at call time (rule #11)."""
    monkeypatch.setenv("VULN_SLA_DAYS_HIGH", "10")
    pid = uuid.uuid4()
    # first_detected 10d + 1h ago → due (10d window) crossed 1h ago → in window.
    rows = [(pid, "new", "high", _NOW - timedelta(days=10, hours=1), None)]
    assert _select_breached(rows, now=_NOW, window=_WINDOW) == {pid: {"high": 1}}
    # Same row under the default 30d window: due is 20d away → not selected.
    monkeypatch.delenv("VULN_SLA_DAYS_HIGH")
    assert _select_breached(rows, now=_NOW, window=_WINDOW) == {}


# ---------------------------------------------------------------------------
# _build_in_app_payload
# ---------------------------------------------------------------------------


def test_payload_singular_and_link_free_title() -> None:
    title, body = _build_in_app_payload(
        project_name="portal", by_severity={"critical": 1}
    )
    assert title == "SLA breach: 1 finding overdue in portal"
    assert "critical 1" in body
    assert "24h" in body


def test_payload_plural_worst_first_breakdown() -> None:
    title, body = _build_in_app_payload(
        project_name="portal", by_severity={"low": 2, "critical": 1, "high": 3}
    )
    assert title == "SLA breach: 6 findings overdue in portal"
    assert "critical 1, high 3, low 2" in body


def test_payload_unexpected_severity_still_renders() -> None:
    _title, body = _build_in_app_payload(
        project_name="portal", by_severity={"critical": 1, "future_sev": 2}
    )
    assert "future_sev 2" in body


# ---------------------------------------------------------------------------
# Toggle + enqueue fan-out
# ---------------------------------------------------------------------------


def test_run_sweep_disabled_never_opens_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VULN_SLA_ALERTS_ENABLED", "false")

    def _explode():  # type: ignore[no-untyped-def]
        raise AssertionError("session must not open when the sweep is disabled")

    monkeypatch.setattr(sweep_module, "sync_session_scope", _explode)

    summary = sweep_module._run_sweep()
    assert summary["skipped"] is True
    assert summary["skipped_reason"] == "disabled"
    assert summary["notifications_enqueued"] == 0


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


def _descriptor(project_id: str | None = None) -> dict[str, Any]:
    return {
        "kind": "vuln_sla_breach",
        "context": {"project_name": "portal", "breach_count": "1"},
        "user_id": str(uuid.uuid4()),
        "title": "SLA breach: 1 finding overdue in portal",
        "body": "1 open finding crossed the remediation SLA in the last 24h.",
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
    assert args == (d["kind"], d["context"], [], [])  # channels=[] → in-app only
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
    assert enqueued == 1  # first failed, second went through — never raises
    assert len(attempts) == 2


# ---------------------------------------------------------------------------
# ER28a: a written-down deadline the sweep has to honour, and what happens
# when somebody moves it.
# ---------------------------------------------------------------------------


def _dated_row(
    *,
    project_id: uuid.UUID,
    due_on: date,
    severity: str = "info",
) -> tuple[uuid.UUID, str, str, datetime, date | None]:
    """A finding whose ONLY deadline is the one somebody wrote down.

    ``info`` carries no SLA window, so the policy contributes nothing and the
    row's deadline is exactly ``due_on``. That isolates the written date: any
    selection here is the written deadline being honoured and not the policy's
    leaking in.
    """
    return (project_id, "new", severity, _NOW - timedelta(days=400), due_on)


def test_a_written_deadline_is_swept_even_without_an_sla_window() -> None:
    """Before ER28a an info finding could never be overdue, so writing a date
    on one recorded an intention nothing acted on."""
    pid = uuid.uuid4()
    # The deadline expires at the END of the named day, so a date of
    # "yesterday" crossed at midnight, inside a 24h window.
    yesterday = (_NOW - timedelta(days=1)).date()
    assert _select_breached(
        [_dated_row(project_id=pid, due_on=yesterday)], now=_NOW, window=_WINDOW
    ) == {pid: {"info": 1}}


def test_moving_a_deadline_forward_alerts_again_when_it_is_missed_again() -> None:
    """The lifecycle, not a single moment.

    The sweep holds no "already told you" state on purpose: the persistent view
    is the overdue list, and the alert only says "these crossed in the last
    24h". So a deadline that is missed, moved out, and missed again alerts
    twice, and that is the intended reading rather than a duplicate: the
    commitment was remade and missed again.

    A single-moment test cannot see this. It needs the sequence.
    """
    pid = uuid.uuid4()
    first_due = (_NOW - timedelta(days=1)).date()

    # 1. Missed, and inside this tick's window: alerted.
    assert _select_breached(
        [_dated_row(project_id=pid, due_on=first_due)], now=_NOW, window=_WINDOW
    ) == {pid: {"info": 1}}

    # 2. Somebody moves the deadline out. At the same instant it is no longer
    #    overdue at all, so nothing is alerted.
    moved_to = (_NOW + timedelta(days=30)).date()
    assert (
        _select_breached(
            [_dated_row(project_id=pid, due_on=moved_to)], now=_NOW, window=_WINDOW
        )
        == {}
    )

    # 3. Time passes and the NEW deadline is missed: alerted again.
    later = datetime.combine(
        moved_to + timedelta(days=1), datetime.min.time(), tzinfo=UTC
    ) + timedelta(hours=1)
    assert _select_breached(
        [_dated_row(project_id=pid, due_on=moved_to)], now=later, window=_WINDOW
    ) == {pid: {"info": 1}}


def test_moving_a_deadline_out_does_not_alert_on_the_old_one_again() -> None:
    """The half that would be a real duplicate: the sweep must not keep
    reporting the deadline that was replaced."""
    pid = uuid.uuid4()
    moved_to = (_NOW + timedelta(days=30)).date()
    later = datetime.combine(
        moved_to + timedelta(days=1), datetime.min.time(), tzinfo=UTC
    ) + timedelta(hours=1)

    breached = _select_breached(
        [_dated_row(project_id=pid, due_on=moved_to)], now=later, window=_WINDOW
    )
    # Exactly one crossing is counted at the new date, not two.
    assert breached == {pid: {"info": 1}}


def test_an_earlier_written_date_beats_the_policy_in_the_sweep() -> None:
    """The sweep has to apply the same precedence rule as the list and the
    drawer. A critical finding is 7 days from detection by policy; a date
    written for yesterday is earlier, so the sweep sees yesterday."""
    pid = uuid.uuid4()
    yesterday = (_NOW - timedelta(days=1)).date()
    # Policy due is far in the future, so any selection is the written date.
    row = (
        pid,
        "new",
        "critical",
        _NOW,  # detected now → policy due in 7 days
        yesterday,
    )
    assert _select_breached([row], now=_NOW, window=_WINDOW) == {pid: {"critical": 1}}
