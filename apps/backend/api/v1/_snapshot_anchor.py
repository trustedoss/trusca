# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
The shared ``?scan_id=`` / ``?release=`` snapshot anchor for detail reads.

Fourteen endpoints let a caller read a project surface as of one succeeded
scan. Until now the only way to name that scan was its UUID, which a caller
cannot know in advance: answering "what shipped in 4.0" meant listing releases,
matching the label client-side, then re-requesting with the id it found. Two
round-trips, and no URL you could write down or paste into a ticket.

``release`` closes that: ``/notice?release=4.0`` is a permanent address for a
version, because a label identifies exactly one live snapshot (see
``tasks.scan_retention.supersede_prior_release_scans``).

Resolving it HERE rather than in each service is deliberate. The alternative —
a second parameter threaded through fourteen endpoints and the ten services
behind them — multiplies the number of places the precedence rule could drift,
for a translation that is the same everywhere: turn a label into the scan id
the endpoint already knows how to handle. Services keep their existing
``snapshot_scan_id`` contract and never learn that labels exist.

Precedence: ``scan_id`` wins when both are given. It names one immutable
snapshot, whereas a label names whichever snapshot currently holds it — so the
more specific of the two should not be overridden by the looser one.

Authorization: none here, matching ``services.scan_resolution``. The lookup is
scoped to ``project_id`` and both "no such label" and "no such project you can
see" surface as the same 404, so resolving before the endpoint's team check
tells an outside caller nothing it could not already infer.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, Query
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from models import Scan
from services.scan_resolution import SnapshotScanNotFound

_SCAN_ID_DESCRIPTION = (
    "Optional release-snapshot anchor. Read this surface as of ONE specific "
    "succeeded scan instead of the project's current state. Must belong to this "
    "project and be succeeded, else 404. Takes precedence over ``release``."
)

_RELEASE_DESCRIPTION = (
    "Optional version anchor — read this surface as of the release carrying "
    "this label (e.g. '4.0'). Equivalent to looking the label up on "
    "``/releases?release=`` and pinning the ``scan_id`` it returns, but as one "
    "permanent URL. Matched exactly, whitespace trimmed. Unknown label → 404. "
    "Ignored when ``scan_id`` is also given."
)


async def resolve_release_label(
    session: AsyncSession,
    project_id: uuid.UUID,
    label: str,
) -> uuid.UUID | None:
    """Return the live snapshot carrying *label*, or ``None``.

    Mirrors the releases-list filter exactly — succeeded, not superseded,
    trimmed comparison — so a label that appears there resolves here and one
    that does not, does not. Superseded rows are excluded because a rescan
    moves the label: ``4.0`` must mean the snapshot that currently holds it,
    not every scan that ever claimed it.

    Served by ``ix_scans_project_release_label``.
    """
    stripped = label.strip()
    if not stripped:
        return None
    stmt = (
        select(Scan.id)
        .where(Scan.project_id == project_id)
        .where(cast(Scan.status, String) == "succeeded")
        .where(Scan.superseded_at.is_(None))
        .where(func.jsonb_typeof(Scan.scan_metadata["release"]) == "string")
        .where(func.btrim(Scan.scan_metadata["release"].astext) == stripped)
        .order_by(Scan.created_at.desc(), Scan.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def snapshot_anchor(
    project_id: uuid.UUID,
    scan_id: uuid.UUID | None = Query(default=None, description=_SCAN_ID_DESCRIPTION),
    release: str | None = Query(
        default=None, max_length=100, description=_RELEASE_DESCRIPTION
    ),
    session: AsyncSession = Depends(get_db),
) -> uuid.UUID | None:
    """Resolve the effective ``scan_id`` for a detail read.

    Returns what the endpoint's service already expects: ``None`` for "current
    state", or a scan id to pin. An unresolvable label raises
    :class:`SnapshotScanNotFound`, which ``core.errors`` renders as the same
    existence-hiding 404 an unresolvable ``scan_id`` produces — a caller must
    not be able to tell "that version does not exist" from "that scan id is not
    yours".

    A pinned ``scan_id`` is returned unvalidated; the service still passes it
    through ``resolve_snapshot_scan_id``, which owns the ownership + succeeded
    checks. Validating twice here would cost a round-trip on every request to
    move a check that is already in the right place.
    """
    if scan_id is not None or release is None:
        return scan_id
    resolved = await resolve_release_label(session, project_id, release)
    if resolved is None:
        raise SnapshotScanNotFound(
            f"no release labelled {release!r} in project {project_id}"
        )
    return resolved


__all__ = ["resolve_release_label", "snapshot_anchor"]
