"""
Scan resolution helpers — the single source of truth for "which scan does a
project's *current state* read from?".

The denormalized ``Project.latest_scan_id`` pointer is updated on EVERY scan
attempt (queued / running / succeeded / failed — see
``services.scan_service`` and ``services.webhook_service``). That makes it the
right anchor for "what was the last thing that happened" (history, scan-queue
list, the writer that owns it), but the WRONG anchor for "what is the project's
current SCA posture" — because a project whose most recent attempt FAILED still
has perfectly valid findings from its last SUCCEEDED scan.

Reading the project-detail / overview / vuln-list / license / obligation /
source-tree surfaces off ``latest_scan_id`` produced the verified bug where a
project with an older succeeded scan (74 findings, 10 critical) but two newer
FAILED attempts rendered "NO RISK / 0 components" on the Overview next to a
Build-gate card reading "blocked — 10 open critical CVE(s)". The build gate was
already correct because it resolved the latest *succeeded* scan; the display
readers were not.

:func:`latest_succeeded_scan_id` is that resolver, promoted here so every
current-state reader (and the build gate, which used to own a private copy)
shares ONE definition and the two can never drift. The dashboard service already
has its own *batched* (multi-project ``DISTINCT ON``) variant for the portfolio
view; this is the single-project form the per-project detail endpoints need.

CLAUDE.md compliance:
  - Core rule #1/#2: pure async-SQLAlchemy read against PostgreSQL; no schema
    change (the supporting index ``ix_scans_project_created_at`` already exists).
  - This module is a pure DB read with NO auth check: callers (routers /
    services) enforce team access before invoking it, exactly as the build gate
    and the per-tab services already do.
"""

from __future__ import annotations

import uuid

from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Scan


class SnapshotScanNotFound(Exception):
    """Raised when an explicit ``scan_id`` snapshot anchor is not resolvable.

    "Not resolvable" deliberately conflates THREE distinct conditions into one
    existence-hiding signal (feature #28 Phase 1):

      - the scan id does not exist at all;
      - the scan id exists but belongs to a DIFFERENT project (an IDOR probe);
      - the scan id belongs to THIS project but is not ``status='succeeded'``
        (queued / running / failed / cancelled — no immutable snapshot to read).

    Routers translate this to a 404 RFC 7807 problem and MUST NOT reveal which
    of the three it was: a caller that pins another project's scan id must learn
    nothing about whether that id exists elsewhere. Keeping the discrimination
    server-side (the resolver knows; the caller does not) is what closes the
    cross-team enumeration path the IDOR test exercises.
    """


async def latest_succeeded_scan_id(
    session: AsyncSession,
    project_id: uuid.UUID,
) -> uuid.UUID | None:
    """Return the ID of the project's most recent ``status='succeeded'`` scan, or None.

    We deliberately do NOT use ``Project.latest_scan_id`` here: that pointer
    reflects the last *attempted* scan, so a successful scan whose last attempt
    failed would otherwise be evaluated against a non-succeeded scan. Querying
    ``scans`` directly, ordered by ``created_at DESC, id DESC`` and clamped to
    ``status='succeeded'``, gives the contract every current-state reader wants.
    The compound index ``ix_scans_project_created_at`` covers this access path.

    Returns ``None`` when the project has never had a succeeded scan — callers
    map that to the same "empty 200" shapes they already return for a project
    with no scan at all (never a 404 / 500).
    """
    stmt = (
        select(Scan.id)
        .where(Scan.project_id == project_id)
        .where(cast(Scan.status, String) == "succeeded")
        .order_by(Scan.created_at.desc(), Scan.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def resolve_snapshot_scan_id(
    session: AsyncSession,
    project_id: uuid.UUID,
    scan_id: uuid.UUID | None,
) -> uuid.UUID | None:
    """Resolve which scan a detail-read surface should anchor on (feature #28).

    This is the SINGLE place the optional ``?scan_id=`` snapshot anchor is
    interpreted, so every detail endpoint (overview / components / vulns /
    licenses / obligations / gate-result / SBOM / source-tree) applies the
    EXACT same rule and can never drift:

      - ``scan_id is None`` → return :func:`latest_succeeded_scan_id` (the
        UNCHANGED default: the project's most recent succeeded scan, or ``None``
        when it has never succeeded a scan → callers keep their "empty 200"
        behaviour).

      - ``scan_id`` provided → it is VALID only when it (a) belongs to THIS
        ``project_id`` AND (b) has ``status='succeeded'``. A valid id is
        returned verbatim so the caller's ``WHERE scan_id = <resolved>``
        aggregation runs against that immutable snapshot.

      - ``scan_id`` provided but INVALID (nonexistent / another project's scan /
        not succeeded) → raise :class:`SnapshotScanNotFound`. The router maps
        that to a 404 (existence-hide). This is what stops a caller pinning
        another project's scan id to read its findings through this project's
        surface (the IDOR guard).

    Both predicates (project ownership + succeeded status) are checked in ONE
    indexed query (``ix_scans_project_created_at`` / the PK cover it), so the
    override costs a single extra round-trip over the default path. No auth is
    performed here — the caller has already team-scoped ``project_id`` before
    invoking the resolver (same contract as :func:`latest_succeeded_scan_id`).
    """
    if scan_id is None:
        return await latest_succeeded_scan_id(session, project_id)

    # Validate ownership AND succeeded status in one statement. We deliberately
    # do NOT split "wrong project" from "not succeeded": both collapse to the
    # same existence-hiding 404 at the router so a cross-team probe learns
    # nothing (see SnapshotScanNotFound).
    stmt = (
        select(Scan.id)
        .where(Scan.id == scan_id)
        .where(Scan.project_id == project_id)
        .where(cast(Scan.status, String) == "succeeded")
        .limit(1)
    )
    result = await session.execute(stmt)
    resolved = result.scalar_one_or_none()
    if resolved is None:
        raise SnapshotScanNotFound(
            f"scan {scan_id} is not a succeeded snapshot of project {project_id}"
        )
    return resolved


__all__ = [
    "SnapshotScanNotFound",
    "latest_succeeded_scan_id",
    "resolve_snapshot_scan_id",
]
