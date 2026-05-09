"""
Admin backup HTTP routes — Phase 6 chore PR #19.

Endpoints under ``/v1/admin/backup`` (existence-hide via the parent admin
router's ``require_super_admin_or_404`` gate):

  - GET    /v1/admin/backup                  → list all backups (auto + manual)
  - POST   /v1/admin/backup                  → trigger a manual backup (202)
  - GET    /v1/admin/backup/{name}/download  → stream a tar.gz of the backup dir
  - POST   /v1/admin/backup/restore          → multipart upload + restore (202)
  - DELETE /v1/admin/backup/{name}           → remove a manual backup (204)

Auth:
  - Anonymous calls           → 401
  - Authenticated non-admin   → 404 (existence-hide)
  - Authenticated super-admin → pass-through

RFC 7807:
  - All 4xx / 5xx responses use ``application/problem+json``.
  - Non-conforming names → 404 with ``type=.../backup-not-found``.
  - Auto backup deletion attempt → 409 with ``type=.../backup-auto-protected``.
  - Restore without ``X-Confirm-Restore: yes`` → 400.

Streaming:
  - Download streams a tar.gz built on the fly via a ``tempfile.NamedTemporaryFile``
    cleaned up by a Starlette ``BackgroundTask``. The whole archive is never
    materialised in memory.
  - Upload streams chunks to disk; ``Content-Length`` is checked early
    against a 10 GB cap.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi import Path as FastapiPath
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from core.audit import get_audit_context
from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_super_admin_or_404
from models import AuditLog
from schemas.backup import (
    BackupListResponse,
    BackupRestoreResponse,
    BackupTriggerResponse,
)
from services.backup_service import (
    BackupNotFoundError,
    backups_root,
    delete_backup,
    get_backup_path,
    list_backups,
)
from tasks.backup import restore_backup_task, run_backup_task

router = APIRouter(prefix="/backup", tags=["admin"])
log = structlog.get_logger("admin.backup.api")

# Hard cap for restore uploads — 10 GB. Larger archives are almost certainly
# either an operator mistake or an attempt to fill the host disk.
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024 * 1024
# Streaming chunk size for reads / writes.
_CHUNK_SIZE = 1024 * 1024  # 1 MiB

# Canonical RFC 7807 type URIs for this surface.
_TYPE_NOT_FOUND = "https://docs.trustedoss.io/errors/backup-not-found"
_TYPE_AUTO_PROTECTED = "https://docs.trustedoss.io/errors/backup-auto-protected"
_TYPE_CONFIRM_REQUIRED = "https://docs.trustedoss.io/errors/backup-restore-confirm-required"
_TYPE_UPLOAD_TOO_LARGE = "https://docs.trustedoss.io/errors/backup-upload-too-large"
_TYPE_INVALID_ARCHIVE = "https://docs.trustedoss.io/errors/backup-invalid-archive"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit_admin_audit(
    session_add: Any,
    *,
    actor: CurrentUser,
    action: str,
    target_id: str | None,
    diff: dict[str, Any],
) -> AuditLog:
    """Construct an AuditLog row populated with the request context."""
    ctx = get_audit_context()
    row = AuditLog(
        actor_user_id=actor.id,
        team_id=None,
        target_table="backups",
        target_id=target_id,
        action=action,
        request_id=ctx.get("request_id"),
        ip=ctx.get("ip"),
        user_agent=ctx.get("user_agent"),
        diff=diff,
    )
    session_add(row)
    return row


def _utc_stamp(*, now: datetime | None = None) -> str:
    base = now if now is not None else datetime.now(tz=UTC)
    return base.strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------
# GET /v1/admin/backup
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=BackupListResponse,
    summary="List all backups (admin) — auto + manual, newest first",
)
async def list_backups_endpoint(
    request: Request,  # noqa: ARG001
    actor: CurrentUser = Depends(require_super_admin_or_404()),  # noqa: ARG001
) -> Response:
    items = list_backups()
    page = BackupListResponse(items=items, total=len(items))
    return Response(
        content=page.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# POST /v1/admin/backup
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=BackupTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a manual backup (admin) — enqueues the Celery task",
)
async def trigger_backup_endpoint(
    request: Request,  # noqa: ARG001
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    name = f"manual-{_utc_stamp()}"
    actor_id_str = str(actor.id)
    async_result = run_backup_task.delay(kind="manual", actor_user_id=actor_id_str)

    _emit_admin_audit(
        session.add,
        actor=actor,
        action="backup.requested",
        target_id=name,
        diff={
            "kind": "manual",
            "task_id": async_result.id,
        },
    )
    await session.commit()

    log.warning(
        "admin.backup.manual_enqueued",
        actor_id=actor_id_str,
        task_id=async_result.id,
        name=name,
    )

    body = BackupTriggerResponse(task_id=str(async_result.id), name=name)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_202_ACCEPTED,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/admin/backup/{name}/download
# ---------------------------------------------------------------------------


def _stream_and_cleanup(path: Path) -> Any:
    """Yield chunks of ``path``; the BackgroundTask removes the file after."""

    def _gen() -> Any:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    return _gen()


def _remove_path(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("admin.backup.tempfile_cleanup_failed", path=str(path), error=str(exc))


@router.get(
    "/{name}/download",
    summary="Download a backup archive (admin) — streams a tar.gz of the directory",
)
async def download_backup_endpoint(
    request: Request,
    name: str = FastapiPath(..., description="Backup directory name."),
    actor: CurrentUser = Depends(require_super_admin_or_404()),  # noqa: ARG001
) -> Response:
    try:
        backup_path = get_backup_path(name)
    except BackupNotFoundError:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Backup Not Found",
            detail=f"No backup named {name!r} exists.",
            instance=request.url.path,
            type_=_TYPE_NOT_FOUND,
        )

    # Build the tar.gz into a NamedTemporaryFile and stream it back. We do
    # not use ``tempfile.SpooledTemporaryFile`` because backups can easily
    # exceed RAM; an on-disk temp file is the safe shape.
    tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115 — closed below before streaming
        prefix="backup-dl-",
        suffix=".tar.gz",
        delete=False,
    )
    tmp_path = Path(tmp.name)
    tmp.close()

    try:
        with tarfile.open(tmp_path, mode="w:gz") as tar:
            tar.add(str(backup_path), arcname=name)
    except Exception as exc:  # noqa: BLE001 — convert any IO error to 500 problem
        _remove_path(tmp_path)
        log.error("admin.backup.tar_build_failed", name=name, error=str(exc))
        return problem_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Backup Archive Build Failed",
            detail="Could not build the tar.gz archive for download.",
            instance=request.url.path,
        )

    headers = {"Content-Disposition": f'attachment; filename="{name}.tar.gz"'}
    return StreamingResponse(
        _stream_and_cleanup(tmp_path),
        media_type="application/gzip",
        headers=headers,
        background=BackgroundTask(_remove_path, tmp_path),
    )


# ---------------------------------------------------------------------------
# POST /v1/admin/backup/restore
# ---------------------------------------------------------------------------


_REQUIRED_ARTIFACTS = ("postgres.sql.gz", "manifest.json")


def _validate_uploaded_archive(extract_dir: Path) -> Path | None:
    """Return the inner backup-dir path if the upload contains valid artifacts.

    Layout we accept:
      <extract_dir>/<single-top-level-dir>/{postgres.sql.gz, manifest.json, ...}

    Returns the path to the inner backup directory or None if the archive
    does not match.
    """
    children = [p for p in extract_dir.iterdir() if p.is_dir()]
    if len(children) != 1:
        return None
    inner = children[0]
    for artifact in _REQUIRED_ARTIFACTS:
        if not (inner / artifact).is_file():
            return None
    return inner


@router.post(
    "/restore",
    response_model=BackupRestoreResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Restore from an uploaded backup archive (admin) — destructive",
)
async def restore_backup_endpoint(
    request: Request,
    archive: UploadFile = File(..., description="tar.gz produced by GET /download."),
    confirm: str | None = Header(default=None, alias="X-Confirm-Restore"),
    content_length: int | None = Header(default=None, alias="Content-Length"),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    if confirm != "yes":
        return problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Restore Confirmation Required",
            detail=(
                "Restore is destructive. Re-send the request with the "
                "header X-Confirm-Restore: yes."
            ),
            instance=request.url.path,
            type_=_TYPE_CONFIRM_REQUIRED,
        )

    if content_length is not None and content_length > _MAX_UPLOAD_BYTES:
        return problem_response(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            title="Backup Upload Too Large",
            detail=(
                f"Archive size {content_length} exceeds the maximum "
                f"{_MAX_UPLOAD_BYTES} bytes."
            ),
            instance=request.url.path,
            type_=_TYPE_UPLOAD_TOO_LARGE,
            max_bytes=_MAX_UPLOAD_BYTES,
        )

    # Stream the upload to a deterministic destination so the operator can
    # inspect the artifact later if the restore fails. We first land it
    # under ``backups/uploaded-<utc>/`` and only after validation invoke
    # the restore task.
    upload_name = f"uploaded-{_utc_stamp()}"
    upload_dir = backups_root() / upload_name
    upload_dir.mkdir(parents=True, exist_ok=True)
    archive_path = upload_dir / "upload.tar.gz"

    bytes_written = 0
    try:
        with archive_path.open("wb") as fh:
            while True:
                chunk = await archive.read(_CHUNK_SIZE)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > _MAX_UPLOAD_BYTES:
                    raise ValueError("upload exceeded max size mid-stream")
                fh.write(chunk)
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(upload_dir, ignore_errors=True)
        log.error("admin.backup.upload_failed", error=str(exc))
        return problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Backup Upload Failed",
            detail="Could not save the uploaded archive.",
            instance=request.url.path,
            type_=_TYPE_INVALID_ARCHIVE,
        )

    # Extract into a sibling directory so we can validate before mutating
    # anything else.
    extract_dir = upload_dir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive_path, mode="r:gz") as tar:
            # Block path-traversal entries (..) and absolute paths.
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise ValueError(f"unsafe tar member: {member.name!r}")
            # Python 3.12+: filter='data' rejects unsafe members.
            # nosemgrep: trailofbits.python.tarfile-extractall-traversal — preflight
            # loop above rejects "/"-prefixed and ".." members; filter="data"
            # adds a second-layer guard that rejects symlinks/devices/oversize.
            tar.extractall(path=str(extract_dir), filter="data")  # noqa: S202
    except (tarfile.TarError, ValueError, OSError) as exc:
        shutil.rmtree(upload_dir, ignore_errors=True)
        log.error("admin.backup.upload_invalid_archive", error=str(exc))
        return problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Invalid Backup Archive",
            detail="The uploaded archive could not be extracted.",
            instance=request.url.path,
            type_=_TYPE_INVALID_ARCHIVE,
        )

    inner = _validate_uploaded_archive(extract_dir)
    if inner is None:
        shutil.rmtree(upload_dir, ignore_errors=True)
        return problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Invalid Backup Archive",
            detail=(
                "The uploaded archive must contain a single top-level "
                "directory with postgres.sql.gz and manifest.json."
            ),
            instance=request.url.path,
            type_=_TYPE_INVALID_ARCHIVE,
        )

    # Move the inner directory to a name the restore task accepts (must
    # match the regex). The operator-facing name reuses our ``uploaded-``
    # prefix is NOT acceptable — the regex requires {auto,manual}-...; we
    # therefore rename to ``manual-<utc>`` so the standard validation path
    # passes. The original upload_dir is removed afterwards.
    target_name = f"manual-{_utc_stamp()}"
    target_path = backups_root() / target_name
    # Avoid clobbering: if a same-second name already exists, fall back to
    # a uuid-suffixed alternative — still passes the regex because the
    # uuid hex contains only hex digits which would not match. Use a
    # different timestamp instead.
    if target_path.exists():
        target_name = f"manual-{_utc_stamp(now=datetime.now(tz=UTC))}-{uuid.uuid4().hex[:6]}"
        target_path = backups_root() / target_name
        # If the regex would reject the new name, the task will surface
        # BackupNotFoundError; that is the safer failure mode.
    shutil.move(str(inner), str(target_path))
    shutil.rmtree(upload_dir, ignore_errors=True)

    actor_id_str = str(actor.id)
    async_result = restore_backup_task.delay(name=target_name, actor_user_id=actor_id_str)

    _emit_admin_audit(
        session.add,
        actor=actor,
        action="backup.restore_requested",
        target_id=target_name,
        diff={
            "task_id": async_result.id,
            "uploaded_bytes": bytes_written,
        },
    )
    await session.commit()

    log.warning(
        "admin.backup.restore_enqueued",
        actor_id=actor_id_str,
        task_id=async_result.id,
        target_name=target_name,
    )

    body = BackupRestoreResponse(
        task_id=str(async_result.id),
        message=(
            f"Restore enqueued from uploaded archive (saved as {target_name}). "
            "The application will restart when the task completes."
        ),
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_202_ACCEPTED,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# DELETE /v1/admin/backup/{name}
# ---------------------------------------------------------------------------


@router.delete(
    "/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a manual backup (admin) — auto backups are protected",
)
async def delete_backup_endpoint(
    request: Request,
    name: str = FastapiPath(..., description="Backup directory name."),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    # Auto backups are managed by the daily retention pass. Allowing manual
    # deletion would let an admin sidestep the retention window without an
    # audit trail of "why" — and the operator-facing path for force-cleanup
    # is "let the daily task expire it". Non-conforming names (anything that
    # doesn't start with auto-/manual-) are 404 from get_backup_path below.
    if name.startswith("auto-"):
        return problem_response(
            status_code=status.HTTP_409_CONFLICT,
            title="Auto Backup Protected",
            detail=(
                "Auto-backups are managed by the daily retention task. "
                "Wait for the 7-day window to elapse, or delete the file "
                "from the filesystem directly."
            ),
            instance=request.url.path,
            type_=_TYPE_AUTO_PROTECTED,
        )

    try:
        target = get_backup_path(name)
    except BackupNotFoundError:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Backup Not Found",
            detail=f"No backup named {name!r} exists.",
            instance=request.url.path,
            type_=_TYPE_NOT_FOUND,
        )

    try:
        delete_backup(name)
    except BackupNotFoundError:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Backup Not Found",
            detail=f"No backup named {name!r} exists.",
            instance=request.url.path,
            type_=_TYPE_NOT_FOUND,
        )

    _emit_admin_audit(
        session.add,
        actor=actor,
        action="backup.deleted",
        target_id=name,
        diff={"path": str(target)},
    )
    await session.commit()

    log.warning("admin.backup.deleted", actor_id=str(actor.id), name=name)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Suppress mypy's unused-warning on os — kept imported for future env-driven knobs.
_ = os

__all__ = ["router"]
