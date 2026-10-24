"""What the auth routes answer after the JWT and bcrypt libraries moved (#574).

Two contracts the unit tests cannot see end to end:

* A password hash written by the old passlib code, sitting in ``users``,
  still signs the user in through ``POST /auth/login``.
* Every way a bearer token can fail (expired, missing a required claim, the
  wrong algorithm, the wrong key) produces the same 401 Problem response at
  ``GET /auth/me`` as plain garbage. No caller distinguished them before, and
  a difference would tell a caller which check it tripped.
"""

from __future__ import annotations

import json
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration

PROBLEM_JSON = "application/problem+json"
_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "auth" / "passlib_bcrypt_hashes.json"


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from main import app

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _register(client: AsyncClient) -> tuple[str, str]:
    email = f"swap-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "password": f"Sup3rS3cret!{secrets.token_hex(4)}",
            "full_name": "Swap",
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return email, str(resp.json()["id"])


async def test_passlib_hash_in_the_database_still_signs_in(client, db_factory) -> None:
    from core.ratelimit import limiter

    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    for entry in fixture["hashes"]:
        if entry["name"] in {"ascii_2b", "over_72_bytes_multibyte", "ident_2y"}:
            # Three users, six sign-ins: more than one IP's per-minute budget,
            # which is not what this test is about.
            limiter.reset()
            email, _user_id = await _register(client)
            async with db_factory() as session:
                await session.execute(
                    text("UPDATE users SET hashed_password = :h WHERE email = :e"),
                    {"h": entry["hash"], "e": email.lower()},
                )
                await session.commit()

            ok = await client.post(
                "/auth/login", json={"email": email, "password": entry["password"]}
            )
            assert ok.status_code == 200, (entry["name"], ok.text)
            assert ok.json()["access_token"]

            bad = await client.post(
                "/auth/login", json={"email": email, "password": entry["password"] + "!"}
            )
            # Over 72 bytes an extra character is past what bcrypt reads, so
            # it still matches; that is the old behaviour and the fixture's.
            expected = 200 if entry["name"] == "over_72_bytes_multibyte" else 401
            assert bad.status_code == expected, (entry["name"], bad.text)


def _claims(sub: str, **overrides: object) -> dict[str, object]:
    now = int(time.time())
    claims: dict[str, object] = {
        "sub": sub,
        "type": "access",
        "iat": now,
        "exp": now + 300,
        "jti": uuid.uuid4().hex,
    }
    for key, value in overrides.items():
        if value is None:
            claims.pop(key)
        else:
            claims[key] = value
    return claims


@pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")
async def test_every_bad_token_gets_the_same_401(client) -> None:
    from core.config import secret_key

    _email, user_id = await _register(client)
    key = secret_key()

    control = jwt.encode(_claims(user_id), key, algorithm="HS256")
    ok = await client.get("/auth/me", headers={"Authorization": f"Bearer {control}"})
    assert ok.status_code == 200, ok.text  # the construction below is valid

    now = int(time.time())
    bad_tokens = {
        "expired": jwt.encode(_claims(user_id, exp=now - 5, iat=now - 60), key, algorithm="HS256"),
        "missing-exp": jwt.encode(_claims(user_id, exp=None), key, algorithm="HS256"),
        "missing-iat": jwt.encode(_claims(user_id, iat=None), key, algorithm="HS256"),
        "hs512": jwt.encode(_claims(user_id), key, algorithm="HS512"),
        "alg-none": jwt.encode(_claims(user_id), None, algorithm="none"),
        "wrong-key": jwt.encode(
            _claims(user_id), "not-the-real-secret-key-but-long-enough", algorithm="HS256"
        ),
        "refresh-type": jwt.encode(_claims(user_id, type="refresh"), key, algorithm="HS256"),
        "garbage": "not.a.jwt",
    }

    bodies = {}
    for name, token in bad_tokens.items():
        resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401, (name, resp.text)
        assert resp.headers["content-type"].startswith(PROBLEM_JSON), name
        body = resp.json()
        bodies[name] = (body["type"], body["title"], body["detail"])

    assert len(set(bodies.values())) == 1, bodies
