# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Report what the runtime's database role can actually do (ER49).

Why this asks about privileges and not about configuration
----------------------------------------------------------
The check this replaces refused to boot when ``DATABASE_URL_APP`` was set and
the connected role was not ``trustedoss_app``, reading "the variable is set" as
"the operator configured role separation". Nothing in the runtime container
supports that reading:

* ``docker-compose.yml`` sets ``DATABASE_URL_APP`` from ``DATABASE_URL`` when
  the operator has not set it, so it is populated on every deployment. The
  Helm chart likewise always writes it, including for the single-role external
  database its own values file recommends.
* The backend never receives ``DATABASE_URL_OWNER`` (deliberately: a runtime
  compromise must not hold the DDL DSN) or ``POSTGRES_APP_USER``, so there is
  nothing to compare against.

Rendering both deployment modes shows the only in-container difference is the
username inside the DSN. That matters because in the failure the old check
named, an intended split that collapsed back to the owner, the DSN user and
``current_user`` are both the owner and entirely consistent. The old check
therefore had no true-positive case: it could only fire on deployments that had
never configured separation, which is what it did.

So this asks Postgres what the role can do. Whether the runtime holds DDL
rights is the fact that matters for security, it is true or false regardless of
how the deployment was configured, and it does not depend on a role NAME: an
external database whose DML-only role is called something else answers
correctly, which a name comparison cannot.

What it does not distinguish, stated plainly: a runtime holding DDL because the
operator chose a single-role install looks exactly like one holding DDL because
an intended split collapsed. Both are reported. Single-role is a legitimate,
supported configuration, so the default is to warn rather than refuse, and an
operator who requires the split turns on ``REQUIRE_DB_ROLE_SEPARATION``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: TRUNCATE on ``audit_logs`` is the probe because the append-only audit table
#: is the thing role separation exists to protect, and migration 0014 grants
#: the runtime role INSERT/SELECT there and nothing else. Owner roles hold it;
#: the DML-only role does not.
DDL_PROBE_SQL = "SELECT has_table_privilege(current_user, 'audit_logs', 'TRUNCATE')"


def require_db_role_separation() -> bool:
    """Whether a runtime holding DDL rights should refuse to start.

    Opt-in, default off: a single-role install is supported and must keep
    booting. An organization that mandates the split turns this on and gets a
    real gate instead of a log line nobody reads.

    Truthy: ``1`` / ``true`` / ``yes`` / ``on`` (case-insensitive). Read at call
    time (rule #11).
    """
    raw = os.getenv("REQUIRE_DB_ROLE_SEPARATION", "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RoleVerdict:
    """What to do about the connected role. ``fatal`` means refuse to start."""

    event: str
    level: str  # "info" | "warning" | "error"
    fatal: bool
    message: str


def evaluate_db_role(
    *, role: str, holds_ddl: bool | None, strict: bool
) -> RoleVerdict:
    """Judge the connected role. Pure, so every combination is testable.

    ``holds_ddl`` is None when the probe could not be answered.
    """
    if holds_ddl is None:
        if strict:
            # Fail closed. REQUIRE_DB_ROLE_SEPARATION is an operator saying the
            # split is mandatory here; "could not tell" must not pass a gate
            # they asked for, or the gate is advisory exactly when something is
            # already wrong. The cost is a refused start on an unreadable
            # database, which is a condition that needs attention anyway.
            return RoleVerdict(
                event="db.role.separation.undetermined",
                level="error",
                fatal=True,
                message=(
                    f"REQUIRE_DB_ROLE_SEPARATION is on but the privileges of "
                    f"role {role!r} could not be determined, so the required "
                    f"separation cannot be confirmed. Refusing to start. Turn "
                    f"the setting off to start without this guarantee."
                ),
            )
        return RoleVerdict(
            event="db.role.separation.undetermined",
            level="warning",
            fatal=False,
            message=(
                f"could not determine whether database role {role!r} holds DDL "
                f"privileges; continuing without the role separation check"
            ),
        )

    if not holds_ddl:
        return RoleVerdict(
            event="db.role.separation.active",
            level="info",
            fatal=False,
            message=(
                f"database role {role!r} is DML-only; role separation is in "
                f"effect"
            ),
        )

    # Actionable on purpose. The check this replaces told operators to inspect
    # an L1 split they had never configured, which sent them looking for a
    # misconfiguration that did not exist.
    remedy = (
        f"Database role {role!r} can perform DDL (it holds TRUNCATE on "
        f"audit_logs), so the runtime is not limited to reading and writing "
        f"rows: a compromise of this process could drop the audit trigger or "
        f"alter tables. This is expected on a single-role install, which is a "
        f"supported configuration. To separate the roles, set "
        f"POSTGRES_APP_PASSWORD before the first start of a fresh database so "
        f"the runtime role is created, or on Kubernetes point env.database.url "
        f"at a DML-only role and env.database.ownerUrl at the owning role. To "
        f"make this condition refuse to start, set REQUIRE_DB_ROLE_SEPARATION=true."
    )
    if strict:
        return RoleVerdict(
            event="db.role.separation.missing",
            level="error",
            fatal=True,
            message=f"REQUIRE_DB_ROLE_SEPARATION is on. {remedy}",
        )
    return RoleVerdict(
        event="db.role.separation.missing",
        level="warning",
        fatal=False,
        message=remedy,
    )


__all__ = [
    "DDL_PROBE_SQL",
    "RoleVerdict",
    "evaluate_db_role",
    "require_db_role_separation",
]
