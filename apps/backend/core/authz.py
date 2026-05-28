"""
Authorization helpers shared across services.

Two pieces in this module:

- :func:`can_access_team` — the canonical "is this actor a member of (or a
  super-admin over) this team?" predicate. The five services that participate
  in cross-team checks (project / project_detail / vulnerability / license /
  obligation) used to redeclare a ``_can_access_team`` helper each — the
  centralized version keeps the policy in one place so a future tweak (for
  example, adding a "read-only org viewer" role) only has to land here.

- :func:`assert_team_access` — convenience wrapper that does the
  ``if not can_access_team(...): log + raise`` dance every cross-team gate
  performs. Centralizing it pins the structure of the
  ``authz.cross_team_attempt`` log event across modules so SOC tooling sees
  a single shape regardless of which surface emitted it.

The helpers are deliberately small and side-effect-free apart from the log
line in ``assert_team_access`` — services keep their own raise sites for the
domain-specific exceptions (``ProjectForbidden``, ``VulnerabilityNotFound``,
``LicenseFindingNotFound``, ``ObligationNotFound``). The ``deny`` callable
returns the exception so the caller controls the visible message.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import structlog

from core.security import CurrentUser


def can_access_team(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    """``True`` iff the actor can read team-scoped resources for *team_id*.

    Super-admins (either ``is_superuser`` or ``role == "super_admin"``) bypass
    the team-membership check; everyone else must have the team in
    ``actor.team_ids``.
    """
    if actor.is_superuser or actor.role == "super_admin":
        return True
    return team_id in actor.team_ids


def assert_team_access(
    actor: CurrentUser,
    team_id: uuid.UUID,
    *,
    log: structlog.stdlib.BoundLogger,
    resource: str,
    resource_id: str,
    deny: Callable[[], Exception],
) -> None:
    """Raise ``deny()`` (after emitting an ``authz.cross_team_attempt`` warning)
    if the actor cannot access *team_id*; otherwise return.

    Parameters
    ----------
    actor:
        The authenticated caller.
    team_id:
        Team owning the resource the caller is trying to read or mutate.
    log:
        Per-module logger so the emitted event carries the module name in
        its logger field. Callers pass their existing
        ``log = structlog.get_logger("...")`` instance.
    resource:
        Short string identifying the resource type (``"project"``,
        ``"vulnerability_finding"``, ``"license_finding"``, ``"obligation"``,
        …). Goes into the log line for SOC routing.
    resource_id:
        Stringified id of the resource the caller asked for. Goes into the
        log line.
    deny:
        Zero-arg callable returning the domain-specific exception to raise
        on denial. Two patterns:

        - 403-visible-existence: ``deny=lambda: ProjectForbidden(...)``
        - 404-existence-hide:    ``deny=lambda: SomeNotFound(...)``

        The helper invokes ``raise deny()``; using a callable instead of an
        eagerly-built exception avoids constructing one on the happy path.
    """
    if can_access_team(actor, team_id):
        return
    log.warning(
        "authz.cross_team_attempt",
        actor_id=str(actor.id),
        target_team_id=str(team_id),
        resource=resource,
        resource_id=resource_id,
    )
    raise deny()


__all__ = ["assert_team_access", "can_access_team"]
