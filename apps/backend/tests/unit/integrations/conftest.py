"""
Shared fixtures for `integrations/` unit tests — Phase 2 PR #8.

Goals:
  - Force the scan-backend mock so adapters never invoke real cdxgen /
    scancode / Trivy binaries during unit tests (deterministic + CI-friendly).
  - Auto-pin ``WORKSPACE_HOST_PATH`` per test for the Trivy adapter's
    workspace boundary guard.

W6-#43a: the prior ``fakeredis_client`` / ``make_breaker`` / ``make_dt_client``
fixtures were deleted alongside the Dependency-Track integration. No test
in the suite still requests them.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _pin_workspace_root_to_tmp_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Auto-pin ``WORKSPACE_HOST_PATH`` to the per-test ``tmp_path``.

    The Trivy adapter's :func:`_ensure_inside_workspace` guard (PR #196 L1
    follow-up) rejects any ``output_dir`` / ``sbom_path`` that resolves
    outside ``WORKSPACE_HOST_PATH``. Without this autouse fixture every
    integration test that hands ``tmp_path`` (or any subdir) to ``run_trivy_*``
    would trip the guard because pytest's ``tmp_path`` lives under
    ``/private/var/folders/...`` (macOS) or ``/tmp/pytest-of-...`` (Linux),
    not under the default ``/tmp/trustedoss`` workspace root.

    Scoping the env var to ``tmp_path`` per test gives every test an isolated
    workspace boundary that matches its own scratch directory, so:

    - Tests that don't touch the guard see no behaviour change.
    - Tests that *do* exercise the guard (``test_trivy_security.py``) can
      build paths that are either inside or escape ``tmp_path`` to assert
      both branches.

    Read at call time per CLAUDE.md core rule #11, so this monkeypatch takes
    effect immediately on the next ``workspace_root()`` call without any
    cache to bust.
    """
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))


@pytest.fixture
def scan_backend_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the cdxgen / scancode / Trivy adapters to use mock fixture JSON.

    Tests that touch any subprocess-driven adapter must opt into this fixture
    so external tools are never spawned. The env var is the canonical knob
    (resolved at call time per CLAUDE.md core rule #11) so we set it via
    monkeypatch and let the adapters read it normally.
    """
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
