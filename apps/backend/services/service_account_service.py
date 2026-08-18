# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Identities for automation, so a credential does not die with a person.

An API key stops working when the person who issued it is deactivated. That is
right for a person's key and wrong for a pipeline's: a nightly build that has
run for a year stops the day its author leaves, and the first anyone hears of
it is a red pipeline. A service account is an issuer that outlives people.

The key-lifetime rule is not changed. The auth path still asks only whether the
issuer is active, which is why there is no branch there: for a service
account's key the issuer is the service account, so a person leaving has
nothing to do with it. A person's key keeps exactly the behaviour it has today.

What a service account has instead of a person is a steward: somebody
answerable for it, recorded so an unattended credential still has a name
against it. The steward is never consulted when authenticating. When they
leave, existing keys keep working (that is the point) and no new key may be
issued until somebody takes the account over.
"""

from __future__ import annotations

import re
import secrets
import uuid

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import CurrentUser, hash_password
from models import Membership, Team, User

log = structlog.get_logger("services.service_account")

#: The synthetic address a service account carries.
#:
#: ``users.email`` is NOT NULL and unique, and it is what the audit log and
#: every "who did this" surface prints. A recognisable, obviously undeliverable
#: address is better than a blank: somebody reading an audit row sees an
#: automation identity rather than an address they might try to write to.
#:
#: A syntactically valid domain rather than a reserved one like ``.invalid``.
#: The reserved TLDs are rejected by the email validator, which would mean the
#: address could never be submitted to the login form at all, and the guards
#: that refuse a service account there would never run. Two defences are worth
#: more than one that hides the other: the address reaches the guard, and the
#: guard turns it away.
SERVICE_ACCOUNT_EMAIL_DOMAIN = "svc.trusca.internal"

#: Slug rules for the name that becomes the address. Deliberately narrow: this
#: string ends up in an email column with a unique index, so anything that
#: could normalise into a collision is refused rather than transformed.
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")


class ServiceAccountError(Exception):
    """Base for the failures the router renders as Problem Details."""

    status_code = 409
    title = "Conflict"


class ServiceAccountNotFound(ServiceAccountError):
    """No such service account, or it is not the caller's to see."""

    status_code = 404
    title = "Not Found"


class ServiceAccountForbidden(ServiceAccountError):
    """The caller may not manage automation identities for this team."""

    status_code = 403
    title = "Forbidden"


class ServiceAccountInvalid(ServiceAccountError):
    """The requested name cannot be an identifier."""

    status_code = 422
    title = "Invalid Name"


class ServiceAccountExists(ServiceAccountError):
    """A service account with this name already exists."""


class ServiceAccountUnowned(ServiceAccountError):
    """Nobody is answerable for this account, so it may not issue new keys."""


