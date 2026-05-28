"""
Backup automation schemas — Phase 6 chore PR #19.

Pydantic v2 schemas for the admin backup surface:

  - GET    /v1/admin/backup           → BackupListResponse
  - POST   /v1/admin/backup           → BackupTriggerResponse (202)
  - GET    /v1/admin/backup/{name}/download   → streaming tar.gz
  - POST   /v1/admin/backup/restore   → BackupRestoreResponse (202)
  - DELETE /v1/admin/backup/{name}    → 204 (manual backups only)

Adversarial input notes:
  - ``name`` strings are validated against a strict regex
    ``^(auto|manual)-\\d{8}T\\d{6}Z$`` at the service layer to block path
    traversal (``..``), command injection (``;rm``), and null bytes.
  - The schema layer does not own the regex check because the value is
    accepted from path params (FastAPI ``Path``) rather than request bodies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

BackupKind = Literal["auto", "manual"]


class BackupInfo(BaseModel):
    """One row in the admin backup listing."""

    name: str = Field(
        description=(
            "Backup directory name. Format: ``{kind}-YYYYMMDDTHHMMSSZ``. "
            "Used as the path component for download / delete / restore."
        ),
    )
    kind: BackupKind
    created_at: datetime
    size_bytes: int = Field(ge=0)
    db_revision: str | None = Field(
        default=None,
        description=(
            "Alembic head recorded in the manifest at backup time. ``None`` "
            "when the manifest was missing or unreadable (rare — emitted "
            "only by the script's ``unknown`` fallback)."
        ),
    )


class BackupListResponse(BaseModel):
    """Response of ``GET /v1/admin/backup``."""

    items: list[BackupInfo]
    total: int = Field(ge=0)


class BackupTriggerResponse(BaseModel):
    """Response of ``POST /v1/admin/backup`` — Celery enqueue receipt."""

    task_id: str
    name: str = Field(
        description=(
            "Pre-computed backup directory name the task will create. "
            "Useful for the UI to poll the listing without waiting for "
            "task completion."
        ),
    )


class BackupRestoreResponse(BaseModel):
    """Response of ``POST /v1/admin/backup/restore`` — Celery enqueue receipt."""

    task_id: str
    message: str


__all__ = [
    "BackupInfo",
    "BackupKind",
    "BackupListResponse",
    "BackupRestoreResponse",
    "BackupTriggerResponse",
]
