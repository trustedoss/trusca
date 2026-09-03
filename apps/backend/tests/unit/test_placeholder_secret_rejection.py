# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The shipped placeholder secrets must not be usable outside dev.

`.env.example` carried `change-this-to-a-random-secret-key-min-32-chars` as
`SECRET_KEY`. At 47 characters it cleared the length floor, which was the only
check, and the no-clone installation path tells an operator to `curl` that file
down as their `.env`. Every deployment that took the quick path and did not
edit the file was signing its JWTs with a key published in a public repository,
so anyone could mint a token for any of them. Nothing was visibly wrong: the
portal came up, tokens verified, and the only symptom was that they verified
for other people too.

The value is read out of `.env.example` rather than repeated here. A test that
hardcodes the string keeps passing when someone edits the file to a different
weak value, which is the one change most likely to reintroduce this.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.config import (
    _DEV_PLACEHOLDER_SECRET,
    _MIN_SECRET_LEN,
    _PLACEHOLDER_SECRET_MARKERS,
    api_key_hmac_secret,
    placeholder_secret_reason,
    secret_key,
)

# The value `.env.example` used to ship. Kept here because the reader tests
# below are about how a template value is handled, which is a different
# question from what the file currently contains; that one is asserted against
# the file itself, above.
_A_PUBLISHED_TEMPLATE = "change-this-to-a-random-secret-key-min-32-chars"


def _env_example_path() -> Path | None:
    """`.env.example` at the checkout root, or None when only the backend is here.

    Walking up rather than counting `parents[n]`: the first version of this
    counted one level short, every test that reads the file skipped, and the
    run still reported green. A guard test that silently does not run is worth
    less than no guard test, because it also stops anyone from writing one.
    """
    for candidate in Path(__file__).resolve().parents:
        env_example = candidate / ".env.example"
        if env_example.is_file():
            return env_example
    return None


def _env_example_value(key: str) -> str:
    env_example = _env_example_path()
    if env_example is None:
        pytest.skip(".env.example not found above this file (backend-only checkout)")
    pattern = re.compile(rf"^{re.escape(key)}=(.*)$", re.MULTILINE)
    match = pattern.search(env_example.read_text(encoding="utf-8"))
    if match is None:
        pytest.skip(f"{key} is not set in .env.example")
    return match.group(1).strip()


def test_the_env_example_lookup_actually_finds_the_file() -> None:
    """Fail loudly here rather than skipping every test that needs the file.

    CI runs pytest from `apps/backend` inside a full checkout, so the file is
    always reachable and a miss means the lookup broke, not that the file is
    genuinely absent.
    """
    assert _env_example_path() is not None, (
        "could not locate .env.example above this test; the tests that read it "
        "would all skip, and the guard would be gone without anything failing"
    )


# ---------------------------------------------------------------------------
# The shipped file, whatever it currently says
# ---------------------------------------------------------------------------


def test_env_example_ships_no_usable_secret_key() -> None:
    """`.env.example` must ship either nothing, or something that is refused.

    It ships the dev-only placeholder. Empty would be tidier, but the dev
    compose file substitutes its own `change-me-in-dev-only` for an empty
    value, and that is 21 characters, which fails the length floor and breaks
    local bring-up. Shipping the string dev would have been handed anyway costs
    nothing there and is refused everywhere else. Both shapes are accepted here
    so either choice stays open; a usable-looking value is not.
    """
    shipped = _env_example_value("SECRET_KEY")
    if not shipped:
        return
    assert placeholder_secret_reason(shipped) is not None, (
        f"the SECRET_KEY in .env.example ({shipped!r}) would be accepted in "
        "production; it is published, so it must not be"
    )


def test_the_shipped_value_clears_the_length_floor_dev_bring_up_needs() -> None:
    """Whatever ships must still let a dev stack start.

    `docker-compose.dev.yml` resolves `${SECRET_KEY:-change-me-in-dev-only}`,
    and compose applies that default to an EMPTY value as well as an unset one.
    The fallback is 21 characters, under the floor, so an empty value here does
    not reach the dev placeholder path in `secret_key()`; it reaches the length
    check and the backend refuses to boot. Caught by CI's quickstart gate the
    first time this file shipped an empty value.
    """
    shipped = _env_example_value("SECRET_KEY")
    if not shipped:
        pytest.skip("ships empty; the dev compose fallback governs, see the docstring")
    assert len(shipped) >= _MIN_SECRET_LEN


def test_env_example_does_not_pin_app_env() -> None:
    """The guard must not be switched off by the file it is guarding.

    `docker-compose.yml` has no `env_file:`; every variable in it is
    interpolated from `./.env`, and the documented no-clone install copies
    `.env.example` there verbatim. While this file pinned `APP_ENV=dev`, that
    copy overrode `${APP_ENV:-prod}` and a production stack ran in dev mode,
    where this whole check, the dedicated API-key secret requirement, the
    encryption-key fail-closed check and CORS strictness are all skipped. The
    guard was correct code on a branch nothing reached.
    """
    env_example = _env_example_path()
    if env_example is None:
        pytest.skip(".env.example not found above this file")
    for line in env_example.read_text(encoding="utf-8").splitlines():
        assert not line.strip().startswith("APP_ENV="), (
            "`.env.example` sets APP_ENV, which overrides every compose file's "
            "own default once it is copied to .env; leave it commented out so "
            "the production compose file resolves to prod"
        )


