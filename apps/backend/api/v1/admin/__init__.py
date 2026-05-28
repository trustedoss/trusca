"""
Admin API sub-router aggregator — Phase 4 PR #13 + PR #14.

Mounts under ``/v1/admin`` and gates every nested route through
``require_super_admin_or_404`` (existence-hide: non-super-admin = 404,
anonymous = 401).

Sub-routers:
  - ``users``  — ``/v1/admin/users/*``   (PR #13)
  - ``teams``  — ``/v1/admin/teams/*``   (PR #13)
  - ``scans``  — ``/v1/admin/scans/*``   (PR #14: cross-team queue + cancel)
  - ``disk``   — ``/v1/admin/disk``      (PR #14: workspace + DB telemetry)
  - ``audit``  — ``/v1/admin/audit/*``   (PR #14: search + CSV export)
  - ``health`` — ``/v1/admin/health``    (PR #14: aggregated component status)
  - ``backup`` — ``/v1/admin/backup/*``  (chore PR #19: list/trigger/download/restore/delete)
  - ``trivy``  — ``/v1/admin/trivy/*``   (W6-#43e: vulnerability DB status panel)

W6-#43a (ADR-0001): the ``dt`` sub-router was removed when DT was replaced
by Trivy (W6-#41); previously-issued DT audit-log rows are preserved as
historical fact, but the live endpoints are gone.
W6-#43e: the new ``trivy`` sub-router exposes the Trivy DB lifecycle state
to the admin/health panel — read-only; the W6-#44 weekly refresh beat is a
separate follow-up.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.security import require_super_admin_or_404

from . import audit, backup, disk, health, scans, teams, trivy, users

# Apply the super-admin gate at the parent-router level so individual route
# signatures stay clean — each route still gets the resolved CurrentUser
# via its own ``Depends(require_super_admin_or_404())`` injection where it
# needs the actor.
router = APIRouter(
    prefix="/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_super_admin_or_404())],
)

router.include_router(users.router)
router.include_router(teams.router)
router.include_router(scans.router)
router.include_router(disk.router)
router.include_router(audit.router)
router.include_router(health.router)
router.include_router(backup.router)
router.include_router(trivy.router)


__all__ = ["router"]
