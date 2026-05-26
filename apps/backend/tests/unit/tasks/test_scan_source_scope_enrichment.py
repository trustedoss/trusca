"""Unit tests — npm scope enrichment in ``_persist_components`` (W4-D, 2026-05-27).

cdxgen 12.3.3 does not emit ``scope`` for npm components, so without
intervention the Components tab's USAGE column is dash for every npm row. The
fix adds a per-component lookup against the parsed npm lockfile when cdxgen
left the scope NULL. These tests pin:

  * cdxgen-supplied scope (e.g. Maven POMs) wins over the lockfile;
  * an absent cdxgen scope falls back to the lockfile *only* for npm purls;
  * non-npm components (Maven, PyPI, etc.) never consult the lockfile;
  * no lockfile available → behaviour unchanged (NULL stays NULL);
  * the lockfile loader is called exactly once per scan (not per component).

Fake-session unit tests (no DB, no subprocess).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from integrations.npm_lockfile import NpmLockfileData
from models import ScanComponent


class _FakeComponent:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class _FakeComponentVersion:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class _FakeSession:
    """Records every ``session.add`` call so the assertions can inspect rows."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)


def _scan_components(session: _FakeSession) -> list[ScanComponent]:
    return [r for r in session.added if isinstance(r, ScanComponent)]


@pytest.fixture
def patched_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub component / license helpers so ``_persist_components`` runs
    in-memory against the fake session."""
    monkeypatch.setattr(
        "tasks.scan_source._get_or_create_component",
        lambda session, *, purl, name, package_type: _FakeComponent(),
    )
    monkeypatch.setattr(
        "tasks.scan_source._get_or_create_component_version",
        lambda session, *, component, version, purl_with_version: _FakeComponentVersion(),
    )
    monkeypatch.setattr(
        "tasks.scan_source._persist_component_licenses",
        lambda session, *, scan_uuid, component_version_id, cdxgen_component, purl: None,
    )
    def _no_graph(
        session: Any,
        *,
        scan_uuid: uuid.UUID,
        sbom: dict[str, Any],
        ref_to_cv_id: dict[str, uuid.UUID],
        ref_to_scan_component: dict[str, Any],
        npm_lock: Any = None,
    ) -> None:
        return None

    monkeypatch.setattr("tasks.scan_source._persist_dependency_graph", _no_graph)


# ---------------------------------------------------------------------------
# Scope enrichment
# ---------------------------------------------------------------------------


def test_cdxgen_scope_wins_over_lockfile(
    tmp_path: Path,
    patched_helpers: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cdxgen-supplied scope (e.g. Maven POM ``<scope>``) is NOT overridden
    by the lockfile. Maven emits scope reliably; the lockfile is npm-only."""
    from tasks.scan_source import _persist_components

    # cdxgen says compile (Maven); lockfile would (wrongly) say dev. cdxgen wins.
    monkeypatch.setattr(
        "tasks.scan_source.read_lockfile",
        lambda src: NpmLockfileData(
            scope_by_purl={"pkg:maven/org.apache/commons@1": "dev"},
            adjacency={},
        ),
    )
    session = _FakeSession()
    sbom = {
        "components": [
            {
                "purl": "pkg:maven/org.apache/commons@1",
                "bom-ref": "pkg:maven/org.apache/commons@1",
                "name": "commons",
                "version": "1",
                "scope": "compile",
            }
        ]
    }
    _persist_components(session, scan_uuid=uuid.uuid4(), sbom=sbom, source_dir=tmp_path)
    scs = _scan_components(session)
    assert len(scs) == 1
    assert scs[0].dependency_scope == "compile"


