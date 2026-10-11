"""The BomLens engine adapter, end to end on real BomLens output (T2-1).

Every case reads a folder the published BomLens image wrote
(``tests/fixtures/bomlens/``, see its ``PROVENANCE.md``) and writes through the
real persist paths into the real database, then reads the rows back in a
separate session. A write that is never committed cannot pass.

Matrix: a clean run (cdxgen through BomLens' two-stage path), a container image
run with real multi-CVE density, a degraded run, a run whose vulnerability scan
failed (the scan must fail), a run the engine reports as failed, a manifest of
an unknown contract major (refused before anything is written), and an output
file that does not match its recorded digest.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from integrations.bomlens.contract import (
    BomLensContractError,
    BomLensRunFailed,
    BomLensVulnerabilityScanFailed,
    UnsupportedContractVersion,
)
from models import Scan, ScanArtifact, ScanComponent, Vulnerability, VulnerabilityFinding
from services import scan_outcome as so
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "bomlens"

#: The image every fixture came from (PROVENANCE.md).
IMAGE_DIGEST = "sha256:1de6f4d0c8969116347af5d55d69a61b38f3670eda819a63e09a197a53f1ad57"


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    # persist_sbom_components asks public registries for missing licences when
    # this is on; the adapter test is about the adapter, not the network.
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", "false")


def _seed_running_source_scan() -> uuid.UUID:
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url

    async def _build() -> uuid.UUID:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team, git_url=None)
            scan = Scan(
                project_id=project.id,
                kind="source",
                status="running",
                progress_percent=0,
                requested_by_user_id=user.id,
                scan_metadata={},
            )
            s.add(scan)
            await s.commit()
            await s.refresh(scan)
            scan_id = scan.id
        await engine.dispose()
        return scan_id

    return asyncio.run(_build())


def _copy_fixture(name: str, tmp_path: Path) -> Path:
    target = tmp_path / name
    shutil.copytree(FIXTURES / name, target)
    return target


def _rewrite_manifest(folder: Path, change: Any) -> None:
    path = folder / "bomlens-result.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    change(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _metadata(session: Session, scan_id: uuid.UUID) -> dict[str, Any]:
    session.expire_all()
    scan = session.get(Scan, scan_id)
    assert scan is not None
    return dict(scan.scan_metadata or {})


def _component_count(session: Session, scan_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(ScanComponent).where(ScanComponent.scan_id == scan_id)
        ).scalar_one()
    )


def _finding_ids(session: Session, scan_id: uuid.UUID) -> list[str]:
    rows = session.execute(
        select(Vulnerability.external_id)
        .join(VulnerabilityFinding, VulnerabilityFinding.vulnerability_id == Vulnerability.id)
        .where(VulnerabilityFinding.scan_id == scan_id)
    ).scalars()
    return sorted(rows)


def _artifact_kinds(session: Session, scan_id: uuid.UUID) -> dict[str, str | None]:
    rows = session.execute(
        select(ScanArtifact.kind, ScanArtifact.sha256).where(ScanArtifact.scan_id == scan_id)
    ).all()
    return {kind: sha for kind, sha in rows}


# ---------------------------------------------------------------------------
# A clean run
# ---------------------------------------------------------------------------


def test_a_clean_run_persists_components_findings_and_its_records(
    tmp_path: Path, sync_session: Session
) -> None:
    from tasks.bomlens_result import ENGINE_METADATA_KEY, apply_bomlens_result

    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("source-ok", tmp_path)

    applied = apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)

    # lodash 4.17.21 is the fixture's one dependency; Trivy reported three CVEs
    # against it, and all three land.
    assert _component_count(sync_session, scan_id) == 1
    assert _finding_ids(sync_session, scan_id) == [
        "CVE-2025-13465",
        "CVE-2026-2950",
        "CVE-2026-4800",
    ]
    assert applied.findings_persisted == 3

    meta = _metadata(sync_session, scan_id)
    # Nothing degraded, so nothing is recorded: a record on a clean scan would
    # teach people to ignore it.
    assert so.degraded_stages(meta) == []
    assert so.SCANCODE_SKIP_KEY not in meta
    # Timings only for what ran: deep-license was turned off, so no scancode.
    assert set(meta[so.STAGE_TIMINGS_KEY]) == {"cdxgen", "postprocess", "trivy"}
    engine = meta[ENGINE_METADATA_KEY]
    assert engine["image_digest"] == IMAGE_DIGEST
    assert engine["contract_version"] == "1.0.0-dev"
    assert engine["run_status"] == "succeeded"
    assert {"id": "deep-license", "status": "disabled", "reason": None} in engine["steps"]

    manifest = json.loads((folder / "bomlens-result.json").read_text(encoding="utf-8"))
    recorded = {o["role"]: o["sha256"] for o in manifest["outputs"]}
    assert _artifact_kinds(sync_session, scan_id) == {
        "sbom_cyclonedx": recorded["sbom.cyclonedx"],
        "trivy_sbom_report": recorded["vulnerabilities.trivy"],
    }


def test_an_image_run_keeps_every_cve_on_every_package(
    tmp_path: Path, sync_session: Session
) -> None:
    """Real density: 48 CVEs on 7 of alpine 3.19.0's 15 packages.

    The SBOM also lists 80 files and the distribution itself, none with a PURL;
    they are not packages and stay out of the inventory. The distribution is
    recorded from Trivy's report instead, as the container pipeline records it.
    """
    from tasks.bomlens_result import apply_bomlens_result

    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("image-ok", tmp_path)

    applied = apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)

    assert applied.components_left_out == 81
    assert _component_count(sync_session, scan_id) == 15
    assert len(_finding_ids(sync_session, scan_id)) == 48
    assert _metadata(sync_session, scan_id)["os"] == {
        "family": "alpine",
        "name": "3.19.0",
        "eosl": True,
    }


# ---------------------------------------------------------------------------
# Degraded
# ---------------------------------------------------------------------------


def test_a_degraded_run_records_each_degradation_and_still_persists(
    tmp_path: Path, sync_session: Session
) -> None:
    """The image ran without the cdxgen stage (manifest-only fallback) and was
    asked for licence detection it does not carry."""
    from tasks.bomlens_result import apply_bomlens_result

    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("source-degraded", tmp_path)

    apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)

    meta = _metadata(sync_session, scan_id)
    assert so.degraded_stages(meta) == [
        {"stage": "generate", "reason": "fallback"},
        {"stage": "scancode", "reason": "not_installed"},
    ]
    # The Overview's own scancode notice reads this key, not the list.
    assert so.scancode_skip_reason(meta) == "not_installed"
    # A skipped step did no work and has no window.
    assert "scancode" not in meta[so.STAGE_TIMINGS_KEY]
    assert _component_count(sync_session, scan_id) == 71
    assert len(_finding_ids(sync_session, scan_id)) == 14


def test_a_reason_from_a_later_contract_minor_is_stored_degraded_and_verbatim(
    tmp_path: Path, sync_session: Session
) -> None:
    """BomLens #191 adds ``no_components`` on ``generate`` in a minor version.
    This release does not know it; the run still persists, and the stage is
    recorded as degraded (catch-all ``failed``) with the engine's word kept."""
    from tasks.bomlens_result import apply_bomlens_result

    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("source-ok", tmp_path)

    def _no_components(manifest: dict[str, Any]) -> None:
        manifest["contract"]["version"] = "1.1.0"
        for step in manifest["steps"]:
            if step["id"] == "generate":
                step.update(status="degraded", reason="no_components")
        manifest["run"].update(status="succeeded_degraded", reason="step_failed_or_degraded")

    _rewrite_manifest(folder, _no_components)

    apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)

    meta = _metadata(sync_session, scan_id)
    assert meta[so.STAGE_OUTCOMES_KEY] == {
        "generate": {"reason": "failed", "engine_reason": "no_components"}
    }
    assert so.degraded_stages(meta) == [{"stage": "generate", "reason": "failed"}]
    assert _component_count(sync_session, scan_id) == 1


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


