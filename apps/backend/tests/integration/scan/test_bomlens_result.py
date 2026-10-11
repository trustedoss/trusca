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
from models import (
    License,
    LicenseFinding,
    Scan,
    ScanArtifact,
    ScanComponent,
    Vulnerability,
    VulnerabilityFinding,
)
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


def _artifact_files(session: Session, scan_id: uuid.UUID) -> dict[str, Path]:
    rows = session.execute(
        select(ScanArtifact.kind, ScanArtifact.storage_path).where(ScanArtifact.scan_id == scan_id)
    ).all()
    return {kind: Path(path) for kind, path in rows}


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    assert so.LICENSE_ENRICHMENT_KEY not in meta
    # The exclusion was on (it is by default) and left no manifest out.
    scope = meta["scope_filter"]
    assert (scope["dropped"], scope["kept"], scope["excluded_manifests"]) == ({}, 1, [])
    assert "**/benchmark/**" in scope["excluded_paths"]
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
    # The rows point at TRUSCA's copy of the verified bytes, outside the
    # engine's folder, and the copy is byte-identical to what was checked.
    for kind, path in _artifact_files(sync_session, scan_id).items():
        assert folder.resolve() not in path.resolve().parents, kind
        assert _sha256(path) == _artifact_kinds(sync_session, scan_id)[kind]


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
    """A later minor adds a reason on ``generate`` (as BomLens #194 added
    ``no_components`` before this reader knew it). The run still persists, and
    the stage is recorded as degraded (catch-all ``failed``) with the engine's
    word kept."""
    from tasks.bomlens_result import apply_bomlens_result

    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("source-ok", tmp_path)

    def _new_reason(manifest: dict[str, Any]) -> None:
        manifest["contract"]["version"] = "1.1.0"
        for step in manifest["steps"]:
            if step["id"] == "generate":
                step.update(status="degraded", reason="no_dependencies_resolved")
        manifest["run"].update(status="succeeded_degraded", reason="step_failed_or_degraded")

    _rewrite_manifest(folder, _new_reason)

    apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)

    meta = _metadata(sync_session, scan_id)
    assert meta[so.STAGE_OUTCOMES_KEY] == {
        "generate": {"reason": "failed", "engine_reason": "no_dependencies_resolved"}
    }
    assert so.degraded_stages(meta) == [{"stage": "generate", "reason": "failed"}]
    assert _component_count(sync_session, scan_id) == 1


# ---------------------------------------------------------------------------
# What the deep-license image adds: detected licences, registry lookups, the
# non-shipped exclusion, and a source that declared what it did not yield
# ---------------------------------------------------------------------------


def _detected(session: Session, scan_id: uuid.UUID) -> list[tuple[str | None, str | None]]:
    rows = session.execute(
        select(License.spdx_id, LicenseFinding.source_path)
        .join(LicenseFinding, LicenseFinding.license_id == License.id)
        .where(LicenseFinding.scan_id == scan_id, LicenseFinding.kind == "detected")
    ).all()
    return sorted((spdx, path) for spdx, path in rows)


#: What scancode found in the captured run's own files (PROVENANCE.md).
CAPTURED_DETECTIONS = [
    ("Apache-2.0", "apache-header.js"),
    ("GPL-3.0-or-later", "gpl-header.js"),
    ("MPL-2.0", "mpl-header.js"),
]


def test_a_deep_license_run_persists_detections_and_its_completeness_records(
    tmp_path: Path, sync_session: Session
) -> None:
    from tasks.bomlens_result import apply_bomlens_result

    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("source-deep-license", tmp_path)

    applied = apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)

    # Detected licences, on the project's own files, through the in-process
    # persist path (kind detected, a path per file, scancode as the source).
    assert _detected(sync_session, scan_id) == CAPTURED_DETECTIONS
    assert applied.detections_persisted == 3
    sources = sync_session.execute(
        select(LicenseFinding.raw_data).where(
            LicenseFinding.scan_id == scan_id, LicenseFinding.kind == "detected"
        )
    ).scalars()
    assert {(raw or {}).get("source") for raw in sources} == {"scancode"}

    manifest = json.loads((folder / "bomlens-result.json").read_text(encoding="utf-8"))
    recorded = {o["role"]: o["sha256"] for o in manifest["outputs"]}
    assert (
        _artifact_kinds(sync_session, scan_id)["scancode_result"]
        == (recorded["license-scan.scancode"])
    )

    meta = _metadata(sync_session, scan_id)
    # The registry ran out of time before its one answer: the Overview notice
    # and the metric read it from the same record as an in-process scan.
    assert so.license_lookup_gap(meta) == (1, "budget_exhausted")
    assert meta[so.LICENSE_ENRICHMENT_KEY]["engines"] == ["bomlens"]
    assert so.degraded_stages(meta) == [{"stage": "enrich", "reason": "budget_exhausted"}]
    # The test folder's manifest was left out before generation.
    assert meta["scope_filter"]["excluded_manifests"] == ["test/package.json"]
    assert meta["scope_filter"]["kept"] == 1
    # scancode ran: a window, no skip notice.
    assert "scancode" in meta[so.STAGE_TIMINGS_KEY]
    assert so.SCANCODE_SKIP_KEY not in meta