def test_npm_component_uses_lockfile_scope_when_cdxgen_missing(
    tmp_path: Path,
    patched_helpers: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cdxgen left scope NULL for an npm component → the lockfile fills it.

    This is the dominant 2026-05-26 P3 #12 gap: cdxgen 12.3.3 emits npm
    components with no ``scope`` field, leaving the UI USAGE column at dash
    for every npm row. The lockfile-derived scope closes that gap."""
    from tasks.scan_source import _persist_components

    monkeypatch.setattr(
        "tasks.scan_source.read_lockfile",
        lambda src: NpmLockfileData(
            scope_by_purl={
                "pkg:npm/express@4.18.2": "required",
                "pkg:npm/jest@29.7.0": "dev",
            },
            adjacency={},
        ),
    )
    session = _FakeSession()
    sbom = {
        "components": [
            {
                "purl": "pkg:npm/express@4.18.2",
                "bom-ref": "pkg:npm/express@4.18.2",
                "name": "express",
                "version": "4.18.2",
                # NO ``scope`` field — cdxgen's npm behaviour.
            },
            {
                "purl": "pkg:npm/jest@29.7.0",
                "bom-ref": "pkg:npm/jest@29.7.0",
                "name": "jest",
                "version": "29.7.0",
            },
        ]
    }
    _persist_components(session, scan_uuid=uuid.uuid4(), sbom=sbom, source_dir=tmp_path)
    scs = _scan_components(session)
    scopes = {sc.dependency_scope for sc in scs}
    assert scopes == {"required", "dev"}


def test_non_npm_component_never_consults_lockfile(
    tmp_path: Path,
    patched_helpers: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PyPI / Maven / Go purl missing scope stays NULL — the lockfile is
    npm-only. A non-npm purl that *happens* to appear in the lockfile (e.g. a
    spoofed entry) is ignored.

    This protects the trust boundary: the lockfile is attacker-controllable
    but its enrichment scope is bounded to npm purls."""
    from tasks.scan_source import _persist_components

    monkeypatch.setattr(
        "tasks.scan_source.read_lockfile",
        lambda src: NpmLockfileData(
            scope_by_purl={
                # Spoofed: a hostile lockfile claims a PyPI package is "dev".
                "pkg:pypi/django@4.0": "dev",
            },
            adjacency={},
        ),
    )
    session = _FakeSession()
    sbom = {
        "components": [
            {
                "purl": "pkg:pypi/django@4.0",
                "bom-ref": "pkg:pypi/django@4.0",
                "name": "django",
                "version": "4.0",
            },
        ]
    }
    _persist_components(session, scan_uuid=uuid.uuid4(), sbom=sbom, source_dir=tmp_path)
    scs = _scan_components(session)
    assert len(scs) == 1
    assert scs[0].dependency_scope is None  # NOT "dev" — type-bound


def test_no_lockfile_means_scope_stays_null(
    tmp_path: Path,
    patched_helpers: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No package-lock.json on disk → ``read_lockfile`` returns None → the
    behaviour is identical to pre-W4-D: cdxgen scope or NULL."""
    from tasks.scan_source import _persist_components

    monkeypatch.setattr("tasks.scan_source.read_lockfile", lambda src: None)
    session = _FakeSession()
    sbom = {
        "components": [
            {
                "purl": "pkg:npm/express@4.18.2",
                "bom-ref": "pkg:npm/express@4.18.2",
                "name": "express",
                "version": "4.18.2",
            },
        ]
    }
    _persist_components(session, scan_uuid=uuid.uuid4(), sbom=sbom, source_dir=tmp_path)
    scs = _scan_components(session)
    assert scs[0].dependency_scope is None


def test_source_dir_none_skips_lockfile_load(
    patched_helpers: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the caller does not pass ``source_dir`` (legacy call sites / tests),
    the lockfile loader is NOT called. Behaviour is identical to pre-W4-D."""
    from tasks.scan_source import _persist_components

    calls: list[Any] = []

    def _spy(source_dir: Path) -> None:
        calls.append(source_dir)
        return None

    monkeypatch.setattr("tasks.scan_source.read_lockfile", _spy)
    session = _FakeSession()
    sbom = {
        "components": [
            {
                "purl": "pkg:npm/x@1",
                "bom-ref": "pkg:npm/x@1",
                "name": "x",
                "version": "1",
            }
        ]
    }
    _persist_components(session, scan_uuid=uuid.uuid4(), sbom=sbom)
    assert calls == []  # loader never invoked when source_dir is None


def test_lockfile_loaded_exactly_once_per_persist_call(
    tmp_path: Path,
    patched_helpers: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lockfile is loaded once at the top of ``_persist_components`` — NOT
    re-loaded per component. A 1000-component scan must not re-parse the
    lockfile 1000 times."""
    from tasks.scan_source import _persist_components

    load_count = 0

    def _counting_loader(source_dir: Path) -> NpmLockfileData | None:
        nonlocal load_count
        load_count += 1
        return NpmLockfileData(scope_by_purl={}, adjacency={})

    monkeypatch.setattr("tasks.scan_source.read_lockfile", _counting_loader)
    session = _FakeSession()
    # 50 npm components → would be 50 reads if cached per-call.
    components = [
        {
            "purl": f"pkg:npm/p{i}@1",
            "bom-ref": f"pkg:npm/p{i}@1",
            "name": f"p{i}",
            "version": "1",
        }
        for i in range(50)
    ]
    _persist_components(
        session, scan_uuid=uuid.uuid4(), sbom={"components": components}, source_dir=tmp_path
    )
    assert load_count == 1  # parsed once, used for every lookup


def test_empty_string_cdxgen_scope_falls_back_to_lockfile(
    tmp_path: Path,
    patched_helpers: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cdxgen scope that is the empty string is treated as missing.

    Some cdxgen versions emit ``scope: ""`` for unresolved entries; an empty
    string is not a useful classification and the lockfile should fill in."""
    from tasks.scan_source import _persist_components

    monkeypatch.setattr(
        "tasks.scan_source.read_lockfile",
        lambda src: NpmLockfileData(
            scope_by_purl={"pkg:npm/express@4.18.2": "required"},
            adjacency={},
        ),
    )
    session = _FakeSession()
    sbom = {
        "components": [
            {
                "purl": "pkg:npm/express@4.18.2",
                "bom-ref": "pkg:npm/express@4.18.2",
                "name": "express",
                "version": "4.18.2",
                "scope": "",  # empty string from cdxgen
            }
        ]
    }
    _persist_components(session, scan_uuid=uuid.uuid4(), sbom=sbom, source_dir=tmp_path)
    scs = _scan_components(session)
    assert scs[0].dependency_scope == "required"
