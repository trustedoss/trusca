"""Unit tests — EOL stamping inside ``persist_sbom_components`` (Phase M).

The SBOM fixture is the BomLens original (``tests/fixtures/eol/
eol-components.json``) and the dataset is its deterministic companion
(``eol-data.json``, far past/future dates) — the exact pair the BomLens
post-process e2e verified. Expected verdicts:

  spring-boot-starter-web@3.2.0   → eol       (dated 2020-01-01, past)
  spring-boot-actuator@3.3.1      → supported (dated 2099-12-31, future)
  spring-boot-experimental@9.9.0  → unknown   (cycle unlisted)
  express@4.18.2                  → supported (boolean false)
  django@4.2.1                    → eol       (dated past)
  lodash@4.17.21                  → untouched (unmapped — every column NULL)
  express-session@1.17.3          → untouched (must NOT match express rule)

Also pinned: rerun idempotency (changed-value guard), EOL_ENABLED=false
skips, corrupt dataset degrades to no stamping, ingest-path coverage is
automatic (same persist function).
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "eol"
SBOM_PATH = FIXTURES / "eol-components.json"
DATA_PATH = FIXTURES / "eol-data.json"


class _FakeComponent:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class _FakeComponentVersion:
    def __init__(self, purl: str) -> None:
        self.id = uuid.uuid4()
        self.purl_with_version = purl
        self.eol_state: str | None = None
        self.eol_product: str | None = None
        self.eol_cycle: str | None = None
        self.eol_date: date | None = None
        self.eol_source: str | None = None
        self.eol_evaluated_at = None


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)


@pytest.fixture
def cv_registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, _FakeComponentVersion]:
    """Stub persist helpers; capture ComponentVersion fakes keyed by purl.

    Reuses the same fake CV for a repeated purl — mirroring the real
    ``_get_or_create_component_version`` (shared catalog row), which is what
    makes the rerun-idempotency assertion meaningful.
    """
    registry: dict[str, _FakeComponentVersion] = {}

    def _get_cv(
        session: Any, *, component: Any, version: str, purl_with_version: str
    ) -> _FakeComponentVersion:
        if purl_with_version not in registry:
            registry[purl_with_version] = _FakeComponentVersion(purl_with_version)
        return registry[purl_with_version]

    monkeypatch.setattr(
        "tasks.scan_source._get_or_create_component",
        lambda session, *, purl, name, package_type: _FakeComponent(),
    )
    monkeypatch.setattr("tasks.scan_source._get_or_create_component_version", _get_cv)
    monkeypatch.setattr(
        "tasks.scan_source._persist_component_licenses",
        lambda session, *, scan_uuid, component_version_id, cdxgen_component, purl: None,
    )
    monkeypatch.setattr(
        "tasks.scan_source._persist_dependency_graph",
        lambda session, **kwargs: None,
    )
    return registry


@pytest.fixture
def fixture_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOL_SNAPSHOT_PATH", str(DATA_PATH))


def _sbom() -> dict[str, Any]:
    return json.loads(SBOM_PATH.read_text(encoding="utf-8"))


def _persist(session: _FakeSession) -> None:
    from tasks.scan_source import persist_sbom_components

    persist_sbom_components(session, scan_uuid=uuid.uuid4(), sbom=_sbom())


def _by_name(registry: dict[str, _FakeComponentVersion]) -> dict[str, _FakeComponentVersion]:
    return {purl.split("/")[-1].split("@")[0]: cv for purl, cv in registry.items()}


def test_fixture_sbom_stamps_the_expected_verdict_matrix(
    cv_registry: dict[str, _FakeComponentVersion], fixture_dataset: None
) -> None:
    _persist(_FakeSession())
    rows = _by_name(cv_registry)

    assert rows["spring-boot-starter-web"].eol_state == "eol"
    assert rows["spring-boot-starter-web"].eol_date == date(2020, 1, 1)
    assert rows["spring-boot-starter-web"].eol_product == "spring-boot"
    assert rows["spring-boot-starter-web"].eol_cycle == "3.2"
    assert rows["spring-boot-starter-web"].eol_source == "endoflife.date@2026-01-01"

    assert rows["spring-boot-actuator"].eol_state == "supported"
    assert rows["spring-boot-experimental"].eol_state == "unknown"
    assert rows["express"].eol_state == "supported"
    assert rows["django"].eol_state == "eol"

    # Unmapped / near-miss components stay untouched — every column NULL.
    for untouched in ("lodash", "express-session"):
        row = rows[untouched]
        assert row.eol_state is None
        assert row.eol_product is None
        assert row.eol_evaluated_at is None


def test_rerun_is_idempotent(
    cv_registry: dict[str, _FakeComponentVersion], fixture_dataset: None
) -> None:
    _persist(_FakeSession())
    first_stamp = _by_name(cv_registry)["spring-boot-starter-web"].eol_evaluated_at
    assert first_stamp is not None

    _persist(_FakeSession())  # same catalog rows re-observed (shared registry)
    second_stamp = _by_name(cv_registry)["spring-boot-starter-web"].eol_evaluated_at
    assert second_stamp == first_stamp  # changed-value guard: not re-dirtied


def test_eol_disabled_skips_stamping(
    cv_registry: dict[str, _FakeComponentVersion],
    fixture_dataset: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EOL_ENABLED", "false")
    _persist(_FakeSession())
    assert all(cv.eol_state is None for cv in cv_registry.values())


def test_corrupt_dataset_degrades_to_no_stamping(
    cv_registry: dict[str, _FakeComponentVersion],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = tmp_path / "corrupt.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("EOL_SNAPSHOT_PATH", str(bad))
    _persist(_FakeSession())  # must not raise
    assert all(cv.eol_state is None for cv in cv_registry.values())