def test_the_engine_record_is_merged_with_the_persist_paths_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    """The persist path's own registry lookups (still on for what BomLens left
    unlicensed) skipped some too: one record that counts both."""
    import tasks.bomlens_result as adapter
    from tasks.scan_source import persist_sbom_components as real_persist

    def persist_and_skip(session: Session, *, scan_uuid: uuid.UUID, **kwargs: Any) -> None:
        real_persist(session, scan_uuid=scan_uuid, **kwargs)
        scan = session.get(Scan, scan_uuid)
        assert scan is not None
        scan.scan_metadata = {
            **(scan.scan_metadata or {}),
            so.LICENSE_ENRICHMENT_KEY: {
                "budget_seconds": 60.0,
                "spent_seconds": 1.0,
                "lookups": 2,
                "lookup_failures": 2,
                "not_looked_up": 4,
                "not_looked_up_reason": "breaker_open",
            },
        }

    monkeypatch.setattr(adapter, "persist_sbom_components", persist_and_skip)
    scan_id = _seed_running_source_scan()

    adapter.apply_bomlens_result(
        scan_uuid=scan_id, output_dir=_copy_fixture("source-deep-license", tmp_path)
    )

    meta = _metadata(sync_session, scan_id)
    assert so.license_lookup_gap(meta) == (5, "both")
    assert meta[so.LICENSE_ENRICHMENT_KEY]["engines"] == ["inprocess", "bomlens"]


def test_a_licence_scan_over_the_ceiling_is_not_opened(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    """Same ceiling as in process (SCANCODE_MAX_RESULT_BYTES): the scan keeps
    its inventory and says the licence scan was too large."""
    from tasks.bomlens_result import apply_bomlens_result

    monkeypatch.setenv("SCANCODE_MAX_RESULT_BYTES", "1024")
    scan_id = _seed_running_source_scan()

    apply_bomlens_result(
        scan_uuid=scan_id, output_dir=_copy_fixture("source-deep-license", tmp_path)
    )

    assert _detected(sync_session, scan_id) == []
    assert "scancode_result" not in _artifact_kinds(sync_session, scan_id)
    assert _component_count(sync_session, scan_id) == 1
    assert {"stage": "scancode", "reason": "too_large"} in so.degraded_stages(
        _metadata(sync_session, scan_id)
    )


def test_a_licence_scan_larger_than_declared_is_not_read_past_the_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    """The manifest says 1000 bytes; the file is 9231. Read up to the scancode
    ceiling and stopped there: a degraded licence scan, not the whole file in
    memory and then a contract error that fails the scan."""
    from tasks.bomlens_result import apply_bomlens_result

    monkeypatch.setenv("SCANCODE_MAX_RESULT_BYTES", "4096")
    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("source-deep-license", tmp_path)

    def _declare_small(manifest: dict[str, Any]) -> None:
        for output in manifest["outputs"]:
            if output["role"] == "license-scan.scancode":
                output["bytes"] = 1000

    _rewrite_manifest(folder, _declare_small)

    apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)

    assert _detected(sync_session, scan_id) == []
    assert _component_count(sync_session, scan_id) == 1
    assert {"stage": "scancode", "reason": "too_large"} in so.degraded_stages(
        _metadata(sync_session, scan_id)
    )