def _can_administer_team(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    if actor.is_superuser or actor.role == "super_admin":
        return True
    return actor.team_roles.get(team_id) in {"team_admin", "super_admin"}


def _unusable_password() -> str:
    """A hash nothing can present a password for.

    A real bcrypt hash of a value nobody holds, rather than a sentinel string:
    ``verify_password`` returns False for a malformed hash, so a sentinel would
    also work, but a well-formed hash keeps the login path on its normal
    timing and leaves no shape in the column that a future reader could take
    for a marker to special-case.
    """
    return hash_password(secrets.token_urlsafe(48))


def _address_for(slug: str, team_id: uuid.UUID) -> str:
    """The synthetic address, scoped to the team.

    ``users.email`` is unique across the deployment, so an unscoped name would
    make service-account names a global namespace: one team could not use a
    name another had taken, and the conflict would tell them a team somewhere
    owns something called ``deploy``. Folding the team in keeps names a team's
    own business.
    """
    return f"{slug}.{team_id.hex[:12]}@{SERVICE_ACCOUNT_EMAIL_DOMAIN}"


async def create_service_account(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    team_id: uuid.UUID,
    slug: str,
    display_name: str,
    role: str = "developer",
) -> User:
    """Create an automation identity that belongs to one team.

    The team administrator's call, matching who may issue a key for that team:
    a service account is a credential holder, and creating one is the same act
    as deciding somebody may hold credentials there.

    The caller becomes the steward. Making it implicit rather than asking is
    deliberate: the person creating it is answerable for it until they hand it
    on, and an optional field would be left empty on exactly the accounts
    nobody wants to own.
    """
    if not _can_administer_team(actor, team_id):
        raise ServiceAccountForbidden(
            f"actor may not create service accounts for team {team_id}"
        )
    creator = (
        await session.execute(select(User).where(User.id == actor.id))
    ).scalar_one_or_none()
    if creator is None or creator.is_service_account:
        # The steward is set from the creator below, so an automation identity
        # creating another would build the chain of accounts vouching for each
        # other that the whole stewardship idea exists to prevent. Nothing can
        # reach here holding a service account's session today; this is what
        # keeps that true if something ever can.
        raise ServiceAccountForbidden(
            "an automation identity cannot create another"
        )
    if not _SLUG_PATTERN.match(slug):
        raise ServiceAccountInvalid(
            "name must be lowercase letters, digits and hyphens, 3 to 64 "
            "characters, starting and ending with a letter or digit"
        )
    if role not in {"viewer", "developer", "team_admin"}:
        raise ServiceAccountInvalid(f"unknown role: {role!r}")

    team = (
        await session.execute(select(Team.id).where(Team.id == team_id))
    ).scalar_one_or_none()
    if team is None:
        raise ServiceAccountNotFound(f"team {team_id} not found")

    account = User(
        email=_address_for(slug, team_id),
        hashed_password=_unusable_password(),
        full_name=display_name.strip() or slug,
        is_active=True,
        is_superuser=False,
        is_verified=True,
        is_service_account=True,
        managed_by_user_id=actor.id,
    )
    session.add(account)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        # Deliberately not naming what it collided with beyond the team the
        # caller already administers: the caller knows their own names, and
        # anything more would answer a question about somebody else's.
        raise ServiceAccountExists(
            "a service account with that name already exists in this team"
        ) from exc

    # The membership is what gives the account its reach, exactly as it does
    # for a person. Reusing it is the reason this identity lives in ``users``.
    session.add(Membership(user_id=account.id, team_id=team_id, role=role))
    await session.commit()
    await session.refresh(account)
    log.info(
        "service_account_created",
        service_account_id=str(account.id),
        team_id=str(team_id),
        role=role,
        actor_id=str(actor.id),
    )
    return account


async def list_service_accounts(
    session: AsyncSession, actor: CurrentUser, *, team_id: uuid.UUID
) -> list[User]:
    """The team's automation identities, newest first."""
    if not _can_administer_team(actor, team_id):
        raise ServiceAccountNotFound(f"team {team_id} not found")
    rows = (
        await session.execute(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.team_id == team_id,
                User.is_service_account.is_(True),
            )
            .order_by(User.created_at.desc())
        )
    ).scalars()
    return list(rows)


async def assign_steward(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    service_account_id: uuid.UUID,
    steward_user_id: uuid.UUID,
) -> User:
    """Hand an account to somebody who will answer for it.

    Succession rather than transfer of the credentials themselves: the keys do
    not change, which is the whole point. What changes is whose name is against
    them, and whether the account may issue more.
    """
    account = await _load_manageable(session, actor, service_account_id)

    steward = (
        await session.execute(select(User).where(User.id == steward_user_id))
    ).scalar_one_or_none()
    if steward is None or steward.is_service_account or not steward.is_active:
        # A service account cannot vouch for another, and neither can somebody
        # who has left: both would leave the credential unattended while
        # looking attended, which is worse than being plainly unowned.
        raise ServiceAccountInvalid("the steward must be an active person")
    if not await _shares_a_team_with(session, steward.id, account.id):
        # Somebody outside the team is a name that makes the check pass rather
        # than a person who could be asked about this credential. Without this,
        # an administrator facing "no new keys until somebody owns it" clears
        # it by naming a colleague who never learns they were named.
        raise ServiceAccountInvalid(
            "the steward must be a member of this account's team"
        )

    account.managed_by_user_id = steward.id
    await session.commit()
    await session.refresh(account)
    log.info(
        "service_account_steward_assigned",
        service_account_id=str(account.id),
        steward_user_id=str(steward.id),
        actor_id=str(actor.id),
    )
    return account


