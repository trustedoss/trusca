# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""A container image scan on the BomLens engine (``SCAN_CONTAINER_ENGINE=bomlens``, preview).

``tasks.scan_container`` checks the scan row and the registry allow-list for
either engine, then hands an engine scan here. One container does the scan:
the BomLens image in ``MODE=IMAGE`` reads the image from its registry (syft),
reads the Debian packages' licences (Trivy) and matches the SBOM against the
worker's Trivy database (``integrations.bomlens.runner.BomLensRunner.run_image``).
TRUSCA then persists the result through the same adapter a BomLens source scan
uses (``tasks.bomlens_result.apply_bomlens_result``): components, declared
licences, dependency edges, vulnerabilities, the image OS, stage outcomes, and
the rule that a vulnerability scan that did not end ``ok`` fails the scan.

Progress uses the in-process container stages, in the same order:
``bootstrap``, ``trivy`` (the engine container), ``persist``, ``finalize``.

Registry credentials are not passed to the engine. The in-process path hands
Trivy a ``config.json`` in a folder outside the scan workspace and keeps the
credential out of the scanner's environment (``tasks._registry_auth``). The
engine container can be given neither: its mounts come only from the scan
workspace, which ``scripts/backup.sh`` archives, and an environment variable
would put the credential in the environment of syft and Trivy while they parse
the image, and in the container's configuration. So an image whose registry
has a stored credential is refused here, before any container starts, with a
message that says how to scan it (``SCAN_CONTAINER_ENGINE=inprocess``). An
image on a registry with no stored credential is read anonymously, as the
in-process path reads it.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any, Final

import structlog
from sqlalchemy import select

from core.db import sync_session_scope
from integrations.bomlens import runner as bomlens_runner
from models import Project, RegistryCredential, Scan, Team
from services.registry_allowlist import split_registry_host
from tasks._progress import make_line_callback
from tasks.bomlens_result import apply_bomlens_result

log = structlog.get_logger("tasks.scan_container_bomlens")

#: Where the run's execution record is kept in ``scan_metadata`` (the same key
#: as a BomLens source scan; ``mode`` says which kind of run it was).
ENGINE_RUN_METADATA_KEY: Final = "scan_engine_run"


class RegistryCredentialNotPassed(bomlens_runner.BomLensEngineError):
    """The image's registry has a stored credential the engine does not take."""


def credential_refusal(host: str) -> str:
    """The scan error for an image on a registry with a stored credential.

    Quoted in the administrator guide; a guard test keeps the two equal.
    """
    return (
        f"registry {host!r} has a stored registry credential, and the BomLens engine "
        "does not pass registry credentials to its container; scan this image with "
        "SCAN_CONTAINER_ENGINE=inprocess"
    )


def _stored_credential_exists(scan_uuid: uuid.UUID, image_ref: str) -> bool:
    """Whether the scan's organization stored a credential for the image's registry.

    Only the row's existence is read; the credential is never decrypted on
    this path.
    """
    host = split_registry_host(image_ref)
    with sync_session_scope() as session:
        found = session.execute(
            select(RegistryCredential.id)
            .select_from(Scan)
            .join(Project, Project.id == Scan.project_id)
            .join(Team, Team.id == Project.team_id)
            .join(
                RegistryCredential,
                RegistryCredential.organization_id == Team.organization_id,
            )
            .where(Scan.id == scan_uuid, RegistryCredential.registry_host == host)
            .limit(1)
        ).scalar_one_or_none()
    return found is not None


def _project_identity(scan_uuid: uuid.UUID) -> tuple[str, str]:
    """``PROJECT_NAME`` and ``PROJECT_VERSION`` for BomLens (it names its files after them)."""
    with sync_session_scope() as session:
        scan = session.get(Scan, scan_uuid)
        project = session.get(Project, scan.project_id) if scan is not None else None
        name = project.name if project is not None else "image"
        version = (scan.ref if scan is not None else None) or scan_uuid.hex[:12]
    return name, version