def test_a_scancode_ceiling_above_the_engine_ceiling_still_degrades(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    """SCANCODE_MAX_RESULT_BYTES set above MAX_OUTPUT_BYTES (here the engine
    ceiling is lowered to stand for a file over 256 MiB): the licence scan
    degrades as too_large instead of failing the scan.

    The captured licence scan is smaller than the Trivy report, so this copy
    pads it with trailing whitespace (still the same JSON) and records the new
    size and digest, putting it alone over the lowered ceiling."""
    import hashlib

    from integrations.bomlens import contract
    from tasks.bomlens_result import apply_bomlens_result

    folder = _copy_fixture("source-deep-license", tmp_path)
    path = folder / "mixed_1.0.0_scancode.json"
    padded = path.read_bytes() + b" " * 40_000
    path.write_bytes(padded)

    def _record_padding(manifest: dict[str, Any]) -> None:
        for output in manifest["outputs"]:
            if output["role"] == "license-scan.scancode":
                output.update(bytes=len(padded), sha256=hashlib.sha256(padded).hexdigest())

    _rewrite_manifest(folder, _record_padding)
    monkeypatch.setenv("SCANCODE_MAX_RESULT_BYTES", str(2**30))
    monkeypatch.setattr(contract, "MAX_OUTPUT_BYTES", 30_000)
    scan_id = _seed_running_source_scan()

    apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)

    assert _detected(sync_session, scan_id) == []
    assert {"stage": "scancode", "reason": "too_large"} in so.degraded_stages(
        _metadata(sync_session, scan_id)
    )


def test_only_the_checked_bytes_of_the_licence_scan_are_kept(tmp_path: Path) -> None:
    from integrations.bomlens.contract import load_manifest
    from tasks.bomlens_result import _read_license_scan

    folder = _copy_fixture("source-deep-license", tmp_path)
    scan = _read_license_scan(load_manifest(folder), folder)

    assert scan.verified is not None
    assert scan.verified.document == {}
    assert scan.verified.data == (folder / "mixed_1.0.0_scancode.json").read_bytes()
    assert len(scan.detections) == 3


def test_a_symlink_planted_where_the_copy_goes_is_refused(
    tmp_path: Path, sync_session: Session
) -> None:
    """The kept copy is written without following a link, so a link planted at
    its path cannot redirect the bytes to another file."""
    from tasks.bomlens_result import apply_bomlens_result

    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("source-ok", tmp_path)
    keep = tmp_path / "kept"
    keep.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched", encoding="utf-8")
    (keep / "node_1.0.0_bom.json").symlink_to(victim)

    with pytest.raises(BomLensContractError, match="symbolic link"):
        apply_bomlens_result(scan_uuid=scan_id, output_dir=folder, artifact_dir=keep)

    assert victim.read_text(encoding="utf-8") == "untouched"
    assert "sbom_cyclonedx" not in _artifact_kinds(sync_session, scan_id)


def test_a_licence_scan_that_ran_but_wrote_nothing_is_a_failed_stage(
    tmp_path: Path, sync_session: Session
) -> None:
    from tasks.bomlens_result import apply_bomlens_result

    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("source-deep-license", tmp_path)
    _rewrite_manifest(
        folder,
        lambda m: m.update(
            outputs=[o for o in m["outputs"] if o["role"] != "license-scan.scancode"]
        ),
    )

    apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)

    assert _detected(sync_session, scan_id) == []
    assert {"stage": "scancode", "reason": "failed"} in so.degraded_stages(
        _metadata(sync_session, scan_id)
    )


def test_a_tampered_licence_scan_is_refused_before_anything_is_written(
    tmp_path: Path, sync_session: Session
) -> None:
    """The licence scan is engine output like the SBOM: a file that does not
    match its recorded digest ends the scan, and nothing lands."""
    from tasks.bomlens_result import apply_bomlens_result

    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("source-deep-license", tmp_path)
    path = folder / "mixed_1.0.0_scancode.json"
    content = path.read_text(encoding="utf-8")
    path.write_text(content.replace("MPL-2.0", "MIT-0.0", 1), encoding="utf-8")

    with pytest.raises(BomLensContractError, match="license-scan.scancode"):
        apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)
    assert _component_count(sync_session, scan_id) == 0
    assert _detected(sync_session, scan_id) == []


def test_a_detection_that_cannot_be_written_degrades_only_that_stage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    """The in-process savepoint: a failure writing detected licences rolls back
    only them. The inventory and findings stay and the stage says so."""
    from sqlalchemy.exc import IntegrityError

    import tasks.scan_source as scan_source
    from tasks.bomlens_result import apply_bomlens_result

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise IntegrityError("insert", {}, Exception("constraint"))

    monkeypatch.setattr(scan_source, "_persist_detected_licenses", _boom)
    scan_id = _seed_running_source_scan()

    applied = apply_bomlens_result(
        scan_uuid=scan_id, output_dir=_copy_fixture("source-deep-license", tmp_path)
    )

    assert applied.detections_persisted == 0
    assert _detected(sync_session, scan_id) == []
    assert _component_count(sync_session, scan_id) == 1
    assert len(_finding_ids(sync_session, scan_id)) == 3
    meta = _metadata(sync_session, scan_id)
    assert {"stage": "detected_licenses", "reason": "failed"} in so.degraded_stages(meta)
    # The records written in the same transaction as the components survive.
    assert so.license_lookup_gap(meta) == (1, "budget_exhausted")


