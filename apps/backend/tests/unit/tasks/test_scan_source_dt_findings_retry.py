"""
Unit tests for `_poll_dt_findings_with_retry` (chore PR #4 Part C).

Pinned behaviour:

* Returns the FIRST non-empty result without exhausting the schedule
  (matcher emits the full set at once, so subsequent polls would just
  re-fetch the same data).
* All-empty schedule returns `[]` rather than raising — terminal
  behaviour matches the pre-PR call (caller persists zero findings).
* Each attempt is wrapped by the breaker, so a `breaker.call` that
  raises propagates out of the helper (the surrounding pipeline maps
  it onto the breaker-open / DT-error terminal failure paths).
* `time.sleep` is invoked with the configured per-attempt delay; tests
  swap in a recorder so the assertions don't pay 60s wall-clock.
"""

from __future__ import annotations

from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeBreaker:
    """Pass-through breaker — runs the callable directly."""

    def call(self, fn):  # type: ignore[no-untyped-def]
        return fn()


class _ScriptedDTClient:
    """DT client whose `get_findings` returns a pre-scripted sequence."""

    def __init__(self, sequence: list[list[dict[str, Any]]]) -> None:
        self._sequence = list(sequence)
        self.calls: int = 0

    def get_findings(self, *, project_uuid: str) -> list[dict[str, Any]]:  # noqa: ARG002
        self.calls += 1
        if not self._sequence:
            return []
        return self._sequence.pop(0)


@pytest.fixture
def fast_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace `time.sleep` with a recorder so tests don't actually sleep."""
    seen: list[float] = []
    monkeypatch.setattr(
        "tasks.scan_source.time.sleep", lambda d: seen.append(float(d))
    )
    return seen


# ---------------------------------------------------------------------------
# Happy path — non-empty result short-circuits
# ---------------------------------------------------------------------------


def test_returns_findings_on_first_attempt_when_dt_is_warm(
    fast_sleep: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DT that's already finished matching returns findings on poll #1."""
    from tasks.scan_source import _poll_dt_findings_with_retry

    monkeypatch.setattr(
        "tasks.scan_source._DT_FINDINGS_POLL_DELAYS_SECONDS", (2, 4, 8, 16, 30)
    )
    client = _ScriptedDTClient([[{"vulnId": "CVE-2024-1"}]])

    findings = _poll_dt_findings_with_retry(
        dt_client=client,  # type: ignore[arg-type]
        breaker=_FakeBreaker(),  # type: ignore[arg-type]
        dt_project_uuid="project-1",
    )

    assert findings == [{"vulnId": "CVE-2024-1"}]
    # We slept once before the first poll — that's intentional (DT
    # matching is async). No further sleeps because we short-circuited.
    assert fast_sleep == [2.0]
    assert client.calls == 1


def test_returns_findings_on_third_attempt_after_two_empty_polls(
    fast_sleep: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Common production case: matcher needs ~10s; polls 1 + 2 are empty."""
    from tasks.scan_source import _poll_dt_findings_with_retry

    monkeypatch.setattr(
        "tasks.scan_source._DT_FINDINGS_POLL_DELAYS_SECONDS", (2, 4, 8, 16, 30)
    )
    client = _ScriptedDTClient(
        [
            [],  # poll 1 — empty
            [],  # poll 2 — empty
            [{"vulnId": "CVE-2024-2"}],  # poll 3 — match arrived
        ]
    )

    findings = _poll_dt_findings_with_retry(
        dt_client=client,  # type: ignore[arg-type]
        breaker=_FakeBreaker(),  # type: ignore[arg-type]
        dt_project_uuid="project-1",
    )

    assert findings == [{"vulnId": "CVE-2024-2"}]
    assert client.calls == 3
    # First three delays consumed; remaining (16, 30) skipped.
    assert fast_sleep == [2.0, 4.0, 8.0]


# ---------------------------------------------------------------------------
# All-empty schedule — return [] rather than raise
# ---------------------------------------------------------------------------


def test_returns_empty_when_every_attempt_returns_empty(
    fast_sleep: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repo with zero vulns (or DT mirror still warming up) → []."""
    from tasks.scan_source import _poll_dt_findings_with_retry

    monkeypatch.setattr(
        "tasks.scan_source._DT_FINDINGS_POLL_DELAYS_SECONDS", (1, 1, 1)
    )
    client = _ScriptedDTClient([[], [], []])

    findings = _poll_dt_findings_with_retry(
        dt_client=client,  # type: ignore[arg-type]
        breaker=_FakeBreaker(),  # type: ignore[arg-type]
        dt_project_uuid="project-1",
    )

    assert findings == []
    assert client.calls == 3
    assert fast_sleep == [1.0, 1.0, 1.0]


# ---------------------------------------------------------------------------
# Breaker integration — open breaker propagates out
# ---------------------------------------------------------------------------


class _OpenBreaker:
    """Breaker that immediately raises — simulates breaker OPEN."""

    def call(self, fn):  # type: ignore[no-untyped-def]
        from integrations.dt import DTBreakerOpen

        raise DTBreakerOpen("breaker open")


def test_breaker_open_propagates_to_caller(
    fast_sleep: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pipeline relies on DTBreakerOpen reaching the task body —
    don't swallow it inside the retry helper."""
    from integrations.dt import DTBreakerOpen
    from tasks.scan_source import _poll_dt_findings_with_retry

    monkeypatch.setattr(
        "tasks.scan_source._DT_FINDINGS_POLL_DELAYS_SECONDS", (1,)
    )

    with pytest.raises(DTBreakerOpen):
        _poll_dt_findings_with_retry(
            dt_client=_ScriptedDTClient([[]]),  # type: ignore[arg-type]
            breaker=_OpenBreaker(),  # type: ignore[arg-type]
            dt_project_uuid="project-1",
        )
    # The first sleep ran before the breaker call.
    assert fast_sleep == [1.0]


# ---------------------------------------------------------------------------
# No-delay schedule (test harness override) — still polls at least once
# ---------------------------------------------------------------------------


def test_zero_delay_schedule_runs_one_poll(
    fast_sleep: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The (0,) override the integration tests use must still issue a poll."""
    from tasks.scan_source import _poll_dt_findings_with_retry

    monkeypatch.setattr(
        "tasks.scan_source._DT_FINDINGS_POLL_DELAYS_SECONDS", (0,)
    )
    client = _ScriptedDTClient([[{"vulnId": "CVE-2024-3"}]])

    findings = _poll_dt_findings_with_retry(
        dt_client=client,  # type: ignore[arg-type]
        breaker=_FakeBreaker(),  # type: ignore[arg-type]
        dt_project_uuid="project-1",
    )

    assert findings == [{"vulnId": "CVE-2024-3"}]
    assert client.calls == 1
    assert fast_sleep == [0.0]
