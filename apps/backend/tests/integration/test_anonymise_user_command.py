# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The operator command actually runs, and actually finishes (ER32).

CLAUDE.md hardening rule 6 exists because two backfill tasks reported work
they had not committed, and neither had a test that executed them. This file
runs ``scripts.anonymise_user.anonymise`` end to end against the database and
asserts on what is in the tables afterwards, not on what the function returned.

Marking the request ``executed`` is asserted as its own thing. If that step is
lost, every erasure still works and nothing looks broken: the request simply
stays on the awaiting-execution list forever, so the one screen that shows
outstanding obligations fills with completed work and stops being read.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import Membership, Team, User
from models.user_anonymisation_request import (
    ANONYMISATION_EXECUTED,
    UserAnonymisationRequest,
)
from scripts.anonymise_user import anonymise
from services.user_anonymisation_service import (
    RequestConflict,
    approve,
    list_awaiting_execution,
    open_request,
)
from tests._helpers import make_user

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping anonymisation command tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        pytest.skip(
            "alembic upgrade head failed; anonymisation command tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """A session with the audit listeners installed, as the app's sessions are.

    Without them, requests these tests create leave no audit rows, and the
    operator command refuses because a request with no audit trail is exactly
    what a forged one looks like. A fixture that skipped this would be testing
    a world the application does not run in.
    """
    from core.audit import install_audit_listeners

    url = _require_database_url()
    engine = create_async_engine(url, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)
    async with factory() as s:
        yield s
    await engine.dispose()


#: One personal and one shared organisation for the whole module, created on
#: first use. Each test still makes its own teams and users; only the two
#: organisations are shared, and nothing here asserts anything about them.
#:
#: Per-test organisations were the first version and they interfered with
#: ``test_admin_teams_api``, which lists organisations with ``page_size=200``
#: and picks its own out of page one. Enough tests adding a row each pushed
#: the row it wanted off that page. Tests that leave the shared database
#: measurably different are tests that break other tests.
_ORG_CACHE: dict[bool, uuid.UUID] = {}


async def _organisation_id(session: AsyncSession, *, personal: bool) -> uuid.UUID:
    """The module's personal or shared organisation, made once.

    ``is_personal`` is what the command reads to decide whether a team is one
    person's or a group's, so the two kinds have to be genuinely different.
    """
    cached = _ORG_CACHE.get(personal)
    if cached is not None:
        return cached

    org_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO organizations (id, name, slug, is_personal) "
            "VALUES (:id, :name, :slug, :personal)"
        ),
        {
            "id": org_id,
            # Unique: organizations.name carries a unique constraint.
            "name": f"{'Personal' if personal else 'Shared'} org {org_id.hex[:8]}",
            "slug": f"org-{org_id.hex[:8]}",
            "personal": personal,
        },
    )
    await session.commit()
    _ORG_CACHE[personal] = org_id
    return org_id


async def _approved_subject(session: AsyncSession) -> tuple[uuid.UUID, str, uuid.UUID]:
    """A subject with a personal team, an audit row, and an approved request."""
    subject = await make_user(session, full_name="Kim Example")
    requester = await make_user(session, is_superuser=True)
    approver = await make_user(session, is_superuser=True)
    await session.commit()
    subject_id, subject_email = subject.id, subject.email

    team = Team(
        organization_id=await _organisation_id(session, personal=True),
        name=f"{subject_email}'s Team",
        slug=f"t-{uuid.uuid4().hex[:8]}",
        description=f"Personal team for {subject_email}",
    )
    session.add(team)
    await session.flush()
    session.add(Membership(user_id=subject_id, team_id=team.id, role="team_admin"))
    await session.execute(
        text(
            "INSERT INTO audit_logs (actor_user_id, action, target_table, target_id, "
            "diff, ip, user_agent) VALUES (:a, 'update', 'users', :t, "
            "CAST(:d AS jsonb), '198.51.100.4', 'curl/8')"
        ),
        {"a": subject_id, "t": str(subject_id), "d": '{"email": {"old": "x@y.z"}}'},
    )
    await session.commit()

    row = await open_request(
        session, subject_user_id=subject_id, requested_by_user_id=requester.id
    )
    await approve(session, request_id=row.id, approved_by_user_id=approver.id)
    await session.commit()
    return subject_id, subject_email, team.id


async def test_the_command_erases_and_marks_the_request_executed(
    session: AsyncSession,
) -> None:
    subject_id, subject_email, team_id = await _approved_subject(session)

    approved_moment = datetime.now(UTC)
    waiting_before = await list_awaiting_execution(session)
    assert any(item.subject_user_id == subject_id for item in waiting_before), (
        "the approved request is not on the awaiting-execution list, so the "
        "admin screen would never show that this erasure is outstanding"
    )

    counts = await anonymise(session, subject_id=subject_id)

    user = await session.get(User, subject_id)
    assert user is not None, "the account must survive: audit rows reference it"
    assert user.email != subject_email
    assert user.email.endswith("@invalid")
    assert user.full_name is None
    assert user.is_active is False

    # Not decoration. ``password_changed_at`` is what makes an access token
    # minted before this moment be refused; deleting the refresh tokens only
    # stops NEW ones being issued. A first version of this command assigned
    # hashed_password directly and left this untouched, so a token already in
    # a browser kept working against the anonymised account for the rest of
    # its half hour.
    assert user.password_changed_at is not None
    assert user.password_changed_at >= approved_moment, (
        "the password rotation did not go through set_password, so access "
        "tokens minted before the erasure are still accepted"
    )

    team = await session.get(Team, team_id)
    assert team is not None
    assert subject_email not in (team.name or "")
    assert subject_email not in (team.description or "")
    assert counts["teams_renamed"] == 1

    ip, user_agent = (
        await session.execute(
            text(
                "SELECT ip::text, user_agent FROM audit_logs "
                "WHERE actor_user_id = :a LIMIT 1"
            ),
            {"a": subject_id},
        )
    ).one()
    assert ip is None and user_agent is None
    assert counts["audit_rows_scrubbed"] >= 1

    request = (
        await session.execute(
            select(UserAnonymisationRequest).where(
                UserAnonymisationRequest.subject_user_id == subject_id
            )
        )
    ).scalar_one()
    assert request.state == ANONYMISATION_EXECUTED, (
        "the command did the erasure but left the request unfinished; it would "
        "stay on the awaiting-execution list forever"
    )
    assert request.executed_at is not None

    waiting_after = await list_awaiting_execution(session)
    assert not any(item.subject_user_id == subject_id for item in waiting_after), (
        "the completed erasure is still listed as outstanding"
    )


async def test_the_command_refuses_without_an_approved_request(
    session: AsyncSession,
) -> None:
    """One super admin's intention is not authority to erase somebody.

    The database refuses this too. Both checks are deliberate: this one gives
    the operator a sentence they can act on, and the database's holds even if
    a future caller forgets to ask.
    """
    subject = await make_user(session)
    requester = await make_user(session, is_superuser=True)
    await session.commit()
    subject_id, requester_id = subject.id, requester.id

    with pytest.raises(SystemExit) as no_request:
        await anonymise(session, subject_id=subject_id)
    assert "no approved anonymisation request" in str(no_request.value)
    await session.rollback()

    await open_request(
        session, subject_user_id=subject_id, requested_by_user_id=requester_id
    )
    await session.commit()

    with pytest.raises(SystemExit) as pending_only:
        await anonymise(session, subject_id=subject_id)
    assert "no approved anonymisation request" in str(pending_only.value)
    await session.rollback()

    still_there = await session.get(User, subject_id)
    assert still_there is not None
    assert still_there.is_active is True, "a refused run still changed the account"


async def test_a_shared_team_the_subject_belongs_to_is_left_alone(
    session: AsyncSession,
) -> None:
    """The hard case, and the one the rename is deliberately narrow for.

    The subject is a MEMBER of this team and their address is in its
    description, so a rename keyed on "their team, mentions them" would rewrite
    it. It must not. Other people navigate by that name; an erasure about one
    person is not licence to rewrite a record a group depends on. What the
    command keys on instead is ``organizations.is_personal``, which says whose
    the team is rather than who is mentioned in it.

    The consequence is that the address survives in a shared team's
    description. That is a real residue, and it belongs in the processing
    procedure where somebody can decide about it, not in a silent rewrite.
    """
    subject_id, subject_email, _ = await _approved_subject(session)

    shared = Team(
        organization_id=await _organisation_id(session, personal=False),
        name="Platform",
        slug=f"t-{uuid.uuid4().hex[:8]}",
        description=f"Ask {subject_email} about the build",
    )
    session.add(shared)
    await session.flush()
    session.add(Membership(user_id=subject_id, team_id=shared.id, role="developer"))
    await session.commit()
    shared_id = shared.id

    await anonymise(session, subject_id=subject_id)

    after = await session.get(Team, shared_id)
    assert after is not None
    assert after.name == "Platform", "a shared team was renamed by an erasure"
    assert after.description is not None and subject_email in after.description, (
        "the shared team's description was rewritten; the rename must follow "
        "organizations.is_personal, not a text match on the subject"
    )


async def test_an_expired_request_cannot_be_approved(session: AsyncSession) -> None:
    """The window is real, and approving past it moves the row to expired.

    Without this the TTL would be decoration: the column would say expired and
    the approval would go through anyway.
    """
    subject = await make_user(session)
    requester = await make_user(session, is_superuser=True)
    approver = await make_user(session, is_superuser=True)
    await session.commit()
    subject_id, requester_id, approver_id = subject.id, requester.id, approver.id

    row = await open_request(
        session,
        subject_user_id=subject_id,
        requested_by_user_id=requester_id,
        now=datetime.now(UTC) - timedelta(days=30),
    )
    await session.commit()

    from services.user_anonymisation_service import NotApprovable

    with pytest.raises(NotApprovable):
        await approve(session, request_id=row.id, approved_by_user_id=approver_id)
    await session.commit()

    refreshed = await session.get(UserAnonymisationRequest, row.id)
    assert refreshed is not None
    assert refreshed.state == "expired"
    assert refreshed.approved_by_user_id is None


async def test_a_stale_request_does_not_lock_the_subject_out(
    session: AsyncSession,
) -> None:
    """Open, let it lapse, open again. The sequence, not the single action.

    A pending request past its window still occupies the partial unique index
    that allows one open request per subject. Nothing could approve it, and
    without expiry nothing could replace it either, so a subject who was once
    the object of a request that nobody decided would be permanently
    un-erasable. Testing "open" and "expire" separately would not find this;
    only the sequence does.
    """
    subject = await make_user(session)
    first = await make_user(session, is_superuser=True)
    second = await make_user(session, is_superuser=True)
    third = await make_user(session, is_superuser=True)
    await session.commit()
    subject_id = subject.id
    first_id, second_id, third_id = first.id, second.id, third.id

    stale = await open_request(
        session,
        subject_user_id=subject_id,
        requested_by_user_id=first_id,
        now=datetime.now(UTC) - timedelta(days=30),
    )
    await session.commit()
    stale_id = stale.id

    # While it is still live, a second request is refused. That is the index
    # doing its job, and the assertion below only means something because of
    # it.
    with pytest.raises(RequestConflict):
        await open_request(
            session, subject_user_id=subject_id, requested_by_user_id=second_id,
            now=datetime.now(UTC) - timedelta(days=29),
        )
    await session.rollback()

    fresh = await open_request(
        session, subject_user_id=subject_id, requested_by_user_id=second_id
    )
    await session.commit()

    retired = await session.get(UserAnonymisationRequest, stale_id)
    assert retired is not None
    assert retired.state == "expired", (
        "the lapsed request is still pending, so it keeps the index and the "
        "subject can never be the object of another request"
    )

    approved = await approve(
        session, request_id=fresh.id, approved_by_user_id=third_id
    )
    await session.commit()
    assert approved.state == "approved"


async def test_an_approved_request_is_never_expired_out_from_under_it(
    session: AsyncSession,
) -> None:
    """Approval is a commitment, and it does not lapse on its own.

    Expiring it would make the awaiting-execution list shrink by itself, which
    is precisely the appearance of the work being done.
    """
    subject = await make_user(session)
    requester = await make_user(session, is_superuser=True)
    approver = await make_user(session, is_superuser=True)
    other = await make_user(session, is_superuser=True)
    await session.commit()
    subject_id = subject.id
    requester_id, approver_id, other_id = requester.id, approver.id, other.id

    long_ago = datetime.now(UTC) - timedelta(days=90)
    row = await open_request(
        session,
        subject_user_id=subject_id,
        requested_by_user_id=requester_id,
        now=long_ago,
    )
    await approve(
        session, request_id=row.id, approved_by_user_id=approver_id, now=long_ago
    )
    await session.commit()
    row_id = row.id

    # Opening another request for the same subject runs the expiry sweep.
    with pytest.raises(RequestConflict):
        await open_request(
            session, subject_user_id=subject_id, requested_by_user_id=other_id
        )
    await session.rollback()

    still_owed = await session.get(UserAnonymisationRequest, row_id)
    assert still_owed is not None
    assert still_owed.state == "approved", (
        "an approved erasure aged out of the backlog by itself"
    )
    assert any(
        item.request_id == row_id for item in await list_awaiting_execution(session)
    )


async def test_no_reader_ever_presents_a_lapsed_request_as_live(
    session: AsyncSession,
) -> None:
    """Expiry is applied lazily, so this asks what the readers see meanwhile.

    A lapsed request keeps the state ``pending`` in the table until somebody
    opens another request for that subject. That is only safe while nothing
    shows it to a person as awaiting a decision: an operator who read it as
    live would either be refused at the worst moment or, far worse, would
    succeed and make the window meaningless.

    So this pins both halves. The one action that touches a pending row
    applies the window itself and refuses, recording the true reason. And the
    list the admin screen renders never contains it, because that list is
    about approved work and a lapsed request was never approved.
    """
    subject = await make_user(session)
    requester = await make_user(session, is_superuser=True)
    approver = await make_user(session, is_superuser=True)
    await session.commit()
    subject_id, requester_id, approver_id = subject.id, requester.id, approver.id

    row = await open_request(
        session,
        subject_user_id=subject_id,
        requested_by_user_id=requester_id,
        now=datetime.now(UTC) - timedelta(days=30),
    )
    await session.commit()
    row_id = row.id

    from services.user_anonymisation_service import NotApprovable

    assert not any(
        item.request_id == row_id for item in await list_awaiting_execution(session)
    ), "a request nobody ever approved appeared on the awaiting-execution list"

    with pytest.raises(NotApprovable):
        await approve(session, request_id=row_id, approved_by_user_id=approver_id)
    await session.commit()

    settled = await session.get(UserAnonymisationRequest, row_id)
    assert settled is not None
    assert settled.state == "expired", (
        "approving a lapsed request refused but left it pending, so the row "
        "still claims to be awaiting a decision"
    )
    assert settled.approved_by_user_id is None

    # And the operator command cannot reach it either: it asks for an
    # approved request, and this one never was.
    with pytest.raises(SystemExit):
        await anonymise(session, subject_id=subject_id)
    await session.rollback()


async def test_an_anonymised_account_cannot_be_authenticated_by_any_route(
    session: AsyncSession,
) -> None:
    """The account row survives on purpose. It must not survive as a login.

    Audit rows reference the user, and that reference is what keeps the trail
    saying who rather than somebody, so the row stays. A row that stays and
    can still be signed into is a different thing entirely: the person was
    told their data was erased, and the account is still a way in.

    Three routes, tried rather than reasoned about. Password, because that is
    what ``set_password`` was handed. Reset, because the address it would mail
    is the one being removed. OAuth, because a provider can hand back the same
    address tomorrow and the linking rule decides what that means.
    """
    from services.auth_service import authenticate
    from services.password_reset_service import request_password_reset

    subject = await make_user(session)
    requester = await make_user(session, is_superuser=True)
    approver = await make_user(session, is_superuser=True)
    await session.commit()
    subject_id, subject_email = subject.id, subject.email
    old_hash = subject.hashed_password

    row = await open_request(
        session, subject_user_id=subject_id, requested_by_user_id=requester.id
    )
    await approve(session, request_id=row.id, approved_by_user_id=approver.id)
    await session.commit()

    await anonymise(session, subject_id=subject_id)

    after = await session.get(User, subject_id)
    assert after is not None
    assert after.hashed_password != old_hash, (
        "the password hash is unchanged, so whoever knew the old password "
        "still knows this account's password"
    )

    # 1. Password. The address no longer resolves to this row at all, and the
    #    row is inactive besides.
    assert await authenticate(session, email=subject_email, password="anything") is None
    assert (
        await authenticate(session, email=after.email, password="anything") is None
    ), "the tombstone address authenticated"

    # 2. Reset. ``matched`` false means no token was issued and no mail was
    #    enqueued; a true here would mean a reset link addressed to a person
    #    whose address this system just promised to forget.
    outcome = await request_password_reset(session, email=subject_email)
    assert outcome["matched"] is False
    await session.commit()

    # 3. OAuth. The identities are gone, so a returning provider cannot find
    #    this row by identity, and it cannot find it by address either because
    #    the address it would match on is no longer stored.
    identities = (
        await session.execute(
            text("SELECT count(*) FROM oauth_identities WHERE user_id = :u"),
            {"u": subject_id},
        )
    ).scalar_one()
    assert identities == 0
    by_old_email = (
        await session.execute(
            select(User).where(User.email == subject_email)
        )
    ).scalar_one_or_none()
    assert by_old_email is None, (
        "the old address still resolves to a user, so an OAuth sign-in with "
        "that address would link straight back into the anonymised account"
    )


async def test_the_erasure_records_itself_without_recording_the_person(
    session: AsyncSession,
) -> None:
    """An irreversible act leaves a row in the one table that cannot be edited.

    Two things have to hold at once and they pull against each other. The run
    must be accounted for, and the account must not contain what the run was
    erasing. So the row carries ids and counts: which request authorised it,
    who asked, who agreed, how many rows each area gave up. No address, no
    name, no old values.

    The address assertion is deliberately weak about what it proves, and the
    weakness is worth stating. It holds today because
    ``core.audit._changed_columns`` records only the NEW value of a changed
    column, so renaming a team away from "<address>'s Team" writes
    ``{"name": "Personal team (...)"}`` and never the old name. Measured: with
    the rename going through the ORM, no audit row in the window contains the
    address. If that diff builder ever started recording old values, this
    would go red, which is the regression worth holding. It does NOT prove the
    erasure is free of personal data in general.

    The window starts after the setup on purpose. The setup creates a team
    named after the subject, and creates ARE audited with the full row, so a
    wider window would fail on the fixture rather than on the code.
    """
    subject_id, subject_email, _team_id = await _approved_subject(session)

    before = (
        await session.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'anonymise'")
        )
    ).scalar_one()

    # The database's clock, not the test process's: audit_logs.created_at is
    # written by Postgres, and comparing it against a locally computed instant
    # would drift. The window has to start here because the setup above
    # created a team named after the subject, and that create was audited with
    # the address in it. This test is about what the ERASURE writes.
    mark = (await session.execute(text("SELECT now()"))).scalar_one()

    await anonymise(session, subject_id=subject_id)

    rows = (
        await session.execute(
            text(
                "SELECT actor_user_id, target_id, diff::text FROM audit_logs "
                "WHERE action = 'anonymise' AND target_id = :t"
            ),
            {"t": str(subject_id)},
        )
    ).all()
    assert len(rows) == 1, (
        "the erasure left no audit row, so the only account of an "
        "irreversible act is a log line and a mutable state column"
    )
    actor, target, diff = rows[0]
    assert actor is None, "the actor is an operator at a shell, not a portal user"
    assert target == str(subject_id)
    assert "request_id" in diff and "counts" in diff

    after = (
        await session.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'anonymise'")
        )
    ).scalar_one()
    assert after == before + 1

    # The address must not have been reintroduced anywhere by the run itself.
    leaked = (
        await session.execute(
            text(
                "SELECT count(*) FROM audit_logs "
                "WHERE diff::text LIKE :needle AND created_at >= :mark"
            ),
            {"needle": f"%{subject_email}%", "mark": mark},
        )
    ).scalar_one()
    assert leaked == 0, (
        "erasing the account wrote the address back into the immutable audit "
        "trail; check whether the ORM listener is installed on this path"
    )


async def test_a_guest_in_someone_elses_personal_team_does_not_rename_it(
    session: AsyncSession,
) -> None:
    """The narrow path left open by scoping on ``is_personal`` alone.

    ``create_team`` refuses to place a team under a personal organisation, but
    ``add_team_member`` has no equivalent check, so a super admin can put one
    person into another's personal team. Anonymising the guest would then
    rename the host's team and blank its description: the host loses the name
    of their own workspace because somebody else was erased.
    """
    host = await make_user(session)
    guest = await make_user(session)
    requester = await make_user(session, is_superuser=True)
    approver = await make_user(session, is_superuser=True)
    await session.commit()
    host_id, guest_id = host.id, guest.id

    team = Team(
        organization_id=await _organisation_id(session, personal=True),
        name="Personal team (host)",
        slug=f"t-{uuid.uuid4().hex[:8]}",
        description="the host's own workspace",
    )
    session.add(team)
    await session.flush()
    session.add(Membership(user_id=host_id, team_id=team.id, role="team_admin"))
    session.add(Membership(user_id=guest_id, team_id=team.id, role="developer"))
    await session.commit()
    team_id = team.id

    row = await open_request(
        session, subject_user_id=guest_id, requested_by_user_id=requester.id
    )
    await approve(session, request_id=row.id, approved_by_user_id=approver.id)
    await session.commit()

    counts = await anonymise(session, subject_id=guest_id)

    after = await session.get(Team, team_id)
    assert after is not None
    assert after.name == "Personal team (host)", (
        "erasing a guest renamed the host's personal team"
    )
    assert after.description == "the host's own workspace"
    assert counts["teams_renamed"] == 0


async def test_an_executed_request_does_not_block_a_later_one(
    session: AsyncSession,
) -> None:
    """The restore path, which the procedure documentation now promises.

    A backup taken before an erasure brings the account back AND brings the
    request row back as ``executed``, so re-running the command against it is
    refused. The operator's way forward is a fresh request, and that only
    works if an executed row does not hold the subject's slot in the partial
    unique index. It does not, because the index covers ``pending`` and
    ``approved`` only. Asserted rather than read off the predicate: this is
    the step an operator takes on a day that has already gone wrong.
    """
    subject_id, _email, _team = await _approved_subject(session)
    await anonymise(session, subject_id=subject_id)

    executed = (
        await session.execute(
            select(UserAnonymisationRequest).where(
                UserAnonymisationRequest.subject_user_id == subject_id
            )
        )
    ).scalars().all()
    assert [r.state for r in executed] == [ANONYMISATION_EXECUTED]

    requester = await make_user(session, is_superuser=True)
    approver = await make_user(session, is_superuser=True)
    await session.commit()

    again = await open_request(
        session, subject_user_id=subject_id, requested_by_user_id=requester.id
    )
    await session.commit()
    approved = await approve(
        session, request_id=again.id, approved_by_user_id=approver.id
    )
    await session.commit()
    assert approved.state == "approved", (
        "after a restore the operator cannot open a fresh request, so an "
        "erasure that has to be redone has no route at all"
    )
