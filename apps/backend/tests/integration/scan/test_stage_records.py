"""Stage timings and degraded stages reach ``scans.scan_metadata`` (U3-B, NF-1).

These write to the real database through the functions the pipelines call, so a
write that is never committed cannot pass. Failures are injected at the point the
real stage catches them, and the clean run is asserted to leave nothing behind:
a record that appears on a good scan would teach people to ignore it.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from models import Scan
from services import scan_outcome as so
from tests._db_required import migrate_to_head
from tests.integration.scan.test_container_multi_cve import _seed_queued_container_scan

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


def _metadata(session: Session, scan_id: uuid.UUID) -> dict[str, Any]:
    session.expire_all()
    scan = session.get(Scan, scan_id)
    assert scan is not None
    return dict(scan.scan_metadata or {})


def _run_stages(scan_id: uuid.UUID, stages: list[str], *, untimed: tuple[str, ...] = ()) -> None:
    from tasks._scan_pipeline import set_stage

    for stage in stages:
        set_stage(scan_id, stage, 10, timed=stage not in untimed)


# ---------------------------------------------------------------------------
# Timings
# ---------------------------------------------------------------------------


def test_every_stage_that_ran_has_a_closed_window(sync_session: Session) -> None:
    from tasks._scan_pipeline import mark_succeeded

    scan_id = _seed_queued_container_scan()
    _run_stages(scan_id, ["bootstrap", "fetch", "cdxgen", "finalize"])
    mark_succeeded(scan_id)

    timings = _metadata(sync_session, scan_id)[so.STAGE_TIMINGS_KEY]
    # JSONB does not keep key order; a reader orders stages by their start time.
    assert sorted(timings, key=lambda s: timings[s]["started_at"]) == [
        "bootstrap",
        "fetch",
        "cdxgen",
        "finalize",
    ]
    for entry in timings.values():
        assert set(entry) == {"started_at", "ended_at"}
        assert entry["started_at"] <= entry["ended_at"]


def test_a_stage_that_did_no_work_is_not_timed(sync_session: Session) -> None:
    """The fingerprint-reuse path announces prep and cdxgen without running them."""
    from tasks._scan_pipeline import mark_succeeded

    scan_id = _seed_queued_container_scan()
    _run_stages(scan_id, ["fetch", "prep", "cdxgen", "sign"], untimed=("prep", "cdxgen"))
    mark_succeeded(scan_id)

    meta = _metadata(sync_session, scan_id)
    assert set(meta[so.STAGE_TIMINGS_KEY]) == {"fetch", "sign"}
    # The row itself still moved through them, so watchers saw the full sequence.
    scan = sync_session.get(Scan, scan_id)
    assert scan is not None and scan.current_step == "finalize"


def test_a_failed_scan_closes_the_stage_it_died_in(sync_session: Session) -> None:
    from tasks._scan_pipeline import record_terminal_failure

    scan_id = _seed_queued_container_scan()
    _run_stages(scan_id, ["fetch", "cdxgen"])
    record_terminal_failure(scan_id, "cdxgen exited 1")

    timings = _metadata(sync_session, scan_id)[so.STAGE_TIMINGS_KEY]
    assert "ended_at" in timings["cdxgen"]


def test_a_rerun_starts_from_empty_records(sync_session: Session) -> None:
    """Lifecycle: run and degrade, then re-run the same scan row."""
    from tasks._scan_pipeline import record_stage_outcome
    from tasks.scan_source import _mark_running

    scan_id = _seed_queued_container_scan()
    _run_stages(scan_id, ["fetch"])
    record_stage_outcome(scan_id, "sign")
    before = _metadata(sync_session, scan_id)
    assert so.STAGE_TIMINGS_KEY in before and so.STAGE_OUTCOMES_KEY in before

    scan = sync_session.get(Scan, scan_id)
    assert scan is not None
    _mark_running(sync_session, scan)

    after = _metadata(sync_session, scan_id)
    assert so.STAGE_TIMINGS_KEY not in after
    assert so.STAGE_OUTCOMES_KEY not in after


def test_the_container_pipeline_records_timings_too(sync_session: Session) -> None:
    from tasks.scan_container import _mark_succeeded, _set_stage

    scan_id = _seed_queued_container_scan()
    _set_stage(scan_id, "bootstrap")
    _set_stage(scan_id, "trivy")
    _mark_succeeded(scan_id)

    timings = _metadata(sync_session, scan_id)[so.STAGE_TIMINGS_KEY]
    assert set(timings) == {"bootstrap", "trivy"}
    assert all("ended_at" in e for e in timings.values())


# ---------------------------------------------------------------------------
# Degraded stages: each caught failure leaves a record; a clean one leaves none
# ---------------------------------------------------------------------------


def test_a_clean_scan_records_no_degraded_stage(sync_session: Session) -> None:
    from tasks._scan_pipeline import mark_succeeded

    scan_id = _seed_queued_container_scan()
    _run_stages(scan_id, ["fetch", "cdxgen", "trivy"])
    mark_succeeded(scan_id)

    assert so.STAGE_OUTCOMES_KEY not in _metadata(sync_session, scan_id)


def _outcomes(session: Session, scan_id: uuid.UUID) -> dict[str, str]:
    raw = _metadata(session, scan_id).get(so.STAGE_OUTCOMES_KEY) or {}
    return {stage: entry["reason"] for stage, entry in raw.items()}


class _Boom(Exception):
    pass


def test_prep_failures_are_recorded_by_kind(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    import tasks.scan_source as mod

    cases: list[tuple[Any, str]] = [
        (subprocess.CompletedProcess(["x"], 1, "", "err"), "failed"),
        (subprocess.TimeoutExpired("x", 1), "timeout"),
        (FileNotFoundError("x"), "not_installed"),
        (PermissionError("x"), "failed"),
    ]
    for outcome, expected in cases:
        scan_id = _seed_queued_container_scan()

        def fake_run(*_a: Any, _o: Any = outcome, **_k: Any) -> Any:
            if isinstance(_o, Exception):
                raise _o
            return _o

        monkeypatch.setattr(subprocess, "run", fake_run)
        mod._run_prep("bundle lock", ["bundle", "lock"], tmp_path, 5, scan_id)
        assert _outcomes(sync_session, scan_id) == {"prep": expected}, outcome


def test_a_prep_step_that_succeeds_records_nothing(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    import tasks.scan_source as mod

    scan_id = _seed_queued_container_scan()
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(["x"], 0, "", "")
    )
    mod._run_prep("bundle lock", ["bundle", "lock"], tmp_path, 5, scan_id)
    assert so.STAGE_OUTCOMES_KEY not in _metadata(sync_session, scan_id)


def test_scancode_skips_are_recorded_but_a_deliberate_off_is_not(sync_session: Session) -> None:
    from integrations import scancode as scancode_adapter
    from tasks.scan_source import _record_scancode_skipped

    skipped = _seed_queued_container_scan()
    _record_scancode_skipped(skipped, scancode_adapter.ScancodeTimeout("x"))
    assert _outcomes(sync_session, skipped) == {"scancode": "timeout"}

    off = _seed_queued_container_scan()
    _record_scancode_skipped(off, scancode_adapter.ScancodeDisabled("off"))
    assert so.STAGE_OUTCOMES_KEY not in _metadata(sync_session, off)


def test_a_failed_detected_license_write_is_reported_and_rolled_back(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tasks.scan_source as mod

    def boom(*_a: Any, **_k: Any) -> None:
        raise SQLAlchemyError("constraint")

    monkeypatch.setattr(mod, "_persist_detected_licenses", boom)
    scan_id = _seed_queued_container_scan()
    failed = mod._persist_detected_licenses_or_skip(
        sync_session, scan_uuid=scan_id, sbom={}, detections=[]
    )
    assert failed is True

    monkeypatch.setattr(mod, "_persist_detected_licenses", lambda *a, **k: None)
    assert (
        mod._persist_detected_licenses_or_skip(
            sync_session, scan_uuid=scan_id, sbom={}, detections=[]
        )
        is False
    )


@pytest.mark.parametrize(
    ("stage", "patch_target", "call"),
    [
        ("sign", "cosign_adapter.sign_blob", "sign"),
        ("preserve", "preserve_scan_source", "preserve"),
        ("scope_filter", "sbom_scope_filter.filter_sbom_to_runtime_scope", "scope"),
        ("approvals", "sync_session_scope", "approvals"),
    ],
)
def test_a_caught_stage_failure_is_recorded(
    sync_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: str,
    patch_target: str,
    call: str,
) -> None:
    import tasks.scan_source as mod
    from integrations import cdxgen as cdxgen_adapter

    scan_id = _seed_queued_container_scan()

    def boom(*_a: Any, **_k: Any) -> Any:
        raise _Boom("injected")

    target, _, attr = patch_target.rpartition(".")
    owner = getattr(mod, target) if target else mod
    monkeypatch.setattr(owner, attr, boom)

    if call == "sign":
        sbom = tmp_path / "s.json"
        signed = mod._sign_sbom(scan_uuid=scan_id, sbom_path=sbom, workspace=tmp_path)
        assert signed is False
    elif call == "preserve":
        mod._preserve_source_tree(
            scan_uuid=scan_id,
            project_id=uuid.uuid4(),
            source_dir=tmp_path / "source",
            scancode_json_path=None,
            sbom_path=None,
        )
    elif call == "scope":
        monkeypatch.setattr(mod, "scan_scope_filter_enabled", lambda: True)
        result = cdxgen_adapter.CdxgenResult(sbom_path=tmp_path / "s.json", sbom={"components": []})
        mod._apply_scope_filter(scan_uuid=scan_id, cdxgen_result=result, source_dir=tmp_path)
    else:
        mod._auto_create_conditional_approvals(scan_uuid=scan_id, project_id=uuid.uuid4())

    assert _outcomes(sync_session, scan_id) == {stage: "failed"}


def test_a_reachability_dispatch_failure_is_recorded(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tasks
    import tasks.scan_source as mod

    def boom(_scan_id: str) -> str:
        raise _Boom("broker down")

    monkeypatch.setattr(tasks, "enqueue_reachability", boom)
    scan_id = _seed_queued_container_scan()
    mod._dispatch_reachability(scan_id)
    assert _outcomes(sync_session, scan_id) == {"reachability": "failed"}


def test_recording_never_raises_for_a_missing_scan() -> None:
    from tasks._scan_pipeline import record_stage_outcome

    record_stage_outcome(uuid.uuid4(), "sign")
