# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
M1/M2 (concurrency-scaling plan) load-test delay wiring, ``scan_source_task``.

``core.config.scan_load_test_delay_seconds()`` (M1, PR #205) already carries
its own safety gate and is unit-tested in isolation
(``tests/unit/test_scan_load_test_delay_config.py``). What was still missing,
and what this file pins, is the WIRING inside the scan task: does the task
actually consult that accessor, actually skip the real cdxgen/scancode/Trivy
chain when it returns a positive value, and actually reuse the same
state-transition writers (``_set_stage`` / ``_mark_succeeded``) the real
pipeline uses?

Two layers, both without a database (mirrors the fake-session pattern already
used by ``tests/unit/tasks/test_scan_timeout.py``):

  1. Dispatch: does ``scan_source_task`` call ``_run_pipeline`` XOR
     ``_run_load_test_delay`` depending on ``scan_load_test_delay_seconds()``?
     This is the regression contract: the disabled (default / non-dev)
     case must take EXACTLY the pre-existing code path, unchanged.
  2. ``_run_load_test_delay`` itself: does it sleep the requested duration and
     drive the scan through the same ``_set_stage``/``_mark_succeeded`` calls
     the real pipeline's tail end uses?
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any

import pytest

import tasks.scan_source as mod


class _FakeScan:
    status = "queued"
    project_id = uuid.uuid4()
    scan_metadata: dict[str, Any] | None = None
    id: uuid.UUID
    # S8: scan_source_task snapshots scan.ref before the pipeline runs.
    ref: str | None = None


class _FakeProject:
    id = _FakeScan.project_id
    git_url = "https://example.test/repo.git"


def _fake_scope_factory(scan_uuid: uuid.UUID) -> Any:
    fake_scan = _FakeScan()
    fake_scan.id = scan_uuid

    @contextmanager
    def fake_scope() -> Any:
        class _S:
            def get(self, m: Any, i: Any) -> Any:  # noqa: ARG002
                return fake_scan if m.__name__ == "Scan" else _FakeProject()

            def execute(self, *a: Any, **k: Any) -> Any:
                return None

            def commit(self) -> None:
                pass

        yield _S()

    return fake_scope


@pytest.fixture(autouse=True)
def _wire_fake_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_reset_scan_for_rerun", lambda s, sc: None)
    monkeypatch.setattr(mod, "_mark_running", lambda s, sc: None)


# ---------------------------------------------------------------------------
# 1. Dispatch: regression contract for the disabled (default) path
# ---------------------------------------------------------------------------


def test_delay_disabled_by_default_takes_the_real_pipeline_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No env set ⇒ ``scan_load_test_delay_seconds()`` is 0.0 ⇒ the branch
    added for M1 must be a no-op: exactly the pre-existing ``_run_pipeline``
    call, with the same keyword arguments as before this change."""
    monkeypatch.delenv("SCAN_LOAD_TEST_DELAY_ENABLED", raising=False)
    monkeypatch.delenv("SCAN_LOAD_TEST_DELAY_SECONDS", raising=False)

    scan_uuid = uuid.uuid4()
    monkeypatch.setattr(mod, "sync_session_scope", _fake_scope_factory(scan_uuid))
    monkeypatch.setattr(mod, "workspace_root", lambda: "/tmp/trustedoss-test")

    pipeline_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(mod, "_run_pipeline", lambda **kw: pipeline_calls.append(kw))

    def boom(**_kw: Any) -> None:
        raise AssertionError("_run_load_test_delay must not run when delay is 0")

    monkeypatch.setattr(mod, "_run_load_test_delay", boom)

    mod.scan_source_task.run(str(scan_uuid))

    assert len(pipeline_calls) == 1
    call = pipeline_calls[0]
    assert call["scan_uuid"] == scan_uuid
    assert call["project_id"] == _FakeProject.id
    assert call["git_url"] == _FakeProject.git_url
    assert call["scan_metadata"] == {}


@pytest.mark.parametrize(
    ("enabled", "app_env"),
    [
        ("false", "dev"),  # flag off
        ("true", "staging"),  # flag on, wrong environment
        ("true", "prod"),  # flag on, prod (the case that matters most)
    ],
)
def test_delay_stays_disabled_for_every_non_activating_combination(
    monkeypatch: pytest.MonkeyPatch, enabled: str, app_env: str
) -> None:
    """Safety-first (task instructions): any combination that
    ``scan_load_test_delay_seconds()`` itself refuses must still resolve to
    the real pipeline here (the task performs no separate enable check of
    its own), so this pins that the wiring adds no second, looser gate."""
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_ENABLED", enabled)
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_SECONDS", "99")

    scan_uuid = uuid.uuid4()
    monkeypatch.setattr(mod, "sync_session_scope", _fake_scope_factory(scan_uuid))
    monkeypatch.setattr(mod, "workspace_root", lambda: "/tmp/trustedoss-test")

    pipeline_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(mod, "_run_pipeline", lambda **kw: pipeline_calls.append(kw))
    monkeypatch.setattr(
        mod,
        "_run_load_test_delay",
        lambda **_kw: (_ for _ in ()).throw(
            AssertionError("must not fabricate a result outside an activated dev delay")
        ),
    )

    mod.scan_source_task.run(str(scan_uuid))

    assert len(pipeline_calls) == 1


# ---------------------------------------------------------------------------
# 2. Dispatch: the activated path
# ---------------------------------------------------------------------------


def test_delay_enabled_in_dev_takes_the_delay_path_not_the_real_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_SECONDS", "2.5")

    scan_uuid = uuid.uuid4()
    monkeypatch.setattr(mod, "sync_session_scope", _fake_scope_factory(scan_uuid))
    monkeypatch.setattr(mod, "workspace_root", lambda: "/tmp/trustedoss-test")

    def boom(**_kw: Any) -> None:
        raise AssertionError("the real cdxgen/scancode/Trivy pipeline must not run")

    monkeypatch.setattr(mod, "_run_pipeline", boom)

    delay_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(mod, "_run_load_test_delay", lambda **kw: delay_calls.append(kw))

    mod.scan_source_task.run(str(scan_uuid))

    assert len(delay_calls) == 1
    assert delay_calls[0]["scan_uuid"] == scan_uuid
    assert delay_calls[0]["delay_seconds"] == 2.5


# ---------------------------------------------------------------------------
# 3. ``_run_load_test_delay`` itself: state-transition reuse
# ---------------------------------------------------------------------------


def test_run_load_test_delay_sleeps_then_reuses_the_real_terminal_writers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sleeps for the requested duration, then drives the scan through the
    SAME ``_set_stage``/``_mark_succeeded`` writers the real pipeline's tail
    (``bootstrap`` → ... → ``finalize`` → succeeded) uses, not a bespoke
    state machine."""
    scan_uuid = uuid.uuid4()
    calls: list[tuple[str, Any]] = []

    monkeypatch.setattr(
        mod, "time", type("T", (), {"sleep": staticmethod(lambda s: calls.append(("sleep", s)))})
    )
    monkeypatch.setattr(mod, "_set_stage", lambda su, stage: calls.append(("set_stage", stage)))
    monkeypatch.setattr(mod, "_mark_succeeded", lambda su: calls.append(("mark_succeeded", su)))

    mod._run_load_test_delay(scan_uuid=scan_uuid, delay_seconds=3.0)

    assert calls == [
        ("set_stage", "bootstrap"),
        ("sleep", 3.0),
        ("set_stage", "finalize"),
        ("mark_succeeded", scan_uuid),
    ]