def test_install_sh_and_the_backend_agree_on_what_a_placeholder_is() -> None:
    """One vocabulary, two copies, so they are asserted equal (hardening rule 2).

    `scripts/install.sh` decides whether to preserve an existing SECRET_KEY or
    regenerate it. It runs on the host before any container exists, so it
    cannot import `core.config` and keeps its own copy of the marker list. When
    the copies drift, the script preserves a value the backend will then refuse
    to start on, and the operator gets a crash loop instead of a generated key.
    """
    for candidate in Path(__file__).resolve().parents:
        install_sh = candidate / "scripts" / "install.sh"
        if install_sh.is_file():
            break
    else:
        pytest.skip("scripts/install.sh not found above this file")

    block = re.search(
        r"PLACEHOLDER_SECRET_MARKERS = \((.*?)\)", install_sh.read_text(encoding="utf-8"), re.S
    )
    assert block is not None, "install.sh no longer declares PLACEHOLDER_SECRET_MARKERS"
    script_markers = set(re.findall(r'"([^"]+)"', block.group(1)))

    backend_markers = set(_PLACEHOLDER_SECRET_MARKERS)
    assert script_markers == backend_markers, (
        "install.sh and core.config disagree about which values are "
        f"placeholders: only in script {sorted(script_markers - backend_markers)}, "
        f"only in backend {sorted(backend_markers - script_markers)}"
    )


def test_the_dev_placeholder_is_refused_outside_dev() -> None:
    """The value dev hands out unprompted must not survive a copy into prod."""
    assert placeholder_secret_reason(_DEV_PLACEHOLDER_SECRET) is not None


# ---------------------------------------------------------------------------
# What must still be accepted. A check that rejects real keys is worse than
# no check: it teaches people to weaken a key until it is taken.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "why"),
    [
        # `openssl rand -hex 32`, which is what every installation guide says
        # to run. Lowercase hex only: two character classes, so any rule based
        # on counting classes would reject it.
        ("a3f1c09e7b24d85f6e0a1b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e", "hex"),
        # `secrets.token_urlsafe(32)` shape: mixed case, digits, - and _.
        ("Qx7-Zk2LmNp4Rt8Vw1Yb3Ce6Df9Gh0Jk5Ln2Mp7Rs4Tv1Xz8A", "urlsafe"),
        # Base64 with padding.
        ("MTIzNDU2Nzg5MGFiY2RlZmdoaWprbG1ub3BxcnN0dXZ3eHl6QUJD", "base64"),
    ],
)
def test_generated_keys_are_accepted(value: str, why: str) -> None:
    assert (
        placeholder_secret_reason(value) is None
    ), f"a {why} key from the documented generator was refused"


# ---------------------------------------------------------------------------
# The shapes that are long without being random
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "label"),
    [
        ("change-this-to-a-random-secret-key-min-32-chars", "the shipped template"),
        ("CHANGE-THIS-TO-A-RANDOM-SECRET-KEY-MIN-32-CHARS", "the template, shouted"),
        ("please-replace-me-with-something-random-1234", "replace-me"),
        ("your-secret-key-goes-right-here-abcdefghij", "your-secret"),
        ("this-is-only-an-example-key-do-not-ship-it", "example"),
        ("insecure-development-key-not-for-production", "insecure"),
        ("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "a row of x"),
        ("abababababababababababababababababababab", "two characters"),
        ("abcdefghij" * 5, "one block repeated"),
    ],
)
def test_values_that_are_long_but_not_random_are_refused(value: str, label: str) -> None:
    assert len(value) >= _MIN_SECRET_LEN, "fixture must clear the length floor"
    assert (
        placeholder_secret_reason(value) is not None
    ), f"{label} would be accepted as production key material"


# ---------------------------------------------------------------------------
# The env-var readers, which is where the check actually bites
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["prod", "staging"])
def test_secret_key_refuses_the_template_in_every_non_dev_env(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    """Staging counts. A staging instance issues tokens for real accounts."""
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("SECRET_KEY", _A_PUBLISHED_TEMPLATE)

    with pytest.raises(RuntimeError, match="not a usable secret"):
        secret_key()


def test_secret_key_accepts_a_generated_key_in_prod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = "a3f1c09e7b24d85f6e0a1b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e"
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("SECRET_KEY", generated)

    assert secret_key() == generated


def test_dev_still_boots_on_the_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local bring-up must not need a generated key; that is what dev is for."""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("SECRET_KEY", _A_PUBLISHED_TEMPLATE)

    assert secret_key() == _A_PUBLISHED_TEMPLATE


def test_api_key_hmac_secret_refuses_the_same_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other signing key reads from the same file by the same route."""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("API_KEY_HMAC_SECRET", _A_PUBLISHED_TEMPLATE)

    with pytest.raises(RuntimeError, match="not a usable secret"):
        api_key_hmac_secret()


def test_the_length_floor_still_applies_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short but otherwise random key keeps its own, clearer error."""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("SECRET_KEY", "a3f1c09e7b24d85f")

    with pytest.raises(RuntimeError, match="at least 32 characters"):
        secret_key()