def _record_engine_run(scan_uuid: uuid.UUID, report: bomlens_runner.RunReport) -> None:
    """Keep what the runner did on the scan; best-effort, in its own transaction."""
    try:
        with sync_session_scope() as session:
            scan = session.get(Scan, scan_uuid)
            if scan is None:
                return
            metadata: dict[str, Any] = dict(scan.scan_metadata or {})
            metadata[ENGINE_RUN_METADATA_KEY] = report.as_metadata()
            scan.scan_metadata = metadata
            session.commit()
    except Exception:  # noqa: BLE001 - a record of the run must not fail it
        log.warning("bomlens_run_record_failed", exc_info=True)


def require_running(scan_uuid: uuid.UUID) -> None:
    """Stop when the scan is no longer ``running`` (it was cancelled).

    worker-engine takes no Celery remote control, so a revoke does not reach
    this task; the reaper removes a cancelled scan's container and this check
    keeps the task from writing over the cancellation.
    """
    with sync_session_scope() as session:
        scan = session.get(Scan, scan_uuid)
        status = scan.status if scan is not None else None
    if status != "running":
        raise bomlens_runner.EngineScanCancelled(f"scan is {status}")


def run_bomlens_container_pipeline(
    *,
    scan_uuid: uuid.UUID,
    image_ref: str,
    workspace: Path,
    set_stage: Any,
    mark_succeeded: Any,
) -> None:
    """Scan ``image_ref`` with the BomLens engine and finish the scan.

    ``set_stage`` and ``mark_succeeded`` are the container pipeline's own
    writers (``tasks.scan_container``), so both engines report the same
    stages and percents. Raises a
    :class:`~integrations.bomlens.contract.BomLensError` when the image cannot
    be scanned or its result cannot become a succeeded scan, and
    :class:`~integrations.bomlens.runner.EngineScanCancelled` when the scan
    was cancelled meanwhile.
    """
    # Checked before anything else, the reference first: neither needs Docker.
    target = bomlens_runner.image_target(image_ref)
    if _stored_credential_exists(scan_uuid, image_ref):
        raise RegistryCredentialNotPassed(credential_refusal(split_registry_host(image_ref)))
    if shutil.which("docker") is None:
        raise bomlens_runner.BomLensEngineError(
            "the BomLens engine needs the docker CLI and a Docker endpoint on the worker"
        )
    images = bomlens_runner.resolve_images(deep_license=False)
    name, version = _project_identity(scan_uuid)
    workspace.mkdir(parents=True, exist_ok=True)
    runner = bomlens_runner.BomLensRunner(
        scan_uuid=scan_uuid,
        layout=bomlens_runner.EngineLayout(scan_dir=workspace, source=workspace / "source"),
        images=images,
        project_name=name,
        project_version=version,
        fetch_license=False,
        line_callback=lambda stage: make_line_callback(scan_uuid, stage=stage),
    )

    require_running(scan_uuid)
    set_stage(scan_uuid, "trivy")
    log.info("bomlens_image_scan_start", stage="trivy", image=images.plan.run_ref)
    try:
        output_dir = runner.run_image(target)
    finally:
        _record_engine_run(scan_uuid, runner.report)

    require_running(scan_uuid)
    set_stage(scan_uuid, "persist")
    # The container has exited (run_image guarantees it); the verified copies
    # go to a folder no container ever mounted.
    apply_bomlens_result(
        scan_uuid=scan_uuid,
        output_dir=output_dir,
        source_dir=None,
        artifact_dir=runner.verified_dir(),
    )

    require_running(scan_uuid)
    set_stage(scan_uuid, "finalize")
    mark_succeeded(scan_uuid)


__all__ = [
    "ENGINE_RUN_METADATA_KEY",
    "RegistryCredentialNotPassed",
    "credential_refusal",
    "require_running",
    "run_bomlens_container_pipeline",
]
