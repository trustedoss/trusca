# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Operator command: carry out an approved user anonymisation (ER32).

Run inside the backend container, as the database owner::

    docker-compose -f docker-compose.yml exec -T \\
      -e DATABASE_URL_OWNER="$(grep ^DATABASE_URL_OWNER= .env | cut -d= -f2-)" \\
      -e SUBJECT_USER_ID=... \\
      -e CONFIRM=yes \\
      backend python -m scripts.anonymise_user

Why this is a command and not a button
--------------------------------------
The scrub reaches inside ``audit_logs``, which is append-only, through a
``SECURITY DEFINER`` function that only the table's owner may execute. The
application role deliberately does not hold that grant: a compromised
application must not be able to edit the record of what it did. So the last
step is taken by a person with owner credentials, on a server, on purpose.

That has a consequence the operator has to own. Approval happens in the
product and execution happens here, so between the two the request sits done
in everybody's mind and undone in fact. ``GET
/v1/user-anonymisation/awaiting-execution`` and the admin screen exist to make
that gap visible; this command is what closes it, and nothing closes it
automatically.

What it does, and what it cannot
--------------------------------
Erased: the account's email and name, its OAuth links (which carry the
provider's own copy of the address), its sessions and reset tokens, its saved
searches, and the ip and user_agent on its audit rows. A personal team named
or described after the person is renamed.

Retained, and stated rather than hidden: the audit rows themselves, including
their ``diff``. A diff records what a change contained, and rows written
before this version can hold the old address inside one. The trigger exception
this command relies on forbids touching ``diff`` at all, which is deliberate:
an exception wide enough to rewrite diffs would end the immutability of the
audit trail. The processing procedure documents this, because an erasure that
quietly leaves an address behind while reporting success is worse than one
that says what it kept.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import uuid

import structlog
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import aliased

from core.security import set_password
from models import Membership, Organization, Team, User
from models.auth import PasswordResetToken, RefreshToken
from models.oauth_identity import OAuthIdentity
from models.saved_search import SavedSearch
from services.oauth_service import personal_team_name
from services.user_anonymisation_service import (
    approved_request_for,
    corroborating_audit_actions,
    mark_executed,
)

log = structlog.get_logger("scripts.anonymise_user")

#: Written into ``users.email``. Unique per subject so the column's unique
#: constraint still holds, and shaped so nobody mistakes it for an address
#: somebody might try to contact.
def _tombstone_email(user_id: uuid.UUID) -> str:
    return f"anonymised-{user_id.hex}@invalid"


def _database_url() -> str:
    """Owner credentials, under the name this deployment already uses for them.

    ``DATABASE_URL_OWNER`` is what ``install.sh`` writes for the DDL role, and
    ``docker-compose.yml`` deliberately does not pass it into the runtime
    containers, so a runtime compromise cannot reach it. Introducing a second
    variable for the same credential would have been a second thing to keep out
    of those containers.

    Falling back to ``DATABASE_URL`` covers two different deployments and it
    is worth being exact about which:

    - **Role-separated (L1).** ``DATABASE_URL`` is the application role, which
      holds no EXECUTE on the scrub function, so running without the owner
      credential is refused by Postgres rather than half-done. Everything here
      is one transaction, so a refusal leaves nothing behind.
    - **Single-role.** ``DATABASE_URL_APP`` and ``DATABASE_URL_OWNER`` both
      fall back to ``DATABASE_URL`` (``.env.example``), the runtime role IS the
      table owner, and the command simply succeeds. It also means the
      protections around this operation are not in force on that deployment:
      the trigger's owner-membership check is trivially true for the
      application, and the owner's implicit EXECUTE survives the REVOKE, so
      the running portal can call the scrub function itself. The two-person
      request is still enforced by the function, but the boundary that keeps a
      compromised runtime away from the audit trail is not there.

    The guarantees this command's docstring describes are the role-separated
    ones. See the admin guide for what a single-role install does and does not
    get.
    """
    return os.getenv("DATABASE_URL_OWNER") or os.getenv("DATABASE_URL") or ""


#: A second handle on ``memberships`` so the "only member" subquery can count
#: rows in the same table the outer query has already joined.
_other_members = aliased(Membership)


