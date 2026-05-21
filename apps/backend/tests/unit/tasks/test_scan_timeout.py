"""
Unit tests for PR-A1 scan timeout handling (no DB required).

Pins, without Postgres or a Celery worker:

  - The two env-driven time-limit accessors resolve at call time (rule #11).
  - ``tasks.enqueue_scan`` passes ``soft_time_limit`` + ``time_limit`` to
    ``apply_async`` for BOTH scan kinds, reading the env at dispatch time.
  - The scan tasks catch ``SoftTimeLimitExceeded`` and route it to
    ``_record_terminal_failure`` with a "scan exceeded the time limit (NNNs)"
    message (NOT the generic "unexpected error" path), while the shared
    ``finally`` reclaims the workspace.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from celery.exceptions import SoftTimeLimitExceeded

# ---------------------------------------------------------------------------
# Config accessors (rule #11 — read at call time)
# ---------------------------------------------------------------------------


def test_scan_time_limit_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import (
        scan_hard_time_limit_seconds,
        scan_soft_time_limit_seconds,
    )

    monkeypatch.delenv("SCAN_SOFT_TIME_LIMIT_SECONDS", raising=False)
    monkeypatch.delenv("SCAN_HARD_TIME_LIMIT_SECONDS", raising=False)
    assert scan_soft_time_limit_seconds() == 3600
    assert scan_hard_time_limit_seconds() == 3900


def test_scan_time_limit_env_override_read_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import (
        scan_hard_time_limit_seconds,
        scan_soft_time_limit_seconds,
    )

    monkeypatch.setenv("SCAN_SOFT_TIME_LIMIT_SECONDS", "120")
    monkeypatch.setenv("SCAN_HARD_TIME_LIMIT_SECONDS", "180")
    assert scan_soft_time_limit_seconds() == 120
    assert scan_hard_time_limit_seconds() == 180
    # Mutate again — proves there is no module-level caching.
    monkeypatch.setenv("SCAN_SOFT_TIME_LIMIT_SECONDS", "7200")
    assert scan_soft_time_limit_seconds() == 7200


def test_hard_limit_default_exceeds_soft_limit() -> None:
    """The hard SIGKILL backstop must sit strictly above the soft limit."""
    from core.config import (
        scan_hard_time_limit_seconds,
        scan_soft_time_limit_seconds,
    )

    assert scan_hard_time_limit_seconds() > scan_soft_time_limit_seconds()


# ---------------------------------------------------------------------------
# M2: hard > soft invariant is enforced (clamped), not merely documented.
#
# An operator who sets hard <= soft (typo, or swapping the two env vars) would
# otherwise get SIGKILL at/before the soft-limit handler — killing cleanup,
# leaking the workspace, and pinning the scan in 'running' forever. The hard
# accessor clamps to ``soft + MIN_GRACE`` so the soft handler always has a
# window.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("soft", "hard"),
    [
        ("3600", "1800"),  # hard < soft (e.g. env vars swapped)
        ("3600", "3600"),  # hard == soft (no grace at all)
        ("3600", "3601"),  # hard barely above soft (under the grace floor)
        ("100", "0"),  # zero hard
        ("100", "-50"),  # negative hard
    ],
)
def test_hard_limit_clamped_above_soft(
    monkeypatch: pytest.MonkeyPatch, soft: str, hard: str
) -> None:
    from core.config import (
        SCAN_TIMEOUT_MIN_GRACE_SECONDS,
        scan_hard_time_limit_seconds,
        scan_soft_time_limit_seconds,
    )

    monkeypatch.setenv("SCAN_SOFT_TIME_LIMIT_SECONDS", soft)
    monkeypatch.setenv("SCAN_HARD_TIME_LIMIT_SECONDS", hard)

    effective_soft = scan_soft_time_limit_seconds()
    effective_hard = scan_hard_time_limit_seconds()

    # The invariant: the hard SIGKILL limit always leaves at least the grace
    # window above the soft limit, no matter what the operator typed.
    assert effective_hard >= effective_soft + SCAN_TIMEOUT_MIN_GRACE_SECONDS
    assert effective_hard > effective_soft


def test_hard_limit_honoured_when_already_above_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid operator value comfortably above the floor is left untouched."""
    from core.config import scan_hard_time_limit_seconds

    monkeypatch.setenv("SCAN_SOFT_TIME_LIMIT_SECONDS", "3600")
    monkeypatch.setenv("SCAN_HARD_TIME_LIMIT_SECONDS", "7200")
    # 7200 is well above 3600 + grace, so it is returned verbatim (no clamp).
    assert scan_hard_time_limit_seconds() == 7200


