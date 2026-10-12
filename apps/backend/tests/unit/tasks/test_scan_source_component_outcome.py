# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""``_record_component_outcome``: what a scan writes about finding nothing.

The pipeline reads cdxgen's exit code and nothing else, so a tree it cannot
parse produces ``components: []``, exits 0, and finishes ``succeeded``. Every
number downstream is then 0 for a reason nobody recorded. These tests pin the
recording step: the verdict describes the FINAL document, counts nested
components the way persistence does, and never turns a failure to describe a
scan into a failure of the scan.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

import tasks.scan_source as mod
from services.scan_outcome import (
    COMPONENTS_FOUND,
    EMPTY_NO_MANIFESTS,
    EMPTY_WITH_MANIFESTS,
    METADATA_KEY,
    STAGE_OUTCOMES_KEY,
    degraded_stages,
)


class _FakeScan:
    def __init__(self) -> None:
        self.scan_metadata: dict[str, Any] | None = {"detected_env": "node"}


class _FakeSession:
    """Just enough session to observe what the recorder wrote."""

    def __init__(self, scan: _FakeScan | None) -> None:
        self._scan = scan
        self.committed = 0

    def get(self, _model: Any, _pk: Any) -> _FakeScan | None:
        return self._scan

    def commit(self) -> None:
        self.committed += 1

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


@pytest.fixture
def scan_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    scan = _FakeScan()
    session = _FakeSession(scan)
    monkeypatch.setattr(mod, "sync_session_scope", lambda: session)
    return session


def _manifests(count: int) -> dict[str, Any]:
    return {"files": [], "count": count, "truncated": False}


def test_a_populated_sbom_records_the_ordinary_outcome(
    scan_session: _FakeSession,
) -> None:
    outcome = mod._record_component_outcome(
        uuid.uuid4(),
        sbom={"components": [{"purl": "pkg:npm/left-pad@1.0.0"}]},
        manifest_inventory=_manifests(1),
    )

    assert outcome == COMPONENTS_FOUND
    assert scan_session._scan is not None
    assert scan_session._scan.scan_metadata is not None
    assert scan_session._scan.scan_metadata[METADATA_KEY] == COMPONENTS_FOUND
    # The recorder merges rather than replaces: it must not drop what earlier
    # stages already wrote onto the same column.
    assert scan_session._scan.scan_metadata["detected_env"] == "node"
    assert scan_session.committed == 1


def test_empty_sbom_with_no_manifests_is_the_unsupported_ecosystem_case(
    scan_session: _FakeSession,
) -> None:
    outcome = mod._record_component_outcome(
        uuid.uuid4(), sbom={"components": []}, manifest_inventory=None
    )

    assert outcome == EMPTY_NO_MANIFESTS
    assert scan_session._scan is not None
    assert scan_session._scan.scan_metadata is not None
    assert scan_session._scan.scan_metadata[METADATA_KEY] == EMPTY_NO_MANIFESTS


def test_empty_sbom_with_manifests_present_is_a_scan_failure(
    scan_session: _FakeSession,
) -> None:
    """The tree declared dependencies and the scan produced none of them.

    Kept apart from the case above on purpose: this one wants the user to read
    the scan log, that one wants them to check whether we support their build
    system. One warning for both would send half of them the wrong way.
    """
    outcome = mod._record_component_outcome(
        uuid.uuid4(), sbom={"components": []}, manifest_inventory=_manifests(4)
    )

    assert outcome == EMPTY_WITH_MANIFESTS


def test_nested_components_count_as_found(scan_session: _FakeSession) -> None:
    """A top-level array of one entry can still carry a whole bundled tree.

    Persistence walks nested ``components``, so counting only the top level
    would call a populated scan empty in exactly the case where the SBOM is a
    single wrapper component.
    """
    outcome = mod._record_component_outcome(
        uuid.uuid4(),
        sbom={
            "components": [
                {
                    "purl": "pkg:npm/wrapper@1.0.0",
                    "components": [{"purl": "pkg:npm/nested@2.0.0"}],
                }
            ]
        },
        manifest_inventory=_manifests(1),
    )

    assert outcome == COMPONENTS_FOUND


def test_a_missing_sbom_is_empty_not_an_exception(
    scan_session: _FakeSession,
) -> None:
    assert (
        mod._record_component_outcome(uuid.uuid4(), sbom=None, manifest_inventory=None)
        == EMPTY_NO_MANIFESTS
    )