async def anonymise(session: AsyncSession, *, subject_id: uuid.UUID) -> dict[str, int]:
    """Erase what can be erased, in one transaction. Returns counts, not values."""
    request = await approved_request_for(session, subject_user_id=subject_id)
    if request is None:
        raise SystemExit(
            f"no approved anonymisation request for {subject_id}. "
            "Two super admins must open and approve one first; this command "
            "does not create its own authority."
        )

    # The request row says two people agreed. The row alone does not prove it:
    # the application role can INSERT and UPDATE this table, so anything that
    # reaches SQL through the running portal can write "approved" without any
    # person deciding, and it looks identical on the backlog. Requests made
    # through the API leave a create and an update in the append-only audit
    # log; a conjured one leaves neither.
    #
    # This is a corroboration, not a proof, and refusing here is the right
    # response to its absence: the operator can still go and ask the two
    # people named on the backlog, and if they did agree, the fix is to make
    # the request through the product.
    actions = await corroborating_audit_actions(session, request_id=request.id)
    missing = {"create", "update"} - actions
    if missing:
        raise SystemExit(
            f"request {request.id} has no audit record of being "
            f"{'opened' if 'create' in missing else 'approved'} through the "
            "portal (audit actions found: "
            f"{sorted(actions) or 'none'}). A request written directly to the "
            "database looks like this. Refusing: confirm with the requester "
            f"({request.requested_by_user_id}) and the approver "
            f"({request.approved_by_user_id}) before going further."
        )

    user = await session.get(User, subject_id)
    if user is None:
        raise SystemExit(f"no such user: {subject_id}")

    counts: dict[str, int] = {}

    # Sessions first. Everything after this point changes what the account is,
    # and a live refresh token would let the old session keep acting as it.
    counts["refresh_tokens"] = (
        await session.execute(
            delete(RefreshToken).where(RefreshToken.user_id == subject_id)
        )
    ).rowcount
    counts["password_reset_tokens"] = (
        await session.execute(
            delete(PasswordResetToken).where(PasswordResetToken.user_id == subject_id)
        )
    ).rowcount
    counts["oauth_identities"] = (
        await session.execute(
            delete(OAuthIdentity).where(OAuthIdentity.user_id == subject_id)
        )
    ).rowcount
    counts["saved_searches"] = (
        await session.execute(
            delete(SavedSearch).where(SavedSearch.user_id == subject_id)
        )
    ).rowcount

    # Personal teams only, and "personal" is read from the schema rather than
    # guessed from the text: a team under an organisation flagged
    # ``is_personal`` that this user belongs to. Earlier this matched any team
    # of theirs whose name or description contained their address, which would
    # have renamed a SHARED team somebody had labelled with their email. That
    # team is other people's record; they navigate by its name, and an erasure
    # about one person must not rewrite what a group relies on. A shared team
    # carrying the address is left alone and written down in the processing
    # procedure instead, where somebody can decide about it deliberately.
    renamed = 0
    personal_teams = (
        (
            await session.execute(
                select(Team)
                .join(Membership, Membership.team_id == Team.id)
                .join(Organization, Organization.id == Team.organization_id)
                .where(Membership.user_id == subject_id)
                .where(Organization.is_personal.is_(True))
                # And nobody else is in it. ``create_team`` refuses to put a
                # team under a personal organisation, but ``add_team_member``
                # has no such check, so a super admin can place someone into
                # another person's personal team. Without this clause,
                # anonymising the guest would rename the host's team out from
                # under them, which is the same wrong this query narrowed away
                # from in the shared-team case.
                # Aliased: without it the inner Membership auto-correlates
                # with the one already joined above and the subquery loses its
                # FROM clause entirely.
                .where(
                    select(func.count())
                    .select_from(_other_members)
                    .where(_other_members.team_id == Team.id)
                    .scalar_subquery()
                    == 1
                )
            )
        )
        .scalars()
        .all()
    )
    for team in personal_teams:
        # Through the ORM on purpose, so the rename is audited. A security
        # review raised the opposite concern: that an audited rename would
        # write the old name, "<address>'s Team", back into the immutable
        # trail as the last act of erasing it. Checked rather than assumed.
        # ``core.audit._changed_columns`` records the NEW value of each changed
        # column and nothing else, so the row reads
        # ``{"name": "Personal team (...)", "description": null}``. The old
        # value is never transcribed, and skipping the ORM here would only
        # lose the record that the rename happened.
        team.name = personal_team_name(user_id=subject_id)
        team.description = None
        renamed += 1
    counts["teams_renamed"] = renamed

    user.email = _tombstone_email(subject_id)
    user.full_name = None
    # Through the choke point, not by assigning the column. ``set_password``
    # also stamps ``password_changed_at`` and drops the cached principal,
    # which is what actually refuses an access token minted moments earlier.
    # Deleting the refresh tokens above ends the ability to get a NEW access
    # token; without this the one already in a browser would keep working
    # against the anonymised account for the rest of its half hour. The value
    # is a real hash of something nobody holds, so every verify fails and the
    # column carries no shape a later reader could special-case.
    set_password(user, secrets.token_urlsafe(48))
    user.is_active = False

    await session.flush()

    # The audit rows. Refused by the database itself unless the approval above
    # is genuinely recorded, so this cannot run on an operator's say-so.
    counts["audit_rows_scrubbed"] = int(
        (
            await session.execute(
                text("SELECT audit_logs_scrub_pii(:s)"), {"s": subject_id}
            )
        ).scalar_one()
    )

    # The erasure records itself. Nothing else does: this command builds its
    # own engine and session, so the ORM audit listener that fires for every
    # write in the running application is not installed here, and without this
    # row the only account of an irreversible act would be one structlog line
    # and a state column on a table the application can UPDATE.
    #
    # Not installing the listener is deliberate rather than an oversight. It
    # writes the OLD value of every changed column into a fresh audit diff, and
    # `core.audit` masks only columns literally named `email` and `full_name`.
    # The team rename above changes `teams.name` from something like
    # "<address>'s Team", so an audited run would plant the subject's address
    # back into the immutable trail as the last act of erasing it.
    #
    # Hence a hand-built row carrying ids and counts and no values at all.
    await session.execute(
        text(
            "INSERT INTO audit_logs "
            "(actor_user_id, action, target_table, target_id, diff) "
            "VALUES (NULL, 'anonymise', 'users', :target, CAST(:diff AS jsonb))"
        ),
        {
            "target": str(subject_id),
            # ``actor_user_id`` is NULL because the actor is an operator at a
            # shell, not a user of the portal. The request row names the two
            # people who authorised it and this points at that row.
            "diff": json.dumps(
                {
                    "request_id": str(request.id),
                    "requested_by_user_id": str(request.requested_by_user_id),
                    "approved_by_user_id": str(request.approved_by_user_id),
                    "counts": counts,
                }
            ),
        },
    )

    await mark_executed(session, request_id=request.id)
    await session.commit()

    log.warning("anonymisation_completed", subject_user_id=str(subject_id), **counts)
    return counts


async def _main() -> int:
    url = _database_url()
    if not url:
        print("DATABASE_URL_OWNER or DATABASE_URL must be set", file=sys.stderr)
        return 2

    raw = os.getenv("SUBJECT_USER_ID", "").strip()
    if not raw:
        print("SUBJECT_USER_ID must be set", file=sys.stderr)
        return 2
    try:
        subject_id = uuid.UUID(raw)
    except ValueError:
        print(f"SUBJECT_USER_ID is not a UUID: {raw!r}", file=sys.stderr)
        return 2

    if os.getenv("CONFIRM", "").strip().lower() not in {"yes", "y", "true", "1"}:
        # Two people already agreed, and this still asks. The approval was a
        # decision about the subject; this is a check that the operator meant
        # to run it now, against this id, on this deployment.
        print(
            "refusing to run without CONFIRM=yes. This cannot be undone.",
            file=sys.stderr,
        )
        return 2

    engine = create_async_engine(url, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            counts = await anonymise(session, subject_id=subject_id)
    finally:
        await engine.dispose()

    print(f"anonymised {subject_id}")
    for key in sorted(counts):
        print(f"  {key}: {counts[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
