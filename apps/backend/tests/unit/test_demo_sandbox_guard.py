"""
Unit tests for the feat/demo-sandbox-scan service-layer write guards
(security review finding / H-2 / L-1 follow-up).

The middleware carve-out gates on method + path only, so two blast-radius issues
survive at the router: a ``kind:"container"`` body rides the ``/scans`` path
(H-1: container ``image_ref`` has no SSRF guard), and the path matches ANY
project id (H-2: a demo visitor could write to non-sandbox projects). The service
guards close both, narrowly, ONLY when the carve-out flag is on.

Security assertions are parametrized over the (flag state × input) intersection
per CLAUDE.md §2 hardening rule 1 — permission/scope denial must precede state.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from models import Project
from services.scan_service import (
    DEMO_SANDBOX_PROJECT_NAME,
    DemoSandboxScanKindNotAllowed,
    ScanForbidden,
    _enforce_demo_sandbox_project,
    _enforce_demo_sandbox_scan_kind,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("DEMO_ALLOW_SANDBOX_SCANS", raising=False)
    yield


def _project(name: str) -> Project:
    # Unsaved ORM instance — the guard only reads ``.name``; no session needed.
    return Project(name=name, slug="x")


# --------------------------------------------------------------------------- #
# H-1 — scan-kind guard (container / sbom kinds disabled in the sandbox).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", ["container", "sbom", "anything-else"])
def test_h1_non_source_kind_blocked_when_carveout_on(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setenv("DEMO_ALLOW_SANDBOX_SCANS", "true")
    with pytest.raises(DemoSandboxScanKindNotAllowed) as ei:
        _enforce_demo_sandbox_scan_kind(kind)
    assert ei.value.status_code == 422


def test_h1_source_kind_allowed_when_carveout_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEMO_ALLOW_SANDBOX_SCANS", "true")
    # No raise — source scans are the one permitted kind.
    _enforce_demo_sandbox_scan_kind("source")


@pytest.mark.parametrize("kind", ["source", "container", "sbom"])
def test_h1_noop_when_carveout_off(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Flag off (default): the guard is a no-op — non-demo container scans work."""
    monkeypatch.delenv("DEMO_ALLOW_SANDBOX_SCANS", raising=False)
    _enforce_demo_sandbox_scan_kind(kind)  # must not raise


# --------------------------------------------------------------------------- #
# H-2 — sandbox-project guard (only the seeded "Demo Sandbox" is writable).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["portal-api", "scan-pipeline", "Demo Sandbox "])
def test_h2_non_sandbox_project_blocked_when_carveout_on(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Any project whose name is not EXACTLY the sandbox is 403'd (note the
    trailing-space variant — the match is exact, not a prefix / trim)."""
    monkeypatch.setenv("DEMO_ALLOW_SANDBOX_SCANS", "true")
    with pytest.raises(ScanForbidden) as ei:
        _enforce_demo_sandbox_project(_project(name))
    assert ei.value.status_code == 403


def test_h2_sandbox_project_allowed_when_carveout_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEMO_ALLOW_SANDBOX_SCANS", "true")
    _enforce_demo_sandbox_project(_project(DEMO_SANDBOX_PROJECT_NAME))  # no raise


@pytest.mark.parametrize("name", ["portal-api", "Demo Sandbox"])
def test_h2_noop_when_carveout_off(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Flag off: every project is writable exactly as before — no regression."""
    monkeypatch.delenv("DEMO_ALLOW_SANDBOX_SCANS", raising=False)
    _enforce_demo_sandbox_project(_project(name))  # must not raise


# --------------------------------------------------------------------------- #
# Shared-vocabulary contract (hardening rule 2): the guard and the seed must
# agree on the sandbox project name so the seed can never create a project the
# guard would reject.
# --------------------------------------------------------------------------- #


def test_sandbox_name_shared_with_seed() -> None:
    from scripts import seed_demo

    # The seed imports the SAME constant for the project's ``name`` — assert it
    # is the shared object, not an independently drifting literal.
    assert seed_demo.DEMO_SANDBOX_PROJECT_NAME is DEMO_SANDBOX_PROJECT_NAME
    assert DEMO_SANDBOX_PROJECT_NAME == "Demo Sandbox"