def test_a_source_that_declared_dependencies_and_yielded_none(
    tmp_path: Path, sync_session: Session
) -> None:
    """Lock-less Poetry project with only a ranged pin: BomLens says generate
    degraded (no_components), and TRUSCA records it under that reason and
    reads the empty result as empty_with_manifests even with no inventory."""
    from tasks.bomlens_result import apply_bomlens_result, bomlens_component_outcome

    scan_id = _seed_running_source_scan()

    applied = apply_bomlens_result(
        scan_uuid=scan_id, output_dir=_copy_fixture("source-no-components", tmp_path)
    )

    meta = _metadata(sync_session, scan_id)
    assert meta[so.STAGE_OUTCOMES_KEY] == {"generate": {"reason": "no_components"}}
    assert _component_count(sync_session, scan_id) == 0
    assert applied.declared_but_empty
    assert bomlens_component_outcome(applied, manifest_inventory=None) == (so.EMPTY_WITH_MANIFESTS)


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


def test_a_report_without_results_fails_instead_of_reading_as_zero_cves(
    tmp_path: Path, sync_session: Session
) -> None:
    """M1, on the derived fixture: the real report of source-ok with Results
    removed. Schema 2, the right artifact type, scan-security ok, and still not
    a result; persisted, it would read as zero CVEs and pass the gate."""
    from tasks.bomlens_result import apply_bomlens_result

    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("source-ok-results-stripped", tmp_path)

    with pytest.raises(BomLensVulnerabilityScanFailed, match="no Results list"):
        apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)

    assert _component_count(sync_session, scan_id) == 1
    assert _finding_ids(sync_session, scan_id) == []
    assert set(_artifact_kinds(sync_session, scan_id)) == {"sbom_cyclonedx"}


def test_a_file_rewritten_after_it_was_read_is_not_what_gets_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    """M2: the SBOM in the engine's folder is replaced after the adapter read
    it (while components are being persisted). The artifact kept for the scan
    is still the checked bytes, not the replacement."""
    import tasks.bomlens_result as adapter

    scan_id = _seed_running_source_scan()
    folder = _copy_fixture("source-ok", tmp_path)
    bom = folder / "node_1.0.0_bom.json"
    checked = _sha256(bom)
    from tasks.scan_source import persist_sbom_components as real_persist

    def persist_then_swap(*args: Any, **kwargs: Any) -> None:
        real_persist(*args, **kwargs)
        bom.write_text('{"bomFormat": "CycloneDX", "components": []}', encoding="utf-8")

    monkeypatch.setattr(adapter, "persist_sbom_components", persist_then_swap)

    adapter.apply_bomlens_result(scan_uuid=scan_id, output_dir=folder)

    kept = _artifact_files(sync_session, scan_id)["sbom_cyclonedx"]
    assert _sha256(bom) != checked
    assert _sha256(kept) == checked


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


def test_a_rerun_does_not_inherit_the_completeness_records(
    tmp_path: Path, sync_session: Session
) -> None:
    """The first run skipped a registry lookup and left a manifest out; the
    re-run did neither. Neither record may survive into the second answer."""
    from tasks.bomlens_result import apply_bomlens_result
    from tasks.scan_source import _mark_running, _reset_scan_for_rerun

    scan_id = _seed_running_source_scan()
    apply_bomlens_result(
        scan_uuid=scan_id, output_dir=_copy_fixture("source-deep-license", tmp_path)
    )
    first = _metadata(sync_session, scan_id)
    assert so.license_lookup_gap(first) is not None
    assert first["scope_filter"]["excluded_manifests"] == ["test/package.json"]

    scan = sync_session.get(Scan, scan_id)
    assert scan is not None
    _reset_scan_for_rerun(sync_session, scan)
    _mark_running(sync_session, scan)

    apply_bomlens_result(
        scan_uuid=scan_id, output_dir=_copy_fixture("source-no-components", tmp_path)
    )

    second = _metadata(sync_session, scan_id)
    assert so.LICENSE_ENRICHMENT_KEY not in second
    assert second["scope_filter"]["excluded_manifests"] == []
    assert _detected(sync_session, scan_id) == []


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