def test_a_persistence_failure_never_fails_the_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scan must not die describing itself.

    The verdict is an observation about the scan, not a stage of it, so the
    recorder still returns its answer when the write goes wrong.
    """

    def _explode() -> Any:
        raise RuntimeError("database went away")

    monkeypatch.setattr(mod, "sync_session_scope", _explode)

    assert (
        mod._record_component_outcome(
            uuid.uuid4(), sbom={"components": []}, manifest_inventory=_manifests(2)
        )
        == EMPTY_WITH_MANIFESTS
    )


def test_a_vanished_scan_row_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession(None)
    monkeypatch.setattr(mod, "sync_session_scope", lambda: session)

    assert (
        mod._record_component_outcome(
            uuid.uuid4(), sbom={"components": []}, manifest_inventory=None
        )
        == EMPTY_NO_MANIFESTS
    )
    assert session.committed == 0


# ---------------------------------------------------------------------------
# The ``generate`` / ``no_components`` record, the same one BomLens leaves
# ---------------------------------------------------------------------------


def _inventory(*paths: str, count: int | None = None) -> dict[str, Any]:
    files = [{"path": path, "size": 1, "sha256": "0" * 64} for path in paths]
    return {"files": files, "count": len(files) if count is None else count, "truncated": False}


def test_an_empty_result_despite_manifests_records_generate_no_components(
    scan_session: _FakeSession,
) -> None:
    mod._record_component_outcome(
        uuid.uuid4(),
        sbom={"components": []},
        manifest_inventory=_inventory("app/build.gradle", "settings.gradle"),
    )

    assert scan_session._scan is not None
    meta = scan_session._scan.scan_metadata
    assert meta is not None
    assert meta[STAGE_OUTCOMES_KEY] == {
        "generate": {
            "reason": "no_components",
            "engine_detail": "manifests found: app/build.gradle, settings.gradle",
        }
    }
    assert degraded_stages(meta) == [{"stage": "generate", "reason": "no_components"}]
    assert meta["detected_env"] == "node"
    assert scan_session.committed == 1


def test_a_failed_detail_keeps_the_outcome_and_the_stage(
    scan_session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _broken(_inventory: Any) -> str:
        raise RuntimeError("detail could not be built")

    monkeypatch.setattr(mod, "_no_components_detail", _broken)

    mod._record_component_outcome(
        uuid.uuid4(), sbom={"components": []}, manifest_inventory=_inventory("pom.xml")
    )

    assert scan_session._scan is not None
    meta = scan_session._scan.scan_metadata
    assert meta is not None
    assert meta[METADATA_KEY] == EMPTY_WITH_MANIFESTS
    assert meta[STAGE_OUTCOMES_KEY] == {"generate": {"reason": "no_components"}}
    assert scan_session.committed == 1


@pytest.mark.parametrize(
    ("sbom", "inventory"),
    [
        ({"components": [{"purl": "pkg:npm/a@1"}]}, _inventory("package.json")),
        ({"components": []}, None),
    ],
    ids=["components_found", "empty_no_manifests"],
)
def test_no_stage_is_recorded_for_the_other_outcomes(
    scan_session: _FakeSession, sbom: dict[str, Any], inventory: dict[str, Any] | None
) -> None:
    mod._record_component_outcome(uuid.uuid4(), sbom=sbom, manifest_inventory=inventory)

    assert scan_session._scan is not None
    assert STAGE_OUTCOMES_KEY not in (scan_session._scan.scan_metadata or {})


def test_the_detail_names_five_manifests_and_counts_the_rest() -> None:
    inventory = _inventory(*(f"m{i}/pom.xml" for i in range(7)), count=9)

    detail = mod._no_components_detail(inventory)

    assert detail == (
        "manifests found: m0/pom.xml, m1/pom.xml, m2/pom.xml, m3/pom.xml, m4/pom.xml and 4 more"
    )


def test_the_detail_drops_control_characters_from_a_path() -> None:
    """The paths come from the scanned tree, so they get the engine-output treatment:
    a NUL would fail the JSONB commit, a line break would split a log line."""
    detail = mod._no_components_detail(_inventory("a\x00b/​package.json\nx"))

    assert detail == "manifests found: ab/package.json x"


@pytest.mark.parametrize(
    "inventory",
    [None, {"count": 2}, {"files": "nope", "count": 1}, {"files": [{}], "count": 1}],
)
def test_no_detail_without_usable_paths(inventory: dict[str, Any] | None) -> None:
    assert mod._no_components_detail(inventory) is None