def test_a_failed_vulnerability_scan_fails_and_keeps_the_inventory(
    tmp_path: Path, sync_session: Session
) -> None:
    """Trivy had no database and no network. BomLens wrote an empty report with
    a ScanError and recorded scan-security as failed; that is not "no
    vulnerabilities", and nothing may be persisted as if it were."""
    from tasks.bomlens_result import apply_bomlens_result

    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("source-vulnerabilities-failed", tmp_path)

    with pytest.raises(BomLensVulnerabilityScanFailed, match="vulnerability scan failed"):
        apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)

    assert _component_count(sync_session, scan_id) == 71
    assert _finding_ids(sync_session, scan_id) == []
    assert set(_artifact_kinds(sync_session, scan_id)) == {"sbom_cyclonedx"}
    meta = _metadata(sync_session, scan_id)
    # scan-security is not a degradable stage: it fails the scan instead.
    assert so.degraded_stages(meta) == [{"stage": "generate", "reason": "fallback"}]


def test_a_scan_error_in_the_report_fails_even_when_the_step_says_ok(
    tmp_path: Path, sync_session: Session
) -> None:
    """The second layer, on its own: the same real report with the step's
    status rewritten to ok. Without the ScanError check, an empty Results list
    would be persisted as a clean scan."""
    from tasks.bomlens_result import apply_bomlens_result

    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("source-vulnerabilities-failed", tmp_path)

    def _step_ok(manifest: dict[str, Any]) -> None:
        for step in manifest["steps"]:
            if step["id"] == "scan-security":
                step.update(status="ok", reason=None, detail=None)
        manifest["run"].update(status="succeeded_degraded")

    _rewrite_manifest(folder, _step_ok)

    with pytest.raises(BomLensVulnerabilityScanFailed, match="carries a scan error"):
        apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)
    assert _finding_ids(sync_session, scan_id) == []


