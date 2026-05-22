"""Scan-pipeline chaos invariant (Tier K — failure injection).

The 5–60 min scan pipeline calls out to cdxgen / scancode / DT / disk; ANY of
them can blow up mid-run. The guarantee that keeps the service stable is: a stage
crash terminates the scan as ``failed`` (never stuck "running" forever, never a
Celery retry-storm) AND the workspace is always reclaimed (no orphan tree filling
the disk). ``test_scan_timeout`` pins the SoftTimeLimit path; this pins the
catch-all ``except Exception`` path for arbitrary crashes — including an OSError,
which is what a disk-full write surfaces as.

Mirrors the timeout test's fake-session harness (no DB / no real tools).
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("cdxgen subprocess crashed"),
        ValueError("scancode produced unparseable output"),
        OSError("[Errno 28] No space left on device"),  # disk-full chaos
    ],
)
def test_stage_crash_marks_failed_and_reclaims_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, exc: Exception
) -> None:
    import tasks.scan_source as mod

    monkeypatch.setattr(mod, "workspace_root", lambda: str(tmp_path))

    scan_uuid = uuid.uuid4()
    workspace = tmp_path / str(scan_uuid)
    workspace.mkdir(parents=True)
    (workspace / "partial.json").write_text("half-written scan output\n")

    class _FakeScan:
        status = "queued"
        project_id = uuid.uuid4()
        scan_metadata = None
        id = scan_uuid

    class _FakeProject:
        id = _FakeScan.project_id
        git_url = None

    @contextmanager
    def fake_scope() -> Any:
        class _S:
            def get(self, model: Any, _ident: Any) -> Any:
                return _FakeScan() if model.__name__ == "Scan" else _FakeProject()

            def execute(self, *_a: Any, **_k: Any) -> Any:
                return None

            def commit(self) -> None:
                pass

        yield _S()

    monkeypatch.setattr(mod, "sync_session_scope", fake_scope)
    monkeypatch.setattr(mod, "_reset_scan_for_rerun", lambda s, sc: None)
    monkeypatch.setattr(mod, "_mark_running", lambda s, sc: None)

    def boom(**_kwargs: Any) -> None:
        raise exc

    monkeypatch.setattr(mod, "_run_pipeline", boom)

    recorded: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(
        mod, "_record_terminal_failure", lambda su, msg: recorded.append((su, msg))
    )

    # Must NOT propagate (a re-raise would have Celery retry the scan forever).
    mod.scan_source_task.run(str(scan_uuid))

    assert recorded, "a stage crash must record a terminal failure (status=failed)"
    su, msg = recorded[0]
    assert su == scan_uuid
    assert "unexpected error" in msg  # the catch-all branch's message
    # finally: the workspace tree is always reclaimed — no orphan on disk.
    assert not workspace.exists()