def test_negative_soft_still_yields_safe_default_hard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nonsensical negative soft must not produce a hard <= soft pairing.

    The default hard (3900) dominates the clamp floor here, but the assertion
    pins that the dispatch still gets a strictly-greater hard limit.
    """
    from core.config import (
        scan_hard_time_limit_seconds,
        scan_soft_time_limit_seconds,
    )

    monkeypatch.setenv("SCAN_SOFT_TIME_LIMIT_SECONDS", "-100")
    monkeypatch.delenv("SCAN_HARD_TIME_LIMIT_SECONDS", raising=False)
    assert scan_hard_time_limit_seconds() > scan_soft_time_limit_seconds()


# ---------------------------------------------------------------------------
# Dispatcher passes per-task limits (only scan tasks get them)
# ---------------------------------------------------------------------------


class _AsyncResult:
    id = "fake-task-id"


@pytest.mark.parametrize("kind", ["source", "container"])
def test_enqueue_scan_passes_time_limits_per_dispatch(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from tasks import enqueue_scan

    monkeypatch.setenv("SCAN_SOFT_TIME_LIMIT_SECONDS", "111")
    monkeypatch.setenv("SCAN_HARD_TIME_LIMIT_SECONDS", "222")

    captured: dict[str, Any] = {}

    def fake_apply_async(
        *, args: tuple[str, ...], soft_time_limit: int, time_limit: int
    ) -> _AsyncResult:
        captured["args"] = args
        captured["soft"] = soft_time_limit
        captured["hard"] = time_limit
        return _AsyncResult()

    target = (
        "tasks.scan_source.scan_source_task.apply_async"
        if kind == "source"
        else "tasks.scan_container.scan_container_task.apply_async"
    )
    monkeypatch.setattr(target, fake_apply_async)

    class _Scan:
        id = uuid.uuid4()

    scan = _Scan()
    scan.kind = kind  # type: ignore[attr-defined]

    task_id = enqueue_scan(scan)  # type: ignore[arg-type]
    assert task_id == "fake-task-id"
    assert captured["soft"] == 111
    assert captured["hard"] == 222
    assert captured["args"] == (str(scan.id),)


# ---------------------------------------------------------------------------
# SoftTimeLimitExceeded handling in scan_source / scan_container
# ---------------------------------------------------------------------------


def test_scan_source_softtimeout_marks_failed_and_cleans_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A soft-timeout: status='failed' with the timeout message + rmtree runs."""
    import tasks.scan_source as mod

    monkeypatch.setenv("SCAN_SOFT_TIME_LIMIT_SECONDS", "3600")
    monkeypatch.setattr(mod, "workspace_root", lambda: str(tmp_path))

    scan_uuid = uuid.uuid4()
    workspace = tmp_path / str(scan_uuid)
    workspace.mkdir(parents=True)
    (workspace / "leftover.txt").write_text("partial scan output\n")

    # First session: a non-succeeded scan that resets + marks running OK.
    class _FakeScan:
        status = "queued"
        project_id = uuid.uuid4()
        id = scan_uuid

    class _FakeProject:
        id = _FakeScan.project_id
        git_url = None

    def fake_get(model: Any, ident: Any) -> Any:
        # First call returns the scan; second (inside _run_pipeline path) the
        # project. We short-circuit the pipeline by raising the timeout.
        if model.__name__ == "Scan":
            return _FakeScan()
        return _FakeProject()

    from contextlib import contextmanager

    @contextmanager
    def fake_scope() -> Any:
        class _S:
            def get(self, m: Any, i: Any) -> Any:
                return fake_get(m, i)

            def execute(self, *a: Any, **k: Any) -> Any:
                return None

            def commit(self) -> None:
                pass

        yield _S()

    monkeypatch.setattr(mod, "sync_session_scope", fake_scope)
    monkeypatch.setattr(mod, "_reset_scan_for_rerun", lambda s, sc: None)
    monkeypatch.setattr(mod, "_mark_running", lambda s, sc: None)

    # Make the pipeline raise SoftTimeLimitExceeded as if Celery interrupted it.
    def boom(**_kwargs: Any) -> None:
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(mod, "_run_pipeline", boom)

    recorded: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(
        mod,
        "_record_terminal_failure",
        lambda su, msg: recorded.append((su, msg)),
    )

    # Direct invocation: with bind=True the task auto-binds `self`; the default
    # request stack supplies request.id=None (fine for this path).
    mod.scan_source_task.run(str(scan_uuid))

    assert recorded, "timeout should record a terminal failure"
    su, msg = recorded[0]
    assert su == scan_uuid
    assert "time limit" in msg
    assert "3600" in msg
    # finally: rmtree must have reclaimed the workspace tree.
    assert not workspace.exists()


def test_scan_container_softtimeout_marks_failed_and_cleans_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import tasks.scan_container as mod

    monkeypatch.setenv("SCAN_SOFT_TIME_LIMIT_SECONDS", "1800")
    monkeypatch.setattr(mod, "workspace_root", lambda: str(tmp_path))

    scan_uuid = uuid.uuid4()
    workspace = tmp_path / str(scan_uuid)
    workspace.mkdir(parents=True)
    (workspace / "trivy.json").write_text("{}")

    class _FakeScan:
        status = "queued"
        project_id = uuid.uuid4()
        scan_metadata = {"image_ref": "alpine:3.19"}
        id = scan_uuid

    class _FakeProject:
        id = _FakeScan.project_id

    from contextlib import contextmanager

    @contextmanager
    def fake_scope() -> Any:
        class _S:
            def get(self, m: Any, i: Any) -> Any:
                return _FakeScan() if m.__name__ == "Scan" else _FakeProject()

            def execute(self, *a: Any, **k: Any) -> Any:
                return None

            def commit(self) -> None:
                pass

        yield _S()

    monkeypatch.setattr(mod, "sync_session_scope", fake_scope)
    monkeypatch.setattr(mod, "_reset_for_rerun", lambda s, sc: None)
    monkeypatch.setattr(mod, "_mark_running", lambda s, sc: None)

    def boom(**_kwargs: Any) -> None:
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(mod, "_run_pipeline", boom)

    recorded: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(
        mod,
        "_record_terminal_failure",
        lambda su, msg: recorded.append((su, msg)),
    )

    mod.scan_container_task.run(str(scan_uuid))

    assert recorded
    su, msg = recorded[0]
    assert su == scan_uuid
    assert "time limit" in msg
    assert "1800" in msg
    assert not workspace.exists()
