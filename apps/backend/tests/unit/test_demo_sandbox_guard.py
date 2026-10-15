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

import asyncio
import uuid
from collections.abc import Iterator

import pytest

from models import Project
from services.demo_scope import DEMO_ORG_SLUG
from services.scan_service import (
    DEMO_SANDBOX_PROJECT_NAME,
    DEMO_SANDBOX_PROJECT_SLUG,
    DemoSandboxScanKindNotAllowed,
    ScanForbidden,
    _enforce_demo_sandbox_project,
    _enforce_demo_sandbox_scan_kind,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("DEMO_ALLOW_SANDBOX_SCANS", raising=False)
    yield


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
# H-2: sandbox-project guard (only the seeded sandbox is writable).
#
# The guard matches the seeded identity: slug ``demo-sandbox`` in a group of the
# demo organisation. The display name is free text and plays no part. The
# organisation lookup is stubbed here; tests/integration/
# test_demo_sandbox_identity_db.py runs it against Postgres.
# --------------------------------------------------------------------------- #


class _Result:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> str | None:
        return self._value


class _OrgSession:
    """Answers the guard's one query (the project's organisation slug)."""

    def __init__(self, org_slug: str | None) -> None:
        self.org_slug = org_slug
        self.calls = 0

    async def execute(self, _stmt: object) -> _Result:
        self.calls += 1
        return _Result(self.org_slug)


def _project(name: str, slug: str = "x") -> Project:
    # Unsaved ORM instance; the guard reads ``.slug`` and ``.team_id``.
    return Project(name=name, slug=slug, team_id=uuid.uuid4())


def _run(project: Project, org_slug: str | None) -> _OrgSession:
    session = _OrgSession(org_slug)
    asyncio.run(_enforce_demo_sandbox_project(session, project))  # type: ignore[arg-type]
    return session


@pytest.mark.parametrize(
    ("name", "slug", "org_slug"),
    [
        # Wrong slug, even with the sandbox's display name and organisation.
        (DEMO_SANDBOX_PROJECT_NAME, "portal-api", DEMO_ORG_SLUG),
        ("portal-api", "portal-api", DEMO_ORG_SLUG),
        (DEMO_SANDBOX_PROJECT_NAME, "demo-sandbox-2", DEMO_ORG_SLUG),
        # Right slug, wrong organisation: a project anyone could create elsewhere.
        (DEMO_SANDBOX_PROJECT_NAME, DEMO_SANDBOX_PROJECT_SLUG, "other-org"),
        (DEMO_SANDBOX_PROJECT_NAME, DEMO_SANDBOX_PROJECT_SLUG, None),
    ],
)
def test_h2_non_sandbox_project_blocked_when_carveout_on(
    monkeypatch: pytest.MonkeyPatch, name: str, slug: str, org_slug: str | None
) -> None:
    monkeypatch.setenv("DEMO_ALLOW_SANDBOX_SCANS", "true")
    with pytest.raises(ScanForbidden) as ei:
        _run(_project(name, slug), org_slug)
    assert ei.value.status_code == 403


@pytest.mark.parametrize("name", [DEMO_SANDBOX_PROJECT_NAME, "renamed by someone"])
def test_h2_seeded_sandbox_allowed_when_carveout_on(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """The display name does not matter; slug + organisation do."""
    monkeypatch.setenv("DEMO_ALLOW_SANDBOX_SCANS", "true")
    session = _run(_project(name, DEMO_SANDBOX_PROJECT_SLUG), DEMO_ORG_SLUG)
    assert session.calls == 1  # the organisation was actually checked


@pytest.mark.parametrize("slug", ["portal-api", DEMO_SANDBOX_PROJECT_SLUG])
def test_h2_noop_when_carveout_off(
    monkeypatch: pytest.MonkeyPatch, slug: str
) -> None:
    """Flag off: every project is writable exactly as before, no lookup made."""
    monkeypatch.delenv("DEMO_ALLOW_SANDBOX_SCANS", raising=False)
    session = _run(_project("portal-api", slug), "other-org")
    assert session.calls == 0


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
    # The guard matches slug + organisation; the seed must create exactly that.
    assert seed_demo._DEMO_SANDBOX_SLUG is DEMO_SANDBOX_PROJECT_SLUG
    assert seed_demo._DEMO_ORG_SLUG is DEMO_ORG_SLUG
