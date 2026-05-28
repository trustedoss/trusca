"""Reachability scan Celery task — Go govulncheck call-graph enrichment (v2.3 r1).

This task is a **best-effort enrichment** layered on top of a completed source
scan. It does NOT produce vulnerability findings — those already exist (from the
DT pipeline in ``tasks.scan_source``). Instead it runs Go ``govulncheck`` over
the scanned project's *preserved* source and stamps a reachability signal onto
the findings that govulncheck can speak to:

    reachable = True   the vulnerable symbol is reachable on the call graph
    reachable = False  the analyser ran and the symbol is NOT reachable
    reachable = NULL   not analysed (left untouched)

CLAUDE.md core rule #3: this runs ONLY inside a Celery worker, never inline. It
is dispatched as a follow-up after ``scan_source_task`` succeeds (see
``tasks.enqueue_reachability``), so the user-facing scan is never blocked by it.

Idempotency / safety:
  - Keyed off ``scan_id``. Re-running re-derives every verdict from the preserved
    source and re-applies it with ``UPDATE ... WHERE id = ...`` per finding — no
    duplicate rows are ever created (it only updates three columns on existing
    ``vulnerability_findings``). A second run produces the same result.
  - Each run gets a fresh workspace ``${WORKSPACE_HOST_PATH}/reach-<scan_id>/``
    (a distinct prefix from the source-scan workspace ``<scan_id>/`` so the two
    never collide, even if a stale source scan is still finishing). The tree is
    removed in ``finally`` — the existing workspace cleaner reclaims any orphan
    left by a SIGKILL.
  - Every failure path (no preserved source, not a Go module, govulncheck
    missing / timeout, broken output, DB hiccup) degrades to "leave findings
    NULL" with a WARNING — it NEVER fails the originating scan and never raises
    onto a retry-forever path.

Mapping (govulncheck verdict → vulnerability_findings):
  - govulncheck reports GO-ids with CVE/GHSA *aliases*; the adapter fans a
    verdict onto all of them. DT findings key on ``vulnerabilities.external_id``
    (CVE/GHSA/GO). We update a finding when (a) its component is a Go package
    (purl starts ``pkg:golang/``) AND (b) its vulnerability id has a verdict.
    Findings with no verdict keep ``reachable = NULL``.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select, update

from core.config import workspace_root
from core.db import sync_session_scope
from integrations import govulncheck as gv
from models import (
    Component,
    ComponentVersion,
    Project,
    Scan,
    Vulnerability,
    VulnerabilityFinding,
)
from services.source_preservation_service import scan_source_tarball_path
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.scan_reachability")

# Components we attempt to analyse: Go packages only (r1). r3 widens this.
_GO_PURL_PREFIX = "pkg:golang/"

# Defence-in-depth caps on extracting the preserved tarball. The tarball was
# written by us (source_preservation_service) with sanitised members, but we
# re-validate on the way back out so a tarball tampered with on disk cannot zip
# slip / device / symlink into the worker.
_MAX_EXTRACT_MEMBERS = 200_000
_MAX_EXTRACT_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB uncompressed ceiling


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.scan_reachability",
    bind=True,
)
def scan_reachability_task(self: Any, scan_id: str) -> None:
    """Enrich a completed source scan's findings with Go reachability signal.

    Args:
        scan_id: UUID **string** (Celery JSON serialization compatibility).
    """
    structlog.contextvars.bind_contextvars(
        scan_id=scan_id, task_id=self.request.id, task_kind="reachability"
    )
    try:
        scan_uuid = uuid.UUID(scan_id)
    except ValueError:
        log.error("reachability_invalid_scan_id", scan_id=scan_id)
        return

    workspace = Path(workspace_root()) / f"reach-{scan_uuid}"
    try:
        _run(scan_uuid=scan_uuid, workspace=workspace)
    except SoftTimeLimitExceeded:
        # Reachability is best-effort; a timeout just means "leave findings as
        # they are". We don't mark the scan failed — the scan already succeeded.
        log.warning("reachability_timed_out", scan_id=str(scan_uuid))
    except Exception as exc:  # noqa: BLE001 — enrichment must never crash loudly
        # Any unexpected error degrades to "no reachability signal written".
        # Re-raising would have Celery retry; reachability is not worth a retry
        # storm — the next scan re-derives it.
        log.warning(
            "reachability_unhandled_error", scan_id=str(scan_uuid), error=str(exc)[:300]
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        structlog.contextvars.unbind_contextvars("scan_id", "task_id", "task_kind")


def _run(*, scan_uuid: uuid.UUID, workspace: Path) -> None:
    """Resolve source → run govulncheck → map verdicts onto findings."""
    # 1. Load the scan + project (read-only snapshot of the ids we need).
    with sync_session_scope() as session:
        scan = session.get(Scan, scan_uuid)
        if scan is None:
            log.warning("reachability_missing_scan_row")
            return
        if scan.kind != "source":
            log.info("reachability_skip_non_source", kind=scan.kind)
            return
        project = session.get(Project, scan.project_id)
        if project is None:
            log.warning("reachability_missing_project_row")
            return
        project_id = project.id

    # 2. Resolve the preserved source tarball. Best-effort: a scan whose source
    #    preservation was skipped (quota / too large / non-Git upload that left
    #    no tree) has nothing to analyse — leave findings NULL.
    tarball = scan_source_tarball_path(project_id, scan_uuid)
    if not tarball.is_file():
        log.info(
            "reachability_no_preserved_source",
            scan_id=str(scan_uuid),
            project_id=str(project_id),
        )
        return

    # 3. Extract into a fresh workspace and locate the Go module directory.
    source_dir = workspace / "source"
    if not _safe_extract_tarball(tarball=tarball, target_dir=source_dir):
        return
    module_dir = _find_go_module_dir(source_dir)
    if module_dir is None:
        log.info("reachability_no_go_module", scan_id=str(scan_uuid))
        return

    # 4. Run govulncheck (the adapter never raises — empty result on any skip).
    result = gv.run_govulncheck(module_dir=module_dir)
    if not result.analysed:
        log.info("reachability_not_analysed", scan_id=str(scan_uuid))
        return
    if not result.verdicts:
        # Analysed cleanly but found nothing reachable / present. Nothing to
        # write — every finding legitimately stays NULL.
        log.info("reachability_no_verdicts", scan_id=str(scan_uuid))
        return

    # 5. Map verdicts onto this scan's Go findings.
    updated, reachable = _apply_verdicts(scan_uuid=scan_uuid, verdicts=result.verdicts)
    log.info(
        "reachability_applied",
        scan_id=str(scan_uuid),
        findings_updated=updated,
        findings_reachable=reachable,
    )


# ---------------------------------------------------------------------------
# Verdict → finding mapping
# ---------------------------------------------------------------------------


def _apply_verdicts(
    *, scan_uuid: uuid.UUID, verdicts: dict[str, bool]
) -> tuple[int, int]:
    """Stamp ``reachable`` onto this scan's Go findings; return (updated, reachable).

    For each ``vulnerability_findings`` row in this scan whose component is a Go
    package (purl ``pkg:golang/...``) and whose vulnerability id has a verdict,
    set ``reachable`` / ``reachability_source`` / ``reachability_analyzed_at``.
    Findings with no matching verdict are left untouched (NULL).

    Idempotent: a re-run recomputes the same verdicts and re-applies the same
    UPDATE — no rows are created, the same columns are overwritten in place.
    """
    now = datetime.now(UTC)
    updated = 0
    reachable_count = 0
    with sync_session_scope() as session:
        # Join finding → component_version → component (for the purl filter) and
        # → vulnerability (for the external_id → verdict lookup). One query; we
        # apply the per-row UPDATE by primary key so the write is unambiguous.
        rows = session.execute(
            select(
                VulnerabilityFinding.id,
                Vulnerability.external_id,
            )
            .join(
                ComponentVersion,
                ComponentVersion.id == VulnerabilityFinding.component_version_id,
            )
            .join(Component, Component.id == ComponentVersion.component_id)
            .join(
                Vulnerability,
                Vulnerability.id == VulnerabilityFinding.vulnerability_id,
            )
            .where(VulnerabilityFinding.scan_id == scan_uuid)
            .where(Component.purl.like(f"{_GO_PURL_PREFIX}%"))
        ).all()

        for finding_id, external_id in rows:
            verdict = _lookup_verdict(external_id, verdicts)
            if verdict is None:
                continue  # No govulncheck signal for this CVE — leave NULL.
            session.execute(
                update(VulnerabilityFinding)
                .where(VulnerabilityFinding.id == finding_id)
                .values(
                    reachable=verdict,
                    reachability_source=gv.SOURCE_LABEL,
                    reachability_analyzed_at=now,
                )
            )
            updated += 1
            if verdict:
                reachable_count += 1
        session.commit()
    return updated, reachable_count


def _lookup_verdict(external_id: object, verdicts: dict[str, bool]) -> bool | None:
    """Match a finding's vulnerability id against the (uppercased) verdict map.

    ``vulnerabilities.external_id`` is a CVE / GHSA / GO id; the adapter
    uppercases its keys, so we uppercase the probe too. Returns the verdict or
    ``None`` when there is no signal for this id.
    """
    if not isinstance(external_id, str):
        return None
    return verdicts.get(external_id.strip().upper())


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


def _find_go_module_dir(source_dir: Path) -> Path | None:
    """Return the directory of the project's primary Go module, or None.

    The preserved tarball stores files relative to the original source root, so a
    top-level ``go.mod`` lands directly under ``source_dir``. We prefer that
    root; failing that, the shallowest ``go.mod`` anywhere in the tree (so a Go
    project nested one level down is still found) while skipping vendored /
    testdata copies. Returns None when the tree has no ``go.mod`` (not a Go
    project → nothing for govulncheck to do).
    """
    root_mod = source_dir / "go.mod"
    if root_mod.is_file():
        return source_dir

    best: Path | None = None
    best_depth = 1_000_000
    skip = {"vendor", "testdata", "node_modules", ".git"}
    for go_mod in source_dir.rglob("go.mod"):
        if not go_mod.is_file():
            continue
        rel = go_mod.relative_to(source_dir)
        parts = rel.parts
        if any(p in skip for p in parts):
            continue
        depth = len(parts)
        if depth < best_depth:
            best_depth = depth
            best = go_mod.parent
    return best


def _safe_extract_tarball(*, tarball: Path, target_dir: Path) -> bool:
    """Extract ``tarball`` into ``target_dir``, rejecting hostile members.

    The tarball was written by ``source_preservation_service`` with sanitised
    members, but we re-validate on the way out (defence in depth) so a tarball
    tampered with on disk cannot drive a zip-slip / absolute path / symlink /
    device member, nor a decompression bomb, into the worker.

    Returns ``True`` on a clean extraction, ``False`` (after a WARNING) on any
    rejection / I/O error — the caller then leaves findings NULL.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    base = target_dir.resolve()
    written = 0
    members = 0
    try:
        with tarfile.open(tarball, mode="r:gz") as tar:
            for member in tar:
                members += 1
                if members > _MAX_EXTRACT_MEMBERS:
                    log.warning("reachability_extract_too_many_members")
                    return False
                if not (member.isfile() or member.isdir()):
                    # Skip symlinks / hardlinks / devices / fifos entirely.
                    continue
                dest = (base / member.name).resolve()
                if not _is_within(base, dest):
                    log.warning("reachability_extract_path_escape", name=member.name)
                    return False
                if member.isdir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                written += max(0, int(member.size))
                if written > _MAX_EXTRACT_BYTES:
                    log.warning("reachability_extract_bomb")
                    return False
                dest.parent.mkdir(parents=True, exist_ok=True)
                extracted = tar.extractfile(member)
                if extracted is None:  # pragma: no cover — non-regular slips filter
                    continue
                with extracted, dest.open("wb") as out:
                    shutil.copyfileobj(extracted, out, length=1024 * 1024)
        return True
    except (tarfile.TarError, OSError) as exc:
        log.warning("reachability_extract_failed", error=str(exc)[:300])
        return False


def _is_within(base: Path, target: Path) -> bool:
    """True iff ``target`` is ``base`` or strictly inside it (no path escape)."""
    try:
        if target == base:
            return True
        if not target.is_relative_to(base):
            return False
        return os.path.commonpath([str(base), str(target)]) == str(base)
    except (ValueError, OSError):
        return False


__all__ = ["scan_reachability_task"]
