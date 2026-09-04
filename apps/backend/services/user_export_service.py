# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
What one person can take with them: self-service export (ER32).

Twenty-eight tables carry a reference to ``users``. Almost none of them hold
personal data about that person. ``projects``, ``scans``, ``gate_policies``
and the rest record work somebody did on the organisation's behalf, and they
stay with the organisation when the person leaves, in the same way a commit
does. What this export gathers is the far smaller set that describes the
person rather than their output: who they are, how they signed in, what they
asked to be notified about, which teams they belong to, the searches they
saved, and their own activity record.

Two deliberate omissions
------------------------
``diff`` is stripped from the activity record. An audit row authored by this
user can hold another user's email inside its diff, because the row records a
change made to somebody else. Handing that to the requester in the name of
their own data protection would disclose a third party's, so the export keeps
what the person did (action, target, when, from where) and drops what the
change contained.

``hashed_password`` and every token are absent. They are not facts about the
person, they are credentials, and an export file is a copy that leaves the
system's control the moment it is downloaded.

Truncation is stated, never silent
----------------------------------
An account years old can hold more audit rows than belong in one response.
The activity record is capped, and when it is capped the payload says so and
gives the true total, because an export that quietly stops at a limit is an
export the recipient believes is complete. The rest of this track is a list of
things that looked finished and were not.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AuditLog, Membership, NotificationPreferences, Team, User
from models.oauth_identity import OAuthIdentity
from models.saved_search import SavedSearch

log = structlog.get_logger("services.user_export")

#: How many activity rows one export carries. Chosen so the response stays
#: comfortably renderable rather than for any legal reason; the payload always
#: reports the true total alongside, so a capped export is visibly capped.
ACTIVITY_LIMIT = 5000


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


async def build_self_export(
    session: AsyncSession, *, user_id: uuid.UUID
) -> dict[str, Any] | None:
    """Everything the system holds *about* this person, as a JSON-ready dict.

    Returns ``None`` when the user does not exist, which the caller renders as
    404. That branch is close to unreachable in practice: it needs a token
    that authenticated against a row which has since gone.

    An anonymised account does NOT reach here. Anonymisation deactivates it and
    ``get_current_user`` refuses an inactive user, so the request is rejected
    before this function is called. That is the right outcome, and it means
    self-export is a thing to do BEFORE asking for an erasure, not after.
    """
    user = await session.get(User, user_id)
    if user is None:
        return None

    prefs = (
        await session.execute(
            select(NotificationPreferences).where(
                NotificationPreferences.user_id == user_id
            )
        )
    ).scalar_one_or_none()

    identities = (
        (
            await session.execute(
                select(OAuthIdentity).where(OAuthIdentity.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )

    memberships = (
        await session.execute(
            select(Membership, Team)
            .join(Team, Team.id == Membership.team_id)
            .where(Membership.user_id == user_id)
        )
    ).all()

    saved = (
        (
            await session.execute(
                select(SavedSearch).where(SavedSearch.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )

    activity_total = (
        await session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.actor_user_id == user_id)
        )
    ).scalar_one()

    activity_rows = (
        (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.actor_user_id == user_id)
                .order_by(AuditLog.created_at.desc())
                .limit(ACTIVITY_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    payload: dict[str, Any] = {
        "generated_at": _iso(datetime.now(tz=UTC)),
        "account": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "is_verified": user.is_verified,
            "created_at": _iso(user.created_at),
            "updated_at": _iso(user.updated_at),
            "last_login_at": _iso(user.last_login_at),
            "password_changed_at": _iso(user.password_changed_at),
        },
        # Named one by one rather than dumped from ``__table__.columns``.
        # A whole-table dump is correct today, when every column is a boolean
        # toggle, and it silently ships whatever anybody adds to this table
        # later, into a file that leaves the system's control the moment it is
        # downloaded. A list has to be extended deliberately.
        "notification_preferences": (
            None
            if prefs is None
            else {
                "email_enabled": prefs.email_enabled,
                "slack_enabled": prefs.slack_enabled,
                "teams_enabled": prefs.teams_enabled,
                "in_app_enabled": prefs.in_app_enabled,
                "updated_at": _iso(prefs.updated_at),
            }
        ),
        "sign_in_methods": [
            {
                "provider": identity.provider,
                "provider_user_id": identity.provider_user_id,
                # The address the provider holds for them, which can differ
                # from their account email and is theirs either way.
                "provider_email": identity.email,
                "linked_at": _iso(identity.linked_at),
                "last_login_at": _iso(identity.last_login_at),
            }
            for identity in identities
        ],
        "team_memberships": [
            {
                "team_id": str(membership.team_id),
                "team_name": team.name,
                "role": membership.role,
                "joined_at": _iso(membership.created_at),
            }
            for membership, team in memberships
        ],
        "saved_searches": [
            {
                "id": str(row.id),
                "name": row.name,
                "kind": row.kind,
                "params": row.params,
                "created_at": _iso(row.created_at),
            }
            for row in saved
        ],
        "activity": {
            "total": activity_total,
            "included": len(activity_rows),
            # Named rather than implied. A consumer that only reads `entries`
            # would otherwise take a capped list for the whole record.
            "truncated": activity_total > len(activity_rows),
            "note": (
                "Change contents are omitted: an entry recording a change to "
                "another user would carry that user's data."
            ),
            "entries": [
                {
                    "created_at": _iso(row.created_at),
                    "action": row.action,
                    "target_table": row.target_table,
                    "target_id": row.target_id,
                    "ip": None if row.ip is None else str(row.ip),
                    "user_agent": row.user_agent,
                }
                for row in activity_rows
            ],
        },
    }

    log.info(
        "self_export_built",
        user_id=str(user_id),
        activity_total=activity_total,
        activity_included=len(activity_rows),
        truncated=payload["activity"]["truncated"],
    )
    return payload


__all__ = ["ACTIVITY_LIMIT", "build_self_export"]
