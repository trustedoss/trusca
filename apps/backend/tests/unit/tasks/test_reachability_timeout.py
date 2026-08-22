"""
Unit tests for S2 (concurrency-scaling-plan-2026-08-22.md §3.2): the
reachability follow-up task must be time-boxed like the other scan tasks.

Background: ``tasks.enqueue_reachability`` dispatched ``scan_reachability_task``
with no ``soft_time_limit`` / ``time_limit`` at all (unlike ``enqueue_scan``,
which passes both to every scan-kind branch; see ``tasks/__init__.py``). With
no global ``task_time_limit`` either (``celery_app.py``), the task could pin a
worker slot indefinitely if something outside the already-bounded govulncheck
subprocess call (extraction, a DB stall) hung.

Design choice (documented here, and in the PR): reachability gets its OWN
soft/hard limits, not the scan pipeline's (3600s/3900s), because it is a
best-effort enrichment layered onto an ALREADY-succeeded scan, and its
dominant cost (the govulncheck subprocess) already self-bounds at 600s
(``GOVULNCHECK_TIMEOUT_SECONDS`` default, core/config.py). Reusing the scan's
65-minute ceiling would let a hung non-govulncheck step (extraction, a DB
stall) occupy a worker slot far longer than this task's own budget ever
needs. The chosen default (900s soft / 1200s hard) leaves 300s of headroom
above the 600s subprocess bound for extraction + DB writes, mirroring the
300s margin used for the S1 broker visibility timeout.

Timeout HANDLING intentionally differs from scan_source / scan_container /
ingest_sbom too: those mark the SCAN itself failed on a soft-limit hit,
because the timed-out task IS the scan. Reachability's SoftTimeLimitExceeded
handler (already in tasks/scan_reachability.py, previously dead code because
no soft_time_limit was ever passed) does NOT touch scan status; the parent
scan already reached a terminal "succeeded" state before reachability was
even dispatched, so flipping it to "failed" because a best-effort enrichment
timed out would be actively wrong. It logs a WARNING and leaves the
reachability columns at their prior value (NULL if never analysed), the same
degrade-gracefully contract every other reachability failure path already
uses (see the module docstring's "Idempotency / safety" section).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from celery.exceptions import SoftTimeLimitExceeded

# ---------------------------------------------------------------------------
# Config accessors (rule #11, read at call time)
# ---------------------------------------------------------------------------


def test_reachability_time_limit_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import (
        reachability_hard_time_limit_seconds,
        reachability_soft_time_limit_seconds,
    )

    monkeypatch.delenv("REACHABILITY_SOFT_TIME_LIMIT_SECONDS", raising=False)
    monkeypatch.delenv("REACHABILITY_HARD_TIME_LIMIT_SECONDS", raising=False)
    assert reachability_soft_time_limit_seconds() == 900
    assert reachability_hard_time_limit_seconds() == 1200


def test_reachability_time_limit_env_override_read_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import (
        reachability_hard_time_limit_seconds,
        reachability_soft_time_limit_seconds,
    )

    # Hard is set comfortably above soft + the grace floor so this exercises
    # plain env reads, not the clamp (that has its own parametrized test below).
    monkeypatch.setenv("REACHABILITY_SOFT_TIME_LIMIT_SECONDS", "60")
    monkeypatch.setenv("REACHABILITY_HARD_TIME_LIMIT_SECONDS", "200")
    assert reachability_soft_time_limit_seconds() == 60
    assert reachability_hard_time_limit_seconds() == 200
    # Mutate again; proves there is no module-level caching.
    monkeypatch.setenv("REACHABILITY_SOFT_TIME_LIMIT_SECONDS", "70")
    assert reachability_soft_time_limit_seconds() == 70


def test_reachability_hard_limit_default_exceeds_soft_limit() -> None:
    from core.config import (
        reachability_hard_time_limit_seconds,
        reachability_soft_time_limit_seconds,
    )

    assert reachability_hard_time_limit_seconds() > reachability_soft_time_limit_seconds()


def test_reachability_soft_limit_exceeds_govulncheck_subprocess_timeout() -> None:
    """Regression contract (plan §4, S2 row): "상한이 실제 소요보다 크다".

    ``govulncheck_timeout_seconds()`` bounds the dominant real-world cost of a
    reachability run (the analyser subprocess). The outer Celery soft limit
    must sit above it with headroom for extraction + DB writes, or a normal
    run that legitimately takes close to the subprocess bound would get cut
    off by the outer limit before the subprocess timeout even has a chance to
    degrade gracefully on its own.
    """
    from core.config import (
        govulncheck_timeout_seconds,
        reachability_soft_time_limit_seconds,
    )

    assert reachability_soft_time_limit_seconds() > govulncheck_timeout_seconds()


@pytest.mark.parametrize(
    ("soft", "hard"),
    [
        ("900", "300"),  # hard < soft (e.g. env vars swapped)
        ("900", "900"),  # hard == soft (no grace at all)
        ("900", "901"),  # hard barely above soft (under the grace floor)
        ("100", "0"),  # zero hard
        ("100", "-50"),  # negative hard
    ],
)
def test_reachability_hard_limit_clamped_above_soft(
    monkeypatch: pytest.MonkeyPatch, soft: str, hard: str
) -> None:
    from core.config import (
        SCAN_TIMEOUT_MIN_GRACE_SECONDS,
        reachability_hard_time_limit_seconds,
        reachability_soft_time_limit_seconds,
    )

    monkeypatch.setenv("REACHABILITY_SOFT_TIME_LIMIT_SECONDS", soft)
    monkeypatch.setenv("REACHABILITY_HARD_TIME_LIMIT_SECONDS", hard)

    effective_soft = reachability_soft_time_limit_seconds()
    effective_hard = reachability_hard_time_limit_seconds()

    assert effective_hard >= effective_soft + SCAN_TIMEOUT_MIN_GRACE_SECONDS
    assert effective_hard > effective_soft


# ---------------------------------------------------------------------------
# Dispatcher passes the reachability-specific limits (not the scan ones)
# ---------------------------------------------------------------------------


class _AsyncResult:
    id = "fake-reachability-task-id"


def test_enqueue_reachability_passes_time_limits_per_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tasks import enqueue_reachability

    monkeypatch.setenv("REACHABILITY_ENABLED", "true")
    monkeypatch.setenv("REACHABILITY_SOFT_TIME_LIMIT_SECONDS", "111")
    monkeypatch.setenv("REACHABILITY_HARD_TIME_LIMIT_SECONDS", "222")

    captured: dict[str, Any] = {}

    def fake_apply_async(
        *, args: tuple[str, ...], soft_time_limit: int, time_limit: int
    ) -> _AsyncResult:
        captured["args"] = args
        captured["soft"] = soft_time_limit
        captured["hard"] = time_limit
        return _AsyncResult()

    monkeypatch.setattr(
        "tasks.scan_reachability.scan_reachability_task.apply_async",
        fake_apply_async,
    )

    scan_id = str(uuid.uuid4())
    task_id = enqueue_reachability(scan_id)

    assert task_id == "fake-reachability-task-id"
    assert captured["soft"] == 111
    assert captured["hard"] == 222
    assert captured["args"] == (scan_id,)


def test_enqueue_reachability_returns_none_when_disabled_no_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unaffected by S2: the REACHABILITY_ENABLED=false short-circuit must
    still skip dispatch entirely (no time-limit read, no apply_async call)."""
    from tasks import enqueue_reachability

    monkeypatch.setenv("REACHABILITY_ENABLED", "false")

    called = {"apply_async": False}

    def _fake_apply_async(**_k: object) -> _AsyncResult:
        called["apply_async"] = True
        return _AsyncResult()

    monkeypatch.setattr(
        "tasks.scan_reachability.scan_reachability_task.apply_async",
        _fake_apply_async,
    )

    result = enqueue_reachability(str(uuid.uuid4()))

    assert result is None
    assert called["apply_async"] is False


# ---------------------------------------------------------------------------
# SoftTimeLimitExceeded handling: scan status is NOT touched (see module
# docstring above for why this deliberately differs from scan_source /
# scan_container's "mark the scan failed" behavior).
# ---------------------------------------------------------------------------


def test_reachability_softtimeout_does_not_raise_and_cleans_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import tasks.scan_reachability as mod

    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    scan_id = uuid.uuid4()

    def _boom(*, scan_uuid: uuid.UUID, workspace: Path) -> None:
        # Materialise the workspace tree BEFORE raising, as _run's real
        # extraction step would, so we can prove `finally` reclaims it.
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "partial-extract.txt").write_text("x", encoding="utf-8")
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(mod, "_run", _boom)

    warnings: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(mod.log, "warning", lambda event, **kw: warnings.append((event, kw)))

    # Must complete without raising; a timed-out reachability run is
    # best-effort and must never fail the (already-succeeded) parent scan or
    # retry-storm the queue.
    mod.scan_reachability_task.apply(args=[str(scan_id)])

    assert not (tmp_path / f"reach-{scan_id}").exists()
    assert any(event == "reachability_timed_out" for event, _kw in warnings)