async def deactivate_service_account(
    session: AsyncSession, actor: CurrentUser, *, service_account_id: uuid.UUID
) -> User:
    """Stop every key this account holds, in one act.

    The counterpart to the lifetime rule this feature loosened: keys no longer
    stop when a person leaves, so there has to be a deliberate way to stop
    them, and it has to be one action rather than a hunt through the key list.
    """
    account = await _load_manageable(session, actor, service_account_id)
    if not account.is_active:
        return account
    account.is_active = False
    await session.commit()
    await session.refresh(account)
    log.info(
        "service_account_deactivated",
        service_account_id=str(account.id),
        actor_id=str(actor.id),
    )
    return account


async def assert_may_issue_for(
    session: AsyncSession, actor: CurrentUser, service_account_id: uuid.UUID
) -> User:
    """Whether ``actor`` may mint a key that this account will own.

    Refused when the account has no steward. Existing keys keep working, which
    is the contract; what stops is handing an unattended identity more
    credentials, and the refusal is what prompts somebody to take it over.
    """
    account = await _load_manageable(session, actor, service_account_id)
    if not account.is_active:
        raise ServiceAccountNotFound(f"service account {service_account_id} not found")
    if account.managed_by_user_id is None:
        raise ServiceAccountUnowned(
            "nobody is answerable for this service account; assign a steward "
            "before issuing another key for it"
        )
    steward_active = (
        await session.execute(
            select(func.count())
            .select_from(User)
            .where(
                User.id == account.managed_by_user_id,
                User.is_active.is_(True),
            )
        )
    ).scalar_one()
    if not steward_active:
        raise ServiceAccountUnowned(
            "the person answerable for this service account is no longer "
            "active; assign a steward before issuing another key for it"
        )
    if not await _shares_a_team_with(session, account.managed_by_user_id, account.id):
        # Re-checked at issuance, not only when the steward was assigned.
        # Somebody moved off the team is no longer a person who could be asked
        # about this credential, and the gate is worth nothing if it only holds
        # at the moment it was set.
        raise ServiceAccountUnowned(
            "the person answerable for this service account has left its team; "
            "assign a steward before issuing another key for it"
        )
    return account


async def _shares_a_team_with(
    session: AsyncSession, user_id: uuid.UUID, account_id: uuid.UUID
) -> bool:
    """Whether the person belongs to a team the account belongs to."""
    account_teams = select(Membership.team_id).where(Membership.user_id == account_id)
    shared = (
        await session.execute(
            select(Membership.team_id)
            .where(
                Membership.user_id == user_id,
                Membership.team_id.in_(account_teams),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return shared is not None


async def _load_manageable(
    session: AsyncSession, actor: CurrentUser, service_account_id: uuid.UUID
) -> User:
    """Load a service account the caller administers, or 404.

    Existence-hide throughout: a caller outside the account's team learns
    nothing about whether the id is real.
    """
    account = (
        await session.execute(
            select(User).where(
                User.id == service_account_id,
                User.is_service_account.is_(True),
            )
        )
    ).scalar_one_or_none()
    if account is None:
        raise ServiceAccountNotFound(f"service account {service_account_id} not found")

    if actor.is_superuser or actor.role == "super_admin":
        # Answered before the team lookup, so an account that has somehow lost
        # its membership is still reachable. ``any([])`` is False, and an
        # account nobody can reach is one whose keys nobody can stop.
        return account

    team_ids = (
        (
            await session.execute(
                select(Membership.team_id).where(Membership.user_id == account.id)
            )
        )
        .scalars()
        .all()
    )
    if not any(_can_administer_team(actor, team_id) for team_id in team_ids):
        raise ServiceAccountNotFound(f"service account {service_account_id} not found")
    return account
