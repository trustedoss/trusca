"""
Admin API sub-router aggregator — Phase 4 PR #13 + PR #14.

Mounts under ``/v1/admin`` and gates every nested route through
``require_super_admin_or_404`` (existence-hide: non-super-admin = 404,
anonymous = 401).

Sub-routers:
  - ``users``  — ``/v1/admin/users/*``   (PR #13)
  - ``teams``  — ``/v1/admin/teams/*``   (PR #13)
  - ``dt``     — ``/v1/admin/dt/*``      (PR #14: status / orphans / health-check)
  - ``scans``  — ``/v1/admin/scans/*``   (PR #14: cross-team queue + cancel)
  - ``disk``   — ``/v1/admin/disk``      (PR #14: workspace + DT + DB telemetry)
  - ``audit``  — ``/v1/admin/audit/*``   (PR #14: search + CSV export)
  - ``health`` — ``/v1/admin/health``    (PR #14: aggregated component status)

Future PRs (#15+) will add the component-approval workflow sub-router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.security import require_super_admin_or_404

from . import audit, disk, dt, health, scans, teams, users

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
router.include_router(dt.router)
router.include_router(scans.router)
router.include_router(disk.router)
router.include_router(audit.router)
router.include_router(health.router)


__all__ = ["router"]
