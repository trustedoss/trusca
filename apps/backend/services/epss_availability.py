# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Whether this deployment has EPSS data an operator can rely on.

The gate answers a per-scan question: did the EPSS axis judge anything on THIS
scan (``services.epss_gate_outcome``). The policy screen asks a different one,
because it is not looking at a scan: an administrator setting a threshold there
needs to know whether this deployment collects EPSS at all, before any project
is scanned again.

Two signals, and both are needed
--------------------------------
"Does the catalog hold any EPSS value" on its own is weak. It says a value was
written once. A deployment that turned the sync off months ago still has old
scores on a handful of CVEs, so the screen would look healthy while every CVE
found since has come back unscored, which is the false reassurance this whole
line of work exists to remove.

"Is the sync enabled and recently successful" on its own is also incomplete: an
operator who just switched it on has a healthy sync and an empty catalog until
the first tick lands.

So both are reported. The screen says the threshold is unreliable when either
one says so, and names which.

This is deliberately NOT the signal the gate uses. A successful sync does not
promise the feed covered a particular scan's CVEs, which is why the per-scan
question is answered per scan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import epss_refresh_enabled
from models import EpssSyncState
from models import Vulnerability as VulnerabilityModel

#: How long after the last successful sync the data is still called current.
#: The feed publishes daily and the beat runs daily, so a gap beyond three days
#: means several ticks in a row did not land: long enough not to fire on one
#: missed night, short enough that the scores have not drifted far.
_STALE_AFTER = timedelta(days=3)


@dataclass(frozen=True)
class EpssAvailability:
    """What the policy screen needs to qualify an EPSS threshold.

    ``usable`` is the single answer, and the three fields under it are why, so
    the screen can say which problem it is rather than only that there is one.
    """

    usable: bool
    refresh_enabled: bool
    scored_cves: int
    last_synced_at: datetime | None


async def get_epss_availability(session: AsyncSession) -> EpssAvailability:
    """Read the deployment's EPSS posture. One count plus one PK lookup."""
    scored = int(
        (
            await session.execute(
                select(func.count())
                .select_from(VulnerabilityModel)
                .where(VulnerabilityModel.epss_score.is_not(None))
            )
        ).scalar_one()
    )
    state = (await session.execute(select(EpssSyncState))).scalars().first()
    last_synced_at = state.last_synced_at if state is not None else None
    refresh_enabled = epss_refresh_enabled()

    fresh = last_synced_at is not None and (
        datetime.now(tz=UTC) - last_synced_at
    ) <= _STALE_AFTER
    return EpssAvailability(
        usable=bool(scored) and refresh_enabled and fresh,
        refresh_enabled=refresh_enabled,
        scored_cves=scored,
        last_synced_at=last_synced_at,
    )


__all__ = ["EpssAvailability", "get_epss_availability", "_STALE_AFTER"]
