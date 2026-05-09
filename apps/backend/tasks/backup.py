"""
Backup automation Celery tasks — Phase 6 chore PR #19.

Two tasks:

  - ``trustedoss.backup.run``     — daily auto-backup + manual on-demand.
  - ``trustedoss.backup.restore`` — destructive restore, no autoretry.

Both shell out to ``scripts/{backup,restore}.sh`` rather than reimplementing
``pg_dump`` / ``tar`` in Python. The shell scripts are the authoritative,
operator-tested path; the task is a thin orchestrator that captures stderr,
emits audit rows, and applies retention.

Audit emission:
  - Celery tasks run outside the FastAPI request lifecycle so the audit
    listener has no actor / request_id to bind. We mirror the pattern used
    in :mod:`tasks.dt_orphan_cleanup` and write explicit ``AuditLog`` rows
    via the sync session helper. The listener excludes the audit table
    itself, so direct inserts never recurse.

Subprocess + event loop:
  - The task body is sync. Celery workers run sync tasks on their own thread,
    so blocking on ``subprocess.run`` is fine — the FastAPI event loop is
    completely separate. We do NOT spawn the subprocess from an async
    context.

CLAUDE.md core rule #11 — environment variables are read at call time.
``backup.sh`` reads ``WORKSPACE_HOST_PATH`` itself; we set ``BACKUP_DIR`` per
invocation so the script writes to a deterministic path.
"""

from __future__ import annotations

import json
import os
import subprocess  # noqa: S404 — wrapping operator-trusted shell scripts
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import structlog

from core.db import sync_session_scope
from models import AuditLog
from services.backup_service import (
    BackupNotFoundError,
    backups_root,
    get_backup_path,
    prune_auto_backups,
)
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.backup")

# Resolve the scripts/ directory at call time, not import time.
#
# In the container the package lives at ``/app/tasks/backup.py`` (only 2
# parents above the file) so a hard ``parents[3]`` crashes the import. On
# the host the same file is 3 parents above the repo root. We support both
# layouts plus a ``TRUSTEDOSS_SCRIPTS_DIR`` env override (used by tests and
# by deployments where scripts live elsewhere).
def _scripts_dir() -> Path:
    env_override = os.environ.get("TRUSTEDOSS_SCRIPTS_DIR")
    if env_override:
        return Path(env_override)
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    # Container layout: ``./scripts`` mounted next to ``/app`` (i.e. /app/scripts).
    if len(here.parents) >= 2:
        candidates.append(here.parents[1] / "scripts")
    # Host / monorepo layout: ``<repo>/apps/backend/tasks/backup.py``.
    if len(here.parents) >= 4:
        candidates.append(here.parents[3] / "scripts")
    for c in candidates:
        if (c / "backup.sh").is_file():
            return c
    raise BackupTaskError(
        f"scripts/ directory with backup.sh not found; tried: {candidates}"
    )


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class BackupTaskError(Exception):
    """Raised when ``backup.sh`` exits non-zero or the manifest is malformed."""


class RestoreTaskError(Exception):
    """Raised when ``restore.sh`` exits non-zero or pre-flight validation fails."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_stamp(*, now: datetime | None = None) -> str:
    """Return ``YYYYMMDDTHHMMSSZ`` (matches the backup-name regex)."""
    base = now if now is not None else datetime.now(tz=UTC)
    return base.strftime("%Y%m%dT%H%M%SZ")


def _emit_audit(
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    target_id: str | None,
    diff: dict[str, Any],
) -> None:
    """Write an ``AuditLog`` row from inside a Celery task."""
    with sync_session_scope() as session:
        row = AuditLog(
            actor_user_id=actor_user_id,
            team_id=None,
            target_table="backups",
            target_id=target_id,
            action=action,
            request_id=None,
            ip=None,
            user_agent="celery/trustedoss.backup",
            diff=diff,
        )
        session.add(row)
        session.commit()


def _read_manifest(name: str) -> dict[str, Any]:
    """Read a backup directory's ``manifest.json`` after the script has run."""
    path = backups_root() / name / "manifest.json"
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _dir_size_bytes(name: str) -> int:
    """Sum sizes of files under ``backups/<name>``."""
    root = backups_root() / name
    if not root.is_dir():
        return 0
    total = 0
    for entry in root.rglob("*"):
        if entry.is_file() and not entry.is_symlink():
            try:
                total += entry.stat().st_size
            except OSError:
                continue
    return total