def test_the_scan_task_marks_the_scan_failed_on_an_engine_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    """Through the task body's own failure mapping, the way the executor will
    reach it: the scan ends failed with the engine's message, not succeeded."""
    from tasks import scan_source
    from tasks.bomlens_result import apply_bomlens_result

    folder = _copy_fixture("source-vulnerabilities-failed", tmp_path / "out")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path / "ws"))

    def _pipeline(*, scan_uuid: uuid.UUID, **_kwargs: object) -> None:
        apply_bomlens_result(scan_uuid=scan_uuid, output_dir=folder)
        scan_source._mark_succeeded(scan_uuid)

    monkeypatch.setattr(scan_source, "_run_pipeline", _pipeline)
    scan_id = _seed_running_source_scan()

    result = scan_source.scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), result.traceback

    sync_session.expire_all()
    scan = sync_session.get(Scan, scan_id)
    assert scan is not None
    assert scan.status == "failed"
    assert scan.error_message is not None
    assert scan.error_message.startswith("scan engine: the vulnerability scan failed")
    assert str(folder) not in scan.error_message


def test_a_run_the_engine_reports_as_failed_writes_no_inventory(
    tmp_path: Path, sync_session: Session
) -> None:
    from tasks.bomlens_result import apply_bomlens_result

    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("source-run-failed", tmp_path)

    with pytest.raises(BomLensRunFailed) as caught:
        apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)

    assert caught.value.step == "generate"
    assert _component_count(sync_session, scan_id) == 0
    # The record of what failed is still there for the operator.
    assert so.degraded_stages(_metadata(sync_session, scan_id)) == [
        {"stage": "generate", "reason": "failed"}
    ]


def test_an_unknown_contract_major_is_refused_before_anything_is_written(
    tmp_path: Path, sync_session: Session
) -> None:
    from tasks.bomlens_result import apply_bomlens_result

    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("source-ok", tmp_path)
    _rewrite_manifest(folder, lambda m: m["contract"].update(version="2.0.0"))

    with pytest.raises(UnsupportedContractVersion, match="'2.0.0'"):
        apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)

    assert _metadata(sync_session, scan_id) == {}
    assert _component_count(sync_session, scan_id) == 0


def test_an_output_that_does_not_match_its_digest_is_refused(
    tmp_path: Path, sync_session: Session
) -> None:
    """Same size, different bytes: only the digest can tell."""
    from tasks.bomlens_result import apply_bomlens_result

    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("source-ok", tmp_path)
    bom = folder / "node_1.0.0_bom.json"
    content = bom.read_text(encoding="utf-8")
    bom.write_text(content.replace("lodash", "lodasH", 1), encoding="utf-8")

    with pytest.raises(BomLensContractError, match="SHA-256"):
        apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)
    assert _component_count(sync_session, scan_id) == 0


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_a_rerun_of_the_same_scan_neither_duplicates_nor_inherits(
    tmp_path: Path, sync_session: Session
) -> None:
    """Degraded run, then the scan row is re-run (as acks_late redelivery does)
    and this time BomLens comes back clean: the first run's rows and its skip
    notice are both gone."""
    from tasks.bomlens_result import apply_bomlens_result
    from tasks.scan_source import _mark_running, _reset_scan_for_rerun

    scan_id = _seed_running_source_scan()
    apply_bomlens_result(scan_uuid=scan_id, output_dir=_copy_fixture("source-degraded", tmp_path))
    assert so.scancode_skip_reason(_metadata(sync_session, scan_id)) == "not_installed"

    scan = sync_session.get(Scan, scan_id)
    assert scan is not None
    _reset_scan_for_rerun(sync_session, scan)
    _mark_running(sync_session, scan)

    apply_bomlens_result(scan_uuid=scan_id, output_dir=_copy_fixture("source-ok", tmp_path))

    meta = _metadata(sync_session, scan_id)
    assert so.degraded_stages(meta) == []
    assert so.SCANCODE_SKIP_KEY not in meta
    # Only the second run's rows remain.
    assert _component_count(sync_session, scan_id) == 1
    assert len(_finding_ids(sync_session, scan_id)) == 3


# ---------------------------------------------------------------------------
# The flag, while the executor does not exist yet
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("engine", "warned"), [("bomlens", True), ("inprocess", False)])
def test_selecting_bomlens_warns_and_keeps_the_in_process_engine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
    engine: str,
    warned: bool,
) -> None:
    """SCAN_ENGINE=bomlens must not break scans before its executor ships: the
    scan runs in process and succeeds, and the log says the request was not met."""
    from structlog.testing import capture_logs

    from tasks.scan_source import scan_source_task
    from tests.integration.scan.test_scan_source_pipeline_mock import _stub_trivy_empty

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setenv("SCAN_ENGINE", engine)
    _stub_trivy_empty(monkeypatch)

    scan_id = _seed_running_source_scan()
    with capture_logs() as logs:
        result = scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), result.traceback

    sync_session.expire_all()
    scan = sync_session.get(Scan, scan_id)
    assert scan is not None and scan.status == "succeeded"
    fallbacks = [
        entry for entry in logs if entry["event"] == "scan_engine_unavailable_fallback_inprocess"
    ]
    expected = [{"requested": "bomlens", "log_level": "warning"}] if warned else []
    assert [{k: e[k] for k in ("requested", "log_level")} for e in fallbacks] == expected
