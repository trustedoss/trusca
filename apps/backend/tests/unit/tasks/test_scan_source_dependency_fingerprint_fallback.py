# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
S8 (concurrency-scaling-plan-2026-08-22.md §3.2): reuse-extraction failure
fallback, unit-tested in isolation from the full pipeline.

``tests/integration/scan/test_scan_source_dependency_fingerprint_reuse.py``
drives the SUCCESSFUL reuse path end to end against a real Postgres. What is
still missing, and what this file pins, is the defensive branch: a
fingerprint match whose preserved tarball is gone, corrupt, or carries a
non-object JSON payload by the time the reuse extraction actually runs (the
tarball was written at a DIFFERENT time than the fingerprint comparison. The
retention beat, an admin purge, or plain disk trouble can remove it in
between). ``_reuse_prior_sbom`` must return ``None`` (never raise) on every
one of those, and ``_run_pipeline``'s caller must fall back to the full
cdxgen path exactly as if no fingerprint match had been found at all. S8 is
an optimization, never a correctness dependency.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

import tasks.scan_source as mod
from services.source_preservation_service import (
    PreservationTooLarge,
    PreservedSbomMissing,
    SourcePreservationError,
)

# ---------------------------------------------------------------------------
# _reuse_prior_sbom: success
# ---------------------------------------------------------------------------


def test_reuse_prior_sbom_returns_the_extracted_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scan_uuid = uuid.uuid4()
    expected_project_id = uuid.uuid4()
    expected_prior_scan_id = uuid.uuid4()
    workspace = tmp_path / "workspace"

    sbom = {"bomFormat": "CycloneDX", "components": [{"purl": "pkg:npm/x@1.0.0"}]}

    def _fake_extract(*, scan_id: uuid.UUID, project_id: uuid.UUID, dest_dir: Path) -> Path:
        assert scan_id == expected_prior_scan_id
        assert project_id == expected_project_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / "cdxgen.cdx.json"
        out.write_text(json.dumps(sbom), encoding="utf-8")
        return out

    monkeypatch.setattr(mod, "extract_preserved_sbom", _fake_extract)

    result = mod._reuse_prior_sbom(
        scan_uuid=scan_uuid,
        project_id=expected_project_id,
        prior_scan_id=expected_prior_scan_id,
        workspace=workspace,
    )

    assert result is not None
    assert result.sbom == sbom
    # Extracted into THIS scan's own workspace, not some shared/global path.
    assert str(workspace) in str(result.sbom_path)


# ---------------------------------------------------------------------------
# _reuse_prior_sbom: every failure mode returns None, never raises
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raised",
    [
        FileNotFoundError("preserved tarball missing"),
        PreservedSbomMissing("no sbom member"),
        PreservationTooLarge("sbom exceeds cap"),
        SourcePreservationError("tar corrupt"),
        OSError("disk read failed"),
    ],
)
def test_reuse_prior_sbom_extraction_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raised: Exception
) -> None:
    def _boom(*, scan_id: uuid.UUID, project_id: uuid.UUID, dest_dir: Path) -> Path:
        raise raised

    monkeypatch.setattr(mod, "extract_preserved_sbom", _boom)

    result = mod._reuse_prior_sbom(
        scan_uuid=uuid.uuid4(),
        project_id=uuid.uuid4(),
        prior_scan_id=uuid.uuid4(),
        workspace=tmp_path / "workspace",
    )

    assert result is None


