"""
Backup automation Celery tasks — Phase 6 chore PR #19, refactored
under marathon bundle 3 (D2).

Two tasks:

  - ``trustedoss.backup.run``     — daily auto-backup + manual on-demand.
  - ``trustedoss.backup.restore`` — destructive restore, no autoretry.

D2 refactor: previously these tasks shelled out to ``scripts/{backup,
restore}.sh``. Those scripts in turn called ``docker-compose exec
postgres pg_dump`` from the host, which means the Celery worker
container would have needed ``docker-compose`` AND a Docker socket
mounted — a recursive dependency we do not want. The new path talks to
PostgreSQL directly via ``DATABASE_URL`` using the ``pg_dump`` /
``psql`` binaries shipped in the worker image (``postgresql-client-17``
installed in ``apps/backend/Dockerfile.worker`` for D2).

The host shell scripts (``scripts/backup.sh``, ``scripts/restore.sh``)
remain unchanged — they are the operator-tested manual path. The Celery
task is no longer a thin wrapper around them; it owns the full backup
shape (``postgres.sql.gz`` + ``workspace.tar.gz`` + ``manifest.json``)
end to end.

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
``DATABASE_URL`` is parsed on every call so a connection-string rotation
takes effect without a worker restart.

Security:
  - Database password is passed to ``pg_dump`` / ``psql`` via the
    ``PGPASSWORD`` env var (subprocess env, NOT argv) so it never appears
    in process listings.
  - Workspace tar extraction has a path-traversal guard that refuses
    members whose normalized path escapes the destination directory. tar
    archive size is implicitly bounded by the disk-pressure cap on the
    workspace itself; we additionally cap the per-member size during
    extraction (decompression-bomb guard).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess  # noqa: S404 — pg_dump/psql/alembic with fixed argv, no shell=True
import tarfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse

import structlog

from core.config import database_url
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


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class BackupTaskError(Exception):
    """Raised when pg_dump / tar / manifest write fails."""


class BackupNameCollisionError(BackupTaskError):
    """Raised when ``backups/<kind>-<stamp>`` collides for too long.

    Marathon bundle 4 (R / M4): two manual backups firing in the same UTC
    second would otherwise have written into the same directory and
    silently corrupted each other. We retry up to ``_NAME_COLLISION_MAX_RETRIES``
    times (1 second apart so the next ``_utc_stamp()`` produces a fresh
    name); if every slot in that window is taken the operator gets a
    deterministic 409-shaped error rather than a torn backup.
    """


class RestoreTaskError(Exception):
    """Raised when psql / tar extract / pre-flight validation fails."""


# ---------------------------------------------------------------------------
# DATABASE_URL parsing
# ---------------------------------------------------------------------------


def _pg_connection_args() -> tuple[list[str], dict[str, str]]:
    """Parse ``DATABASE_URL`` into ``pg_dump`` / ``psql`` argv + env.

    Returns ``(["-h", host, "-p", port, "-U", user, "-d", db], {"PGPASSWORD": pw})``.
    The password lands ONLY in the environment so it never appears in
    ``ps`` output. ``DATABASE_URL`` carries the asyncpg driver suffix
    (``postgresql+asyncpg://``) which urlparse handles fine — we just
    strip the driver bit when constructing argv.

    Raises BackupTaskError on a malformed URL so the task fails fast with
    a clear message instead of letting pg_dump emit a cryptic auth error.
    """
    raw = database_url()
    # Scheme hardening (M3): require ``postgres[+driver_alnum]://``. A
    # malformed scheme like ``postgresql:foo://...`` previously slipped
    # past urlparse (scheme→'postgresql') and hit the host-missing
    # branch with a misleading error. Tighten so the failure mode is
    # "unsupported scheme" with the offending value echoed.
    if "://" not in raw:
        raise BackupTaskError("DATABASE_URL missing scheme://")
    scheme_raw, rest = raw.split("://", 1)
    base_scheme, sep, driver = scheme_raw.partition("+")
    if base_scheme not in ("postgres", "postgresql"):
        raise BackupTaskError(
            f"DATABASE_URL has unsupported scheme {scheme_raw!r}; expected postgres / postgresql"
        )
    if ":" in scheme_raw:
        # ``postgresql:foo://...`` — base_scheme parsed as postgresql but
        # the trailing ``:foo`` is not a legal driver suffix.
        raise BackupTaskError(f"malformed DATABASE_URL scheme: {scheme_raw!r}")
    if sep and not driver.replace("_", "").isalnum():
        # ``postgresql+://`` or ``postgresql+evil!``: driver suffix must
        # be a legal Python identifier (asyncpg / psycopg2 / psycopg).
        raise BackupTaskError(f"unsupported driver suffix: {driver!r}")
    parsed = urlparse(f"{base_scheme}://{rest}")
    host = parsed.hostname
    if not host:
        raise BackupTaskError("DATABASE_URL missing host")
    port = str(parsed.port or 5432)
    user = unquote(parsed.username) if parsed.username else "trustedoss"
    password = unquote(parsed.password) if parsed.password else ""
    # Strip the leading slash from the path component to get the DB name.
    db = parsed.path.lstrip("/") or "trustedoss"
    argv = ["-h", host, "-p", port, "-U", user, "-d", db]
    env = {"PGPASSWORD": password}
    return argv, env


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Marathon bundle 4 (R / M4) — slot-allocation retry budget. Each retry
# sleeps ``_NAME_COLLISION_SLEEP_SECONDS`` (1s) so the next _utc_stamp()
# produces a fresh second. 3 retries cover the realistic burst window
# (admin clicks + Beat fire colliding within ~3 seconds); beyond that we
# refuse rather than mis-match audit rows to a torn dir.
_NAME_COLLISION_MAX_RETRIES = 3
_NAME_COLLISION_SLEEP_SECONDS = 1


def _utc_stamp(*, now: datetime | None = None) -> str:
    """Return ``YYYYMMDDTHHMMSSZ`` (matches the backup-name regex)."""
    base = now if now is not None else datetime.now(tz=UTC)
    return base.strftime("%Y%m%dT%H%M%SZ")


def _allocate_backup_slot(
    kind: Literal["auto", "manual"],
    actor_uuid: uuid.UUID | None,
) -> tuple[str, Path]:
    """Pick a fresh ``backups/<kind>-<stamp>`` directory or raise.

    Marathon bundle 4 (R / M4): the previous code used ``mkdir(exist_ok=
    True)`` which silently shared a slot with any concurrent backup
    firing in the same UTC second. We now try ``mkdir(exist_ok=False)``
    and retry on FileExistsError until we get an exclusive slot or run
    out of retries — at which point we emit a backup.failed audit row
    with stage=name_collision and raise BackupNameCollisionError so the
    caller surfaces a deterministic error instead of writing into a
    half-built dir.

    The mkdir is atomic on POSIX (returns EEXIST for the loser of a
    same-second race) so two workers calling this helper concurrently
    serialize correctly: exactly one wins each slot.
    """
    import time

    last_name = ""
    for attempt in range(_NAME_COLLISION_MAX_RETRIES + 1):
        last_name = f"{kind}-{_utc_stamp()}"
        candidate = backups_root() / last_name
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return last_name, candidate
        except FileExistsError:
            log.warning(
                "admin.backup.name_collision_retry",
                name=last_name,
                attempt=attempt,
                max_retries=_NAME_COLLISION_MAX_RETRIES,
            )
            if attempt < _NAME_COLLISION_MAX_RETRIES:
                time.sleep(_NAME_COLLISION_SLEEP_SECONDS)
    _emit_audit(
        actor_user_id=actor_uuid,
        action="backup.failed",
        target_id=last_name,
        diff={
            "kind": kind,
            "stage": "name_collision",
            "error": (
                f"backup slot {last_name!r} taken after "
                f"{_NAME_COLLISION_MAX_RETRIES} retries — another backup is "
                f"likely in progress"
            ),
        },
    )
    raise BackupNameCollisionError(
        f"backup slot {last_name!r} could not be allocated after "
        f"{_NAME_COLLISION_MAX_RETRIES} retries"
    )


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
    """Read a backup directory's ``manifest.json``."""
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


def _sha256_file(path: Path) -> str:
    """Compute the SHA-256 digest of ``path`` (streaming, 64 KiB chunks)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _alembic_head() -> str:
    """Return the alembic head revision via ``alembic current`` subprocess.

    The worker image ships the alembic migrations + config at
    ``/app/alembic/`` so this runs locally without needing
    docker-compose. Empty string on any failure (the manifest just
    records ``"unknown"`` then — operator can still restore).
    """
    backend_root = _backend_root()
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["alembic", "current"],  # noqa: S607 — alembic on PATH inside the worker image
            cwd=str(backend_root),
            env={**os.environ, **_pg_connection_env()},
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.warning("admin.backup.alembic_current_failed", error=str(exc))
        return ""
    # ``alembic current`` prints e.g. ``0013 (head)\n`` or empty for a fresh DB.
    last = (result.stdout or "").strip().splitlines()[-1:] or [""]
    return last[0].split()[0] if last[0] else ""


def _pg_connection_env() -> dict[str, str]:
    """PGPASSWORD-only env snippet for alembic (which reads its own URL)."""
    _, env = _pg_connection_args()
    return env


def _backend_root() -> Path:
    """Return the backend root (where ``alembic.ini`` lives).

    Container layout: ``/app`` contains ``alembic.ini``. Host monorepo
    layout: ``<repo>/apps/backend``.
    """
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    if len(here.parents) >= 2:
        candidates.append(here.parents[1])  # /app
    if len(here.parents) >= 3:
        candidates.append(here.parents[2])  # <repo>/apps/backend
    for c in candidates:
        if (c / "alembic.ini").is_file():
            return c
    # Fall back to cwd; alembic will surface its own error.
    return Path.cwd()


def _workspace_host_path() -> Path:
    """Return the workspace host path (env-driven) — read at call time."""
    return Path(os.getenv("WORKSPACE_HOST_PATH", "/opt/trustedoss/workspace"))


# ---------------------------------------------------------------------------
# pg_dump / psql wrappers
# ---------------------------------------------------------------------------


def _run_pg_dump(target_gz: Path) -> None:
    """Stream ``pg_dump --clean --if-exists`` into ``target_gz`` (gzipped).

    Uses ``--clean --if-exists`` to mirror the legacy script's restore
    semantics: the dump includes ``DROP TABLE IF EXISTS`` for every
    object so a restore can replay onto an existing database without
    pre-cleaning.

    Streaming (M1 fix): we use ``Popen`` + ``shutil.copyfileobj`` rather
    than ``subprocess.run(capture_output=True)`` so the entire SQL dump
    never lands in worker RAM. ``capture_output=True`` would buffer the
    full pg_dump stdout (multi-GB on a real database) before the gzip
    write fires — the worker would OOM-kill on any non-trivial DB. The
    streaming form passes data through a 64 KiB buffer.
    """
    pg_args, pg_env = _pg_connection_args()
    cmd = [
        "pg_dump",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        *pg_args,
    ]
    env = {**os.environ, **pg_env}
    timeout = int(os.getenv("BACKUP_SUBPROCESS_TIMEOUT", "3600"))
    proc: subprocess.Popen[bytes] | None = None
    try:
        with target_gz.open("wb") as raw_out, gzip.GzipFile(fileobj=raw_out, mode="wb") as gz_out:
            proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert proc.stdout is not None
            assert proc.stderr is not None
            try:
                # 64 KiB chunks — bounds RSS regardless of dump size.
                shutil.copyfileobj(proc.stdout, gz_out, length=65536)
                stderr_bytes = proc.stderr.read()
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                proc.wait()
                raise BackupTaskError(f"pg_dump timed out after {exc.timeout}s") from exc
        if rc != 0:
            stderr = stderr_bytes.decode("utf-8", errors="replace")[-2048:]
            raise BackupTaskError(f"pg_dump exited {rc}: {stderr}")
    except FileNotFoundError as exc:
        raise BackupTaskError(
            "pg_dump binary not found on PATH — "
            "rebuild the worker image with postgresql-client-17"
        ) from exc


def _run_psql_restore(source_gz: Path) -> None:
    """Stream gunzipped SQL into ``psql`` to restore the database.

    Streaming (M1 fix): we open the gzip stream lazily and pump it into
    psql's stdin via ``shutil.copyfileobj`` so the full SQL never lands
    in worker RAM. ``--single-transaction`` makes the restore atomic
    (all-or-nothing) — note this holds DB locks for the full duration,
    so a multi-GB restore blocks concurrent writes; document the
    operator implication in admin-guide.
    """
    pg_args, pg_env = _pg_connection_args()
    cmd = ["psql", "--quiet", "--single-transaction", *pg_args]
    env = {**os.environ, **pg_env}
    timeout = int(os.getenv("BACKUP_SUBPROCESS_TIMEOUT", "3600"))
    proc: subprocess.Popen[bytes] | None = None
    try:
        with gzip.open(source_gz, "rb") as gz_in:
            proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
                cmd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert proc.stdin is not None
            assert proc.stderr is not None
            try:
                shutil.copyfileobj(gz_in, proc.stdin, length=65536)
                proc.stdin.close()
                stderr_bytes = proc.stderr.read()
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                proc.wait()
                raise RestoreTaskError(f"psql timed out after {exc.timeout}s") from exc
        if rc != 0:
            stderr = stderr_bytes.decode("utf-8", errors="replace")[-2048:]
            raise RestoreTaskError(f"psql exited {rc}: {stderr}")
    except FileNotFoundError as exc:
        raise RestoreTaskError(
            "psql binary not found on PATH — "
            "rebuild the worker image with postgresql-client-17"
        ) from exc


# ---------------------------------------------------------------------------
# Workspace tar.gz
# ---------------------------------------------------------------------------

# Decompression-bomb guard: a single member cannot exceed 5 GiB and the
# whole archive cannot exceed 50 GiB. Matches the cap the install / disk
# admin docs commit to and protects against a hostile tar that would fill
# the worker's tmpfs / disk during extraction.
_MAX_MEMBER_BYTES = 5 * 1024**3
_MAX_TOTAL_BYTES = 50 * 1024**3


def _create_workspace_archive(target_tar_gz: Path) -> bool:
    """Tar-gz the workspace into ``target_tar_gz``.

    Returns True when a workspace was archived, False when the path
    didn't exist (the operator may not have one yet — backup proceeds
    without the workspace artifact).
    """
    workspace = _workspace_host_path()
    if not workspace.is_dir():
        log.warning("admin.backup.workspace_missing", path=str(workspace))
        return False
    # Use a deterministic arcname (= the workspace dir's basename) so the
    # restore mirrors the legacy script's ``tar -C parent -xzf …`` layout.
    arcname = workspace.name
    with tarfile.open(target_tar_gz, "w:gz") as tf:
        tf.add(str(workspace), arcname=arcname)
    return True


def _extract_workspace_archive(source_tar_gz: Path) -> None:
    """Extract a workspace tar to ``WORKSPACE_HOST_PATH``'s parent.

    Path-traversal guard: rejects any member whose normalised path
    escapes the destination directory. Decompression-bomb guard:
    enforces the per-member and total caps above.
    """
    workspace = _workspace_host_path()
    dest_parent = workspace.parent
    dest_parent.mkdir(parents=True, exist_ok=True)
    # Wipe the existing workspace dir so the extract starts clean
    # (matches restore.sh's ``rm -rf`` step).
    if workspace.exists():
        if workspace.is_symlink() or not workspace.is_dir():
            raise RestoreTaskError(
                f"workspace path is not a regular directory: {workspace}"
            )
        shutil.rmtree(workspace)

    with tarfile.open(source_tar_gz, "r:gz") as tf:
        total = 0
        for member in tf.getmembers():
            # Member size cap.
            if member.size > _MAX_MEMBER_BYTES:
                raise RestoreTaskError(
                    f"workspace member {member.name!r} exceeds {_MAX_MEMBER_BYTES} bytes"
                )
            total += member.size
            if total > _MAX_TOTAL_BYTES:
                raise RestoreTaskError(
                    f"workspace archive total exceeds {_MAX_TOTAL_BYTES} bytes"
                )
            # Path-traversal guard.
            member_dest = (dest_parent / member.name).resolve()
            try:
                member_dest.relative_to(dest_parent.resolve())
            except ValueError as exc:
                raise RestoreTaskError(
                    f"workspace member {member.name!r} escapes destination"
                ) from exc
        # Re-iterate to extract — tarfile.extractall accepts a `filter`
        # callable for member transform/reject; here we already vetted
        # above, so plain extractall with the data filter is fine.
        # Python 3.12+ ``filter='data'`` rejects symlinks and members whose
        # resolved path escapes destination — combined with the explicit
        # path-traversal + size-cap preflight loop above, this is safe.
        # nosemgrep: trailofbits.python.tarfile-extractall-traversal.tarfile-extractall-traversal  # path traversal + size cap enforced in preflight loop above + filter="data"
        tf.extractall(path=str(dest_parent), filter="data")  # noqa: S202


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _write_manifest(
    backup_dir: Path,
    *,
    name: str,
    has_workspace: bool,
    pg_dump_sha256: str,
    workspace_sha256: str | None,
) -> dict[str, Any]:
    """Write ``manifest.json`` and return the dict that was written."""
    manifest: dict[str, Any] = {
        "name": name,
        "timestamp": _utc_stamp(),
        "alembic_head": _alembic_head() or "unknown",
        "workspace_path": str(_workspace_host_path()),
        "has_workspace": bool(has_workspace),
        "checksums": {
            "postgres.sql.gz": pg_dump_sha256,
        },
    }
    if has_workspace and workspace_sha256:
        manifest["checksums"]["workspace.tar.gz"] = workspace_sha256
    target = backup_dir / "manifest.json"
    with target.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
    return manifest


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
    name, backup_dir = _allocate_backup_slot(kind, actor_uuid)
    pg_dump_path = backup_dir / "postgres.sql.gz"
    workspace_path = backup_dir / "workspace.tar.gz"

    try:
        try:
            _run_pg_dump(pg_dump_path)
        except BackupTaskError as exc:
            log.error("admin.backup.pg_dump_failed", name=name, error=str(exc))
            _emit_audit(
                actor_user_id=actor_uuid,
                action="backup.failed",
                target_id=name,
                diff={"kind": kind, "stage": "pg_dump", "error": str(exc)[-512:]},
            )
            # Clean the partial dir so the next run isn't tempted to read
            # a torn manifest. We do NOT swallow the original exception —
            # autoretry_for will pick it back up.
            shutil.rmtree(backup_dir, ignore_errors=True)
            raise

        has_workspace = False
        try:
            has_workspace = _create_workspace_archive(workspace_path)
        except (OSError, tarfile.TarError) as exc:
            log.error("admin.backup.workspace_tar_failed", name=name, error=str(exc))
            _emit_audit(
                actor_user_id=actor_uuid,
                action="backup.failed",
                target_id=name,
                diff={"kind": kind, "stage": "workspace_tar", "error": str(exc)[-512:]},
            )
            shutil.rmtree(backup_dir, ignore_errors=True)
            raise BackupTaskError(f"workspace tar failed: {exc}") from exc

        pg_sha = _sha256_file(pg_dump_path)
        workspace_sha = _sha256_file(workspace_path) if has_workspace else None
        manifest = _write_manifest(
            backup_dir,
            name=name,
            has_workspace=has_workspace,
            pg_dump_sha256=pg_sha,
            workspace_sha256=workspace_sha,
        )
    finally:
        structlog.contextvars.unbind_contextvars("task_name", "task_id", "kind")

    log.info(
        "admin.backup.completed",
        name=name,
        kind=kind,
        has_workspace=has_workspace,
        alembic_head=manifest.get("alembic_head"),
    )

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
            "has_workspace": has_workspace,
            "checksums": manifest.get("checksums", {}),
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
    """Run a backup directly via ``pg_dump`` + tar (no shell delegation).

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

    # Pre-flight: postgres.sql.gz + manifest.json must be present.
    # workspace.tar.gz is optional — backup may have skipped it.
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

    # Optional checksum re-verify before restore — guards against on-disk
    # corruption that would otherwise restore a partially-good dump.
    expected_checksums = manifest_before.get("checksums") or {}
    for fname in ("postgres.sql.gz", "workspace.tar.gz"):
        expected = expected_checksums.get(fname)
        artifact_path: Path = backup_path / fname
        if expected is None or not artifact_path.is_file():
            continue
        actual = _sha256_file(artifact_path)
        if actual != expected:
            _emit_audit(
                actor_user_id=actor_uuid,
                action="backup.restore_failed",
                target_id=name,
                diff={
                    "error": "checksum_mismatch",
                    "artifact": fname,
                    "expected": expected,
                    "actual": actual,
                },
            )
            raise RestoreTaskError(
                f"checksum mismatch on {fname}: expected={expected} actual={actual}"
            )

    try:
        try:
            _run_psql_restore(backup_path / "postgres.sql.gz")
        except RestoreTaskError as exc:
            log.error("admin.backup.psql_restore_failed", name=name, error=str(exc))
            _emit_audit(
                actor_user_id=actor_uuid,
                action="backup.restore_failed",
                target_id=name,
                diff={"stage": "psql_restore", "error": str(exc)[-512:]},
            )
            raise

        if (backup_path / "workspace.tar.gz").is_file():
            try:
                _extract_workspace_archive(backup_path / "workspace.tar.gz")
            except (OSError, tarfile.TarError, RestoreTaskError) as exc:
                log.error("admin.backup.workspace_extract_failed", name=name, error=str(exc))
                _emit_audit(
                    actor_user_id=actor_uuid,
                    action="backup.restore_failed",
                    target_id=name,
                    diff={"stage": "workspace_extract", "error": str(exc)[-512:]},
                )
                if isinstance(exc, RestoreTaskError):
                    raise
                raise RestoreTaskError(f"workspace extract failed: {exc}") from exc
    finally:
        structlog.contextvars.unbind_contextvars("task_name", "task_id")

    # Re-read manifest after restore (drift detection — operator may have
    # hand-edited the file between validation and execution).
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
    # No autoretry: restore is destructive (drops and reloads tables).
    # Re-running on a transient psql hiccup would compound the damage —
    # the operator must inspect logs and re-trigger explicitly.
    max_retries=0,
)
def restore_backup_task(
    self: Any,
    *,
    name: str,
    actor_user_id: str,
) -> dict[str, Any]:
    """Restore a backup directly via ``psql`` + tar (no shell delegation).

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
