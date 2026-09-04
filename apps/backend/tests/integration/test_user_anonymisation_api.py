# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The anonymisation routes over HTTP, asserted by asking again afterwards.

Every test here makes a request and then makes a SECOND, independent request
to see whether the first one lasted. That is the whole point. A route that
opens a request, flushes, and returns 201 with a real id looks correct from
the response alone, and the row is gone the moment the session closes:
``get_db`` yields a session and closes it without committing, so anything the
service only flushed is rolled back.

The first version of this feature had exactly that defect, and every existing
test missed it because they all called the service functions directly and
committed the session themselves. So these go through the app.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
from tests._db_required import migrate_to_head
from tests._helpers import make_user

PROBLEM_JSON = "application/problem+json"

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
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


def _bearer_for(user: User) -> dict[str, str]:
    role = "super_admin" if user.is_superuser else None
    return {"Authorization": f"Bearer {create_access_token(subject=str(user.id), role=role)}"}


async def _three_people(client: AsyncClient):
    """A subject and two super admins, committed before the app sees them."""
    factory = await _factory(client)
    async with factory() as session:
        subject = await make_user(session)
        requester = await make_user(session, is_superuser=True)
        approver = await make_user(session, is_superuser=True)
        await session.commit()
        return subject, requester, approver


async def test_an_opened_request_is_still_there_on_the_next_call(
    client: AsyncClient,
) -> None:
    """201 with an id is not evidence. Asking again is."""
    subject, requester, approver = await _three_people(client)

    created = await client.post(
        "/v1/user-anonymisation",
        json={"subject_user_id": str(subject.id), "reason": "data subject request"},
        headers=_bearer_for(requester),
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]

    # A second attempt must now conflict. If the first was rolled back this
    # succeeds instead, which is how the defect presented: two 201s in a row
    # for something that is supposed to exist at most once.
    again = await client.post(
        "/v1/user-anonymisation",
        json={"subject_user_id": str(subject.id)},
        headers=_bearer_for(approver),
    )
    assert again.status_code == 409, (
        "opening a second request for the same subject succeeded, so the first "
        f"one was never persisted: {again.text}"
    )
    assert again.headers["content-type"].startswith(PROBLEM_JSON)
    assert again.json()["reason"] == "request_already_open"

    approved = await client.post(
        f"/v1/user-anonymisation/{request_id}/approval",
        headers=_bearer_for(approver),
    )
    assert approved.status_code == 200, approved.text

    listed = await client.get(
        "/v1/user-anonymisation/awaiting-execution",
        headers=_bearer_for(requester),
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["count"] >= 1
    mine = [i for i in body["items"] if i["request_id"] == request_id]
    assert mine, (
        "the approved request is not on the awaiting-execution list, so the "
        "admin panel would report nothing outstanding while an erasure waits"
    )
    assert mine[0]["subject_user_id"] == str(subject.id)


async def test_cancelling_lasts_too(client: AsyncClient) -> None:
    """The mirror case: a cancel that rolls back leaves the subject blocked."""
    subject, requester, approver = await _three_people(client)

    created = await client.post(
        "/v1/user-anonymisation",
        json={"subject_user_id": str(subject.id)},
        headers=_bearer_for(requester),
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]

    cancelled = await client.delete(
        f"/v1/user-anonymisation/{request_id}", headers=_bearer_for(requester)
    )
    assert cancelled.status_code == 200, cancelled.text

    # Cancelling frees the subject. If the cancel did not stick, this 409s.
    reopened = await client.post(
        "/v1/user-anonymisation",
        json={"subject_user_id": str(subject.id)},
        headers=_bearer_for(approver),
    )
    assert reopened.status_code == 201, (
        f"the cancel did not persist, so the subject stays blocked: {reopened.text}"
    )


async def test_the_approver_must_be_someone_else(client: AsyncClient) -> None:
    """Enforced over HTTP, not only in the service."""
    subject, requester, _approver = await _three_people(client)

    created = await client.post(
        "/v1/user-anonymisation",
        json={"subject_user_id": str(subject.id)},
        headers=_bearer_for(requester),
    )
    assert created.status_code == 201, created.text

    self_approved = await client.post(
        f"/v1/user-anonymisation/{created.json()['id']}/approval",
        headers=_bearer_for(requester),
    )
    assert self_approved.status_code == 409, self_approved.text

    listed = await client.get(
        "/v1/user-anonymisation/awaiting-execution", headers=_bearer_for(requester)
    )
    assert not [
        i for i in listed.json()["items"] if i["request_id"] == created.json()["id"]
    ], "a self-approved request reached the execution backlog"


async def test_a_lower_grade_sees_404_not_403(client: AsyncClient) -> None:
    """Existence hiding: a developer must not learn a request exists.

    404 rather than 403 on every route, so the response cannot be used to
    discover that a given user is the subject of one.
    """
    subject, requester, _ = await _three_people(client)
    factory = await _factory(client)
    async with factory() as session:
        nobody = await make_user(session)
        await session.commit()

    created = await client.post(
        "/v1/user-anonymisation",
        json={"subject_user_id": str(subject.id)},
        headers=_bearer_for(requester),
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]

    for method, path in (
        ("post", "/v1/user-anonymisation"),
        ("post", f"/v1/user-anonymisation/{request_id}/approval"),
        ("delete", f"/v1/user-anonymisation/{request_id}"),
        ("get", "/v1/user-anonymisation/awaiting-execution"),
    ):
        call = getattr(client, method)
        kwargs = {"headers": _bearer_for(nobody)}
        if method == "post" and path.endswith("user-anonymisation"):
            kwargs["json"] = {"subject_user_id": str(subject.id)}
        response = await call(path, **kwargs)
        assert response.status_code == 404, (
            f"{method.upper()} {path} answered {response.status_code} to a "
            "non-super-admin; 403 would confirm the route, and on this route "
            "confirming it leaks that somebody is being anonymised"
        )


async def test_the_self_export_is_keyed_off_the_token(client: AsyncClient) -> None:
    """It returns the caller's own data and offers no way to name anyone else."""
    subject, requester, _ = await _three_people(client)

    mine = await client.get("/v1/users/me/export", headers=_bearer_for(subject))
    assert mine.status_code == 200, mine.text
    payload = mine.json()
    assert payload["account"]["id"] == str(subject.id)
    assert payload["account"]["email"] == subject.email

    # Nothing that is a credential, and nothing that belongs to somebody else.
    assert "hashed_password" not in payload["account"]
    body = mine.text
    assert requester.email not in body

    # Truncation is declared rather than implied.
    assert payload["activity"]["truncated"] is False
    assert payload["activity"]["total"] == payload["activity"]["included"]

    theirs = await client.get("/v1/users/me/export", headers=_bearer_for(requester))
    assert theirs.json()["account"]["id"] == str(requester.id)


async def test_each_refusal_carries_its_own_reason_token(
    client: AsyncClient,
) -> None:
    """Three refusals share 409 and ask for three unrelated things.

    Open a fresh request; find a different approver; nothing, it is already
    gone. ``detail`` is English and always will be, so the token is what lets
    a Korean UI say the right sentence. A single token for all three would
    make the distinction unavailable to any client, which is what the code
    said it was avoiding while doing exactly that.
    """
    subject, requester, approver = await _three_people(client)

    missing = await client.post(
        f"/v1/user-anonymisation/{uuid.uuid4()}/approval",
        headers=_bearer_for(requester),
    )
    assert missing.status_code == 409
    assert missing.headers["content-type"].startswith(PROBLEM_JSON)
    assert missing.json()["reason"] == "request_not_found"

    created = await client.post(
        "/v1/user-anonymisation",
        json={"subject_user_id": str(subject.id)},
        headers=_bearer_for(requester),
    )
    assert created.status_code == 201, created.text

    mine = await client.post(
        f"/v1/user-anonymisation/{created.json()['id']}/approval",
        headers=_bearer_for(requester),
    )
    assert mine.status_code == 409
    assert mine.json()["reason"] == "self_approval"

    duplicate = await client.post(
        "/v1/user-anonymisation",
        json={"subject_user_id": str(subject.id)},
        headers=_bearer_for(approver),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["reason"] == "request_already_open"

    tokens = {
        missing.json()["reason"],
        mine.json()["reason"],
        duplicate.json()["reason"],
    }
    assert len(tokens) == 3, f"three different refusals collapsed into {tokens}"


async def test_a_request_written_straight_to_the_database_is_refused(
    client: AsyncClient,
) -> None:
    """The forged-approval path, reproduced rather than reasoned about.

    ``user_anonymisation_requests`` has to be writable by the application: that
    is how the two-person flow works. So anything that reaches SQL through the
    portal can insert a row that says ``approved``, name two real super admins
    on it, and put a person who asked for nothing on the operator's backlog. It
    is indistinguishable there from a real one.

    Measured before the check existed: the insert succeeded and the row was
    listed. What separates it is the audit trail. A request opened and approved
    through the API leaves a create and an update against its id; a conjured
    one leaves neither, and the operator command refuses on that basis.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import insert

    from models.user_anonymisation_request import (
        ANONYMISATION_APPROVED,
        UserAnonymisationRequest,
    )
    from scripts.anonymise_user import anonymise
    from services.user_anonymisation_service import corroborating_audit_actions

    subject, requester, approver = await _three_people(client)
    factory = await _factory(client)

    forged_id = uuid.uuid4()
    async with factory() as session:
        # Straight in through Core, bypassing the ORM unit of work and
        # therefore the audit listener, the way a SQL-execution foothold
        # would. Going through the service would defeat the point: the service
        # is what leaves the audit trail this test is about.
        await session.execute(
            insert(UserAnonymisationRequest).values(
                id=forged_id,
                subject_user_id=subject.id,
                requested_by_user_id=requester.id,
                approved_by_user_id=approver.id,
                state=ANONYMISATION_APPROVED,
                expires_at=datetime.now(UTC) + timedelta(days=7),
                approved_at=datetime.now(UTC),
            )
        )
        await session.commit()

        actions = await corroborating_audit_actions(session, request_id=forged_id)
        assert actions == set(), (
            "the direct insert produced audit rows, so this test is not "
            f"reproducing the forged path: {actions}"
        )

        with pytest.raises(SystemExit) as refused:
            await anonymise(session, subject_id=subject.id)
        assert "no audit record" in str(refused.value)
        await session.rollback()

        still_there = await session.get(User, subject.id)
        assert still_there is not None
        assert still_there.email == subject.email, (
            "a forged approval erased somebody who asked for nothing"
        )


async def test_the_backlog_names_both_parties(client: AsyncClient) -> None:
    """An operator needs people to check with, not just a subject id."""
    subject, requester, approver = await _three_people(client)

    created = await client.post(
        "/v1/user-anonymisation",
        json={"subject_user_id": str(subject.id)},
        headers=_bearer_for(requester),
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    approval = await client.post(
        f"/v1/user-anonymisation/{request_id}/approval",
        headers=_bearer_for(approver),
    )
    assert approval.status_code == 200, approval.text

    listed = await client.get(
        "/v1/user-anonymisation/awaiting-execution", headers=_bearer_for(requester)
    )
    entry = next(i for i in listed.json()["items"] if i["request_id"] == request_id)
    assert entry["requested_by_user_id"] == str(requester.id)
    assert entry["approved_by_user_id"] == str(approver.id)