def _coerce_actor(actor_user_id: str | None) -> uuid.UUID | None:
    """Parse the JSON-serialised actor uuid (Celery passes strings)."""
    if actor_user_id is None:
        return None
    try:
        return uuid.UUID(str(actor_user_id))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# run_backup_task — auto + manual
# ---------------------------------------------------------------------------


def _run_backup(
    self: Any,
    *,
    kind: Literal["auto", "manual"],
    actor_user_id: str | None,
) -> dict[str, Any]:
    """Underlying body — split out so unit tests can call directly."""
    structlog.contextvars.bind_contextvars(
        task_name="backup.run",
        task_id=str(self.request.id) if self and self.request else None,
        kind=kind,
    )
    actor_uuid = _coerce_actor(actor_user_id)
    name = f"{kind}-{_utc_stamp()}"
    backup_dir = backups_root() / name

    env = dict(os.environ)
    # ``backup.sh`` honours BACKUP_DIR (added in chore PR #19) and cd's to
    # the repo root before resolving relative paths.
    env["BACKUP_DIR"] = str(backup_dir)

    scripts_dir = _scripts_dir()
    try:
        completed = subprocess.run(  # noqa: S603 — shell=False, fixed argv
            ["bash", str(scripts_dir / "backup.sh")],  # noqa: S607 — bash via PATH; argv is repo-controlled
            cwd=str(scripts_dir.parent),
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=int(os.getenv("BACKUP_SUBPROCESS_TIMEOUT", "3600")),
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "")[-2048:]
        log.error(
            "admin.backup.script_failed",
            name=name,
            kind=kind,
            returncode=exc.returncode,
            stderr_tail=stderr,
        )
        _emit_audit(
            actor_user_id=actor_uuid,
            action="backup.failed",
            target_id=name,
            diff={"kind": kind, "returncode": exc.returncode, "stderr_tail": stderr},
        )
        raise BackupTaskError(f"backup.sh exited {exc.returncode}: {stderr}") from exc
    except subprocess.TimeoutExpired as exc:
        log.error("admin.backup.script_timeout", name=name, kind=kind)
        _emit_audit(
            actor_user_id=actor_uuid,
            action="backup.failed",
            target_id=name,
            diff={"kind": kind, "error": "timeout"},
        )
        raise BackupTaskError(f"backup.sh timed out after {exc.timeout}s") from exc
    finally:
        structlog.contextvars.unbind_contextvars("task_name", "task_id", "kind")

    log.info(
        "admin.backup.script_succeeded",
        name=name,
        kind=kind,
        stdout_tail=(completed.stdout or "")[-512:],
    )

    manifest = _read_manifest(name)
    size_bytes = _dir_size_bytes(name)
    db_revision = manifest.get("alembic_head")

    _emit_audit(
        actor_user_id=actor_uuid,
        action="backup.completed",
        target_id=name,
        diff={
            "kind": kind,
            "size_bytes": size_bytes,
            "db_revision": db_revision,
        },
    )

    # Retention pass — auto only. Manual backups are explicitly never pruned
    # by the daily task.
    pruned = prune_auto_backups()
    for pruned_name in pruned:
        _emit_audit(
            actor_user_id=None,
            action="backup.pruned",
            target_id=pruned_name,
            diff={"reason": "retention", "retention_days": 7},
        )

    return {
        "name": name,
        "size_bytes": size_bytes,
        "db_revision": db_revision,
        "pruned": pruned,
    }


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.backup.run",
    bind=True,
    autoretry_for=(BackupTaskError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=1,
)
def run_backup_task(
    self: Any,
    *,
    kind: Literal["auto", "manual"] = "auto",
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Execute ``scripts/backup.sh`` and apply 7-day retention to ``auto-*``.

    Args:
        kind: ``auto`` (Beat-driven) or ``manual`` (admin-triggered).
        actor_user_id: UUID string of the operator for manual runs; None for
            scheduled runs (the audit row's ``actor_user_id`` stays NULL).

    Returns a summary dict ``{name, size_bytes, db_revision, pruned}``.
    """
    return _run_backup(self, kind=kind, actor_user_id=actor_user_id)


# ---------------------------------------------------------------------------
# restore_backup_task — destructive, no autoretry
# ---------------------------------------------------------------------------


def _run_restore(
    self: Any,
    *,
    name: str,
    actor_user_id: str,
) -> dict[str, Any]:
    """Underlying body — split out so unit tests can call directly."""
    structlog.contextvars.bind_contextvars(
        task_name="backup.restore",
        task_id=str(self.request.id) if self and self.request else None,
    )
    actor_uuid = _coerce_actor(actor_user_id)

    try:
        backup_path = get_backup_path(name)
    except BackupNotFoundError as exc:
        log.error("admin.backup.restore_target_missing", name=name, error=str(exc))
        _emit_audit(
            actor_user_id=actor_uuid,
            action="backup.restore_failed",
            target_id=name,
            diff={"error": "not_found"},
        )
        raise RestoreTaskError(f"backup not found: {name}") from exc

    # Pre-flight: the three artifacts must be present. ``workspace.tar.gz``
    # is technically optional in the script, but our admin-restore contract
    # requires it (manifest comes from the same script that always writes
    # all three).
    for artifact in ("postgres.sql.gz", "manifest.json"):
        if not (backup_path / artifact).is_file():
            log.error(
                "admin.backup.restore_artifact_missing",
                name=name,
                artifact=artifact,
            )
            _emit_audit(
                actor_user_id=actor_uuid,
                action="backup.restore_failed",
                target_id=name,
                diff={"error": "incomplete_artifacts", "missing": artifact},
            )
            raise RestoreTaskError(f"missing artifact: {artifact}")

    manifest_before = _read_manifest(name)
    revision_before = manifest_before.get("alembic_head")

    env = dict(os.environ)
    env["BACKUP_RESTORE_CONFIRM"] = "yes"

    scripts_dir = _scripts_dir()
    try:
        subprocess.run(  # noqa: S603 — shell=False, fixed argv
            ["bash", str(scripts_dir / "restore.sh"), str(backup_path)],  # noqa: S607 — bash via PATH; argv is repo-controlled
            cwd=str(scripts_dir.parent),
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=int(os.getenv("BACKUP_SUBPROCESS_TIMEOUT", "3600")),
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "")[-2048:]
        log.error(
            "admin.backup.restore_failed",
            name=name,
            returncode=exc.returncode,
            stderr_tail=stderr,
        )
        _emit_audit(
            actor_user_id=actor_uuid,
            action="backup.restore_failed",
            target_id=name,
            diff={"returncode": exc.returncode, "stderr_tail": stderr},
        )
        raise RestoreTaskError(f"restore.sh exited {exc.returncode}: {stderr}") from exc
    except subprocess.TimeoutExpired as exc:
        log.error("admin.backup.restore_timeout", name=name)
        _emit_audit(
            actor_user_id=actor_uuid,
            action="backup.restore_failed",
            target_id=name,
            diff={"error": "timeout"},
        )
        raise RestoreTaskError(f"restore.sh timed out after {exc.timeout}s") from exc
    finally:
        structlog.contextvars.unbind_contextvars("task_name", "task_id")

    # Re-read the manifest after the restore — it should be identical to the
    # pre-flight read but we surface the comparison so the audit trail can
    # detect drift (e.g. if the operator hand-edited the file between
    # validation and execution).
    manifest_after = _read_manifest(name)
    revision_after = manifest_after.get("alembic_head")

    diff: dict[str, Any] = {
        "revision_before": revision_before,
        "revision_after": revision_after,
    }
    if revision_before != revision_after:
        log.warning(
            "admin.backup.restore_revision_mismatch",
            name=name,
            revision_before=revision_before,
            revision_after=revision_after,
        )
        diff["mismatch"] = True

    _emit_audit(
        actor_user_id=actor_uuid,
        action="backup.restored",
        target_id=name,
        diff=diff,
    )

    return {
        "name": name,
        "revision_before": revision_before,
        "revision_after": revision_after,
        "mismatch": revision_before != revision_after,
    }


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.backup.restore",
    bind=True,
    # No autoretry: restore is destructive (drops the live DB before
    # reloading). Re-running on a transient subprocess hiccup would compound
    # the damage — the operator must inspect logs and re-trigger explicitly.
    max_retries=0,
)
def restore_backup_task(
    self: Any,
    *,
    name: str,
    actor_user_id: str,
) -> dict[str, Any]:
    """Execute ``scripts/restore.sh`` non-interactively.

    Args:
        name: Backup directory name; validated against the regex via
            :func:`services.backup_service.get_backup_path`.
        actor_user_id: UUID string of the operator. Required — anonymous
            restores are never allowed.

    Returns a summary dict ``{name, revision_before, revision_after,
    mismatch}``. The task does NOT raise on revision drift; it logs a warning
    and emits an audit row with ``mismatch=True``.
    """
    return _run_restore(self, name=name, actor_user_id=actor_user_id)


__all__ = [
    "BackupTaskError",
    "RestoreTaskError",
    "_run_backup",
    "_run_restore",
    "restore_backup_task",
    "run_backup_task",
]
