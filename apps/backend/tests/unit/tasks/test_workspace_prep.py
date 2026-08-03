"""Unit tests for the worker-boot workspace-permission hook.

Covers ``tasks.workspace_prep`` — the ``worker_ready`` handler that makes the
shared workspace volume writable by the non-root backend so SBOM-ingest cannot
500 with ``PermissionError`` on a fresh named volume.
"""

from __future__ import annotations

import os
import stat

import pytest

from tasks import workspace_prep


def test_ensure_workspace_writable_sets_sticky_world_writable(tmp_path, monkeypatch):
    """A fresh workspace root is created and left mode 1777 (the /tmp model)."""
    root = tmp_path / "workspace"
    monkeypatch.setattr(workspace_prep, "workspace_root", lambda: str(root))

    assert workspace_prep.ensure_workspace_writable() is True
    assert root.is_dir()
    mode = stat.S_IMODE(os.stat(root).st_mode)
    assert mode == 0o1777, f"expected 1777, got {oct(mode)}"


def test_ensure_workspace_writable_is_idempotent(tmp_path, monkeypatch):
    """Running twice on an existing dir is a safe no-op that keeps mode 1777."""
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(workspace_prep, "workspace_root", lambda: str(root))

    assert workspace_prep.ensure_workspace_writable() is True
    assert workspace_prep.ensure_workspace_writable() is True
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o1777


def test_ensure_workspace_writable_best_effort_on_oserror(tmp_path, monkeypatch):
    """A chmod failure is swallowed (returns False), never raised — boot-safe."""
    root = tmp_path / "workspace"
    monkeypatch.setattr(workspace_prep, "workspace_root", lambda: str(root))

    def _boom(*_a, **_k):
        raise PermissionError("operation not permitted")

    monkeypatch.setattr(workspace_prep.os, "chmod", _boom)

    # Must not raise, and must report failure.
    assert workspace_prep.ensure_workspace_writable() is False


def test_worker_ready_handler_delegates(monkeypatch):
    """The Celery signal handler calls ensure_workspace_writable exactly once."""
    calls: list[bool] = []
    monkeypatch.setattr(
        workspace_prep,
        "ensure_workspace_writable",
        lambda: calls.append(True) or True,
    )

    workspace_prep._on_worker_ready(sender=object())
    assert calls == [True]


def test_registered_in_celery_include_list():
    """The module must be in celery_app's include list or the worker never
    imports it and the worker_ready handler never registers (mirrors the
    trivy_db_bootstrap registration guard)."""
    from tasks.celery_app import _TASK_INCLUDES

    assert "tasks.workspace_prep" in _TASK_INCLUDES


@pytest.mark.parametrize("missing_parent", [True, False])
def test_creates_nested_parent(tmp_path, monkeypatch, missing_parent):
    """makedirs(exist_ok=True) creates a missing parent chain without error."""
    root = tmp_path / ("a/b/workspace" if missing_parent else "workspace")
    monkeypatch.setattr(workspace_prep, "workspace_root", lambda: str(root))
    assert workspace_prep.ensure_workspace_writable() is True
    assert root.is_dir()
