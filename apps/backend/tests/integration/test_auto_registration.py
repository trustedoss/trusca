"""
Whether somebody who can authenticate becomes a user (N4 × N6).

The pair the plan names: single sign-on decides who may prove who they are,
and this decides whether proving it creates an account. They are separate
questions and a deployment answers them separately, which is easy to lose
because one code path performs both.

Off by default, and only for the deployment's own provider. On a portal
pointed at a company directory everybody in the company can authenticate and
only some of them are meant to have an account; on the hosted providers,
creating the account is what a signup is.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from integrations.oauth.base import OAuthUserInfo
from models import Membership, User
from services.oauth_service import (
    OAuthCallbackFailed,
    _grade_for,
    _resolve_or_create_user,
)
from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


def _info(provider: str, *, groups: tuple[str, ...] = ()) -> OAuthUserInfo:
    suffix = uuid.uuid4().hex[:10]
    return OAuthUserInfo(
        provider=provider,  # type: ignore[arg-type]
        provider_user_id=f"sub-{suffix}",
        email=f"arriving-{suffix}@example.com",
        full_name="Arriving Person",
        avatar_url=None,
        groups=groups,
        email_can_link_existing_account=True,
    )


# ---------------------------------------------------------------------------
# Whether an account appears
# ---------------------------------------------------------------------------


async def test_an_unknown_person_is_refused_when_nobody_asked_for_them(
    client, monkeypatch
) -> None:
    """The default. Nothing is created and the sign-in fails."""
    monkeypatch.delenv("AUTH_AUTO_REGISTER", raising=False)
    info = _info("oidc")
    factory = await _factory(client)

    async with factory() as session:
        with pytest.raises(OAuthCallbackFailed):
            await _resolve_or_create_user(session, info=info)
        await session.rollback()

        created = (
            await session.execute(select(User).where(User.email == info.email))
        ).scalar_one_or_none()

    assert created is None


async def test_the_refusal_says_no_more_than_any_other_failed_sign_in(
    client, monkeypatch
) -> None:
    """Otherwise it confirms which addresses in the directory are registered.

    Anyone in the company can reach the provider, so a message distinguishing
    "no account" from any other failure turns the login page into a lookup for
    the whole staff list.
    """
    monkeypatch.delenv("AUTH_AUTO_REGISTER", raising=False)
    factory = await _factory(client)

    async with factory() as session:
        with pytest.raises(OAuthCallbackFailed) as refused:
            await _resolve_or_create_user(session, info=_info("oidc"))
        await session.rollback()

    assert "this account cannot sign in" in str(refused.value)


async def test_turning_it_on_admits_them(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_AUTO_REGISTER", "true")
    info = _info("oidc")
    factory = await _factory(client)

    async with factory() as session:
        user, identity = await _resolve_or_create_user(session, info=info)
        await session.commit()

    assert user.email == info.email
    assert identity.provider == "oidc"


async def test_somebody_who_already_has_an_account_still_signs_in(
    client, monkeypatch
) -> None:
    """The setting governs creation, not authentication.

    Reading it as a login gate would lock out everybody an administrator had
    already added, which is the whole roster on a deployment that turned this
    off on purpose.
    """
    monkeypatch.setenv("AUTH_AUTO_REGISTER", "true")
    info = _info("oidc")
    factory = await _factory(client)
    async with factory() as session:
        await _resolve_or_create_user(session, info=info)
        await session.commit()

    monkeypatch.setenv("AUTH_AUTO_REGISTER", "false")
    async with factory() as session:
        user, _identity = await _resolve_or_create_user(session, info=info)
        await session.commit()

    assert user.email == info.email


async def test_the_hosted_providers_are_not_gated_by_it(client, monkeypatch) -> None:
    """Creating the account is what a signup is.

    Gating them would turn the demo signup page into a dead end, and the
    deployments that need this setting are not the ones using these providers.
    """
    monkeypatch.setenv("AUTH_AUTO_REGISTER", "false")
    info = _info("github")
    factory = await _factory(client)

    async with factory() as session:
        user, _identity = await _resolve_or_create_user(session, info=info)
        await session.commit()

    assert user.email == info.email


# ---------------------------------------------------------------------------
# What they may do once admitted (the second half of the pair)
# ---------------------------------------------------------------------------


async def test_an_admitted_person_gets_the_deployments_chosen_grade(
    client, monkeypatch
) -> None:
    """The grade an auto-registered person carries is the deployment's setting.

    The same setting a bulk import consults. Two paths deciding this
    separately is how the same new employee ends up with one grade when an
    administrator adds them and another when they sign in first.
    """
    monkeypatch.setenv("AUTH_AUTO_REGISTER", "true")
    monkeypatch.setenv("DEFAULT_MEMBER_ROLE", "viewer")
    monkeypatch.delenv("OIDC_GROUP_ROLE_MAP", raising=False)
    info = _info("oidc")
    factory = await _factory(client)

    async with factory() as session:
        user, _identity = await _resolve_or_create_user(session, info=info)
        await session.commit()
        membership = (
            await session.execute(
                select(Membership).where(Membership.user_id == user.id)
            )
        ).scalar_one()

    assert membership.role == "viewer"


def test_a_mapped_group_still_beats_the_setting(monkeypatch) -> None:
    """The mapping is the more specific statement.

    A deployment that took the trouble to say what a group means has said
    something about these people in particular; the default is what it says
    about everybody else.
    """
    monkeypatch.setenv("DEFAULT_MEMBER_ROLE", "viewer")
    monkeypatch.setenv("OIDC_GROUP_ROLE_MAP", "platform:team_admin")

    assert _grade_for(_info("oidc", groups=("platform",))) == "team_admin"


def test_the_floor_still_wins_over_the_setting_for_an_unmapped_person(
    monkeypatch,
) -> None:
    """A7's rule is not weakened by this setting.

    On a deployment that mapped its groups, matching none of them is an
    answer, and the answer is the floor. Letting the default override it would
    quietly raise the grade of everybody the mapping does not name.
    """
    monkeypatch.setenv("DEFAULT_MEMBER_ROLE", "team_admin")
    monkeypatch.setenv("OIDC_GROUP_ROLE_MAP", "platform:team_admin")

    assert _grade_for(_info("oidc", groups=("finance",))) == "viewer"


def test_the_hosted_signup_grade_is_untouched_by_the_setting(monkeypatch) -> None:
    """The personal team created at signup contains only its owner.

    Administering it grants nothing over anybody, which is why this path keeps
    its historical grade rather than following a setting written for a company
    directory.
    """
    monkeypatch.setenv("DEFAULT_MEMBER_ROLE", "viewer")
    monkeypatch.delenv("OIDC_GROUP_ROLE_MAP", raising=False)

    assert _grade_for(_info("github")) == "team_admin"


# ---------------------------------------------------------------------------
# The other door
#
# Auto-registration off closes the provider path. It closes nothing on its own
# while anybody can create an account at the sign-up form and then link the
# provider identity to it, which is the sequence below.
# ---------------------------------------------------------------------------


async def test_closing_only_the_provider_leaves_the_sign_up_form_open(
    client, monkeypatch
) -> None:
    """The gap the two settings exist to close together.

    Sign up under a work address, then sign in through the company provider:
    the callback links the identity to the account somebody made for
    themselves, and they hold what the provider gate was withholding.
    """
    monkeypatch.setenv("AUTH_AUTO_REGISTER", "false")
    monkeypatch.setenv("AUTH_SELF_REGISTRATION", "true")
    info = _info("oidc")

    registered = await client.post(
        "/auth/register",
        json={"email": info.email, "password": "correct-horse-battery-staple"},
    )

    factory = await _factory(client)
    async with factory() as session:
        user, identity = await _resolve_or_create_user(session, info=info)
        await session.commit()

    assert registered.status_code == 201, registered.text
    assert user.email == info.email
    assert identity.provider == "oidc"


async def test_closing_both_closes_the_roster(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_AUTO_REGISTER", "false")
    monkeypatch.setenv("AUTH_SELF_REGISTRATION", "false")
    info = _info("oidc")

    registered = await client.post(
        "/auth/register",
        json={"email": info.email, "password": "correct-horse-battery-staple"},
    )

    factory = await _factory(client)
    async with factory() as session:
        with pytest.raises(OAuthCallbackFailed):
            await _resolve_or_create_user(session, info=info)
        await session.rollback()
        created = (
            await session.execute(select(User).where(User.email == info.email))
        ).scalar_one_or_none()

    assert registered.status_code == 404, registered.text
    assert created is None


async def test_the_sign_up_form_is_open_unless_a_deployment_closes_it(
    client, monkeypatch
) -> None:
    """The hosted signup is what this form is for, so the default stays on."""
    monkeypatch.delenv("AUTH_SELF_REGISTRATION", raising=False)

    response = await client.post(
        "/auth/register",
        json={
            "email": f"open-{uuid.uuid4().hex[:10]}@example.com",
            "password": "correct-horse-battery-staple",
        },
    )

    assert response.status_code == 201, response.text