def test_reuse_prior_sbom_corrupt_json_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The tarball extracted fine, but the bytes inside are not valid JSON,
    a truncated write, or a hostile/corrupted archive."""

    def _fake_extract(*, scan_id: uuid.UUID, project_id: uuid.UUID, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / "cdxgen.cdx.json"
        out.write_text("{not valid json", encoding="utf-8")
        return out

    monkeypatch.setattr(mod, "extract_preserved_sbom", _fake_extract)

    result = mod._reuse_prior_sbom(
        scan_uuid=uuid.uuid4(),
        project_id=uuid.uuid4(),
        prior_scan_id=uuid.uuid4(),
        workspace=tmp_path / "workspace",
    )

    assert result is None


def test_reuse_prior_sbom_non_object_json_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Valid JSON, but not a CycloneDX document (a bare JSON array). Must
    not be accepted as an ``sbom: dict`` just because ``json.loads`` succeeded."""

    def _fake_extract(*, scan_id: uuid.UUID, project_id: uuid.UUID, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / "cdxgen.cdx.json"
        out.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        return out

    monkeypatch.setattr(mod, "extract_preserved_sbom", _fake_extract)

    result = mod._reuse_prior_sbom(
        scan_uuid=uuid.uuid4(),
        project_id=uuid.uuid4(),
        prior_scan_id=uuid.uuid4(),
        workspace=tmp_path / "workspace",
    )

    assert result is None


# ---------------------------------------------------------------------------
# _record_input_manifests: a persist failure returns None (not the collected
# inventory), so S8's fingerprint computation never treats a scan whose
# manifest inventory it could not actually store as a fingerprintable one.
# ---------------------------------------------------------------------------


def test_record_input_manifests_returns_none_when_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    inventory = {
        "files": [{"path": "package.json", "size": 1, "sha256": "a" * 64}],
        "count": 1,
        "truncated": False,
    }
    monkeypatch.setattr(
        "tasks.scan_source.scan_inputs.collect_manifest_inventory",
        lambda _root: inventory,
    )

    @contextmanager
    def _boom_scope():  # type: ignore[no-untyped-def]
        raise RuntimeError("db unavailable")
        yield  # pragma: no cover (unreachable, satisfies generator shape)

    monkeypatch.setattr(mod, "sync_session_scope", _boom_scope)

    result = mod._record_input_manifests(uuid.uuid4(), Path("/nonexistent"))

    assert result is None


# ---------------------------------------------------------------------------
# _persist_dependency_fingerprint: best-effort, never raises
# ---------------------------------------------------------------------------


def test_persist_dependency_fingerprint_noop_when_none() -> None:
    """A None fingerprint (un-fingerprintable scan) must not even try a
    write, asserted by NOT monkeypatching sync_session_scope at all; a call
    into it would raise (no DB in this test)."""
    mod._persist_dependency_fingerprint(uuid.uuid4(), None)


def test_persist_dependency_fingerprint_swallows_a_db_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    @contextmanager
    def _boom_scope():  # type: ignore[no-untyped-def]
        raise RuntimeError("db unavailable")
        yield  # pragma: no cover (unreachable, satisfies generator shape)

    monkeypatch.setattr(mod, "sync_session_scope", _boom_scope)

    # Must not raise: a bookkeeping write failing here must never fail an
    # otherwise-successful scan.
    mod._persist_dependency_fingerprint(uuid.uuid4(), "a" * 64)


# ---------------------------------------------------------------------------
# _run_pipeline: a failed reuse extraction falls back to the full path
# ---------------------------------------------------------------------------


def test_run_pipeline_falls_back_to_full_cdxgen_when_reuse_extraction_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``_find_reusable_prior_scan`` found a fingerprint match, but
    ``_reuse_prior_sbom`` could not actually extract it (tarball reclaimed
    between the fingerprint write and this scan). The pipeline must still
    reach the full cdxgen call, not abort or silently skip SBOM generation."""
    scan_uuid = uuid.uuid4()
    project_id = uuid.uuid4()
    workspace = tmp_path / str(scan_uuid)

    monkeypatch.setattr(mod, "_set_stage", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_fetch_source", lambda **k: workspace / "source")
    monkeypatch.setattr(mod, "_resolve_project_root", lambda source_dir: source_dir)
    monkeypatch.setattr(mod, "_detect_and_record_env", lambda *a, **k: "unknown")
    monkeypatch.setattr(mod, "_record_input_manifests", lambda *a, **k: {
        "files": [{"path": "package.json", "size": 1, "sha256": "a" * 64}],
        "count": 1,
        "truncated": False,
    })
    monkeypatch.setattr(mod, "cdxgen_scanner_version", lambda: "12.3.3")
    monkeypatch.setattr(mod, "cdxgen_spec_version", lambda: "1.5")
    monkeypatch.setattr(mod, "cdxgen_fetch_license", lambda: False)
    monkeypatch.setattr(mod, "scan_scope_filter_enabled", lambda: True)
    monkeypatch.setattr(mod, "scan_scope_filter_maven_enabled", lambda: True)
    monkeypatch.setattr(mod, "scan_scope_filter_node_enabled", lambda: True)

    # A reuse candidate exists...
    monkeypatch.setattr(mod, "_find_reusable_prior_scan", lambda **k: uuid.uuid4())
    # ...but extraction fails (the defensive branch under test).
    monkeypatch.setattr(mod, "_reuse_prior_sbom", lambda **k: None)

    full_pipeline_calls: list[str] = []

    class _FakeExecutor:
        def generate_sbom(self, request, *, prep, stage, line_callback):  # type: ignore[no-untyped-def]
            full_pipeline_calls.append("generate_sbom")
            out_dir = request.output_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            sbom_path = out_dir / "cdxgen.cdx.json"
            sbom = {"bomFormat": "CycloneDX", "components": []}
            sbom_path.write_text(json.dumps(sbom), encoding="utf-8")

            class _Result:
                pass

            r = _Result()
            r.sbom_path = sbom_path  # type: ignore[attr-defined]
            r.sbom = sbom  # type: ignore[attr-defined]
            return r

    monkeypatch.setattr(
        "tasks.scan_source.scan_executor.get_executor", lambda: _FakeExecutor()
    )
    monkeypatch.setattr(mod, "_merge_cocoapods_components", lambda **k: None)
    monkeypatch.setattr(mod, "_apply_scope_filter", lambda **k: None)
    monkeypatch.setattr(mod, "_stamp_document_metadata", lambda **k: None)
    monkeypatch.setattr(mod, "_persist_artifact", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_sign_sbom", lambda **k: False)

    def _stop_here(**_k: object) -> None:
        raise _StopAtScancode()

    class _StopAtScancode(Exception):
        pass

    monkeypatch.setattr("tasks.scan_source.scancode_adapter.run_scancode", _stop_here)

    with pytest.raises(_StopAtScancode):
        mod._run_pipeline(
            scan_uuid=scan_uuid,
            project_id=project_id,
            workspace=workspace,
            git_url=None,
            scan_metadata={},
            ref="main",
        )

    assert full_pipeline_calls == ["generate_sbom"], (
        "a failed reuse extraction must fall back to the real cdxgen call, "
        "not silently skip SBOM generation"
    )
