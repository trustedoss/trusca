# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""`.env.example` must not ship a working credential.

The installation guide tells operators to `curl` this file down as their
`.env`, so anything assigned in it is a value real deployments run with. Two
had escaped: `SECRET_KEY`, which signed JWTs on every deployment that took the
quick path (fixed separately, and refused at startup now), and
`DEMO_SUPER_ADMIN_PASSWORD`, which carried the password this project publishes
for its public demo in a file whose own comment said never to set it on a
production stack.

This is a shape check, not a secret scanner. It asks whether a key whose name
says credential carries an assigned value, which is the property that made both
of those reachable. Commented lines are fine: they document without configuring.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

#: Keys whose name says the value is a credential.
_CREDENTIAL_NAME = re.compile(r"(PASSWORD|PASSWD|SECRET|TOKEN|CREDENTIAL|PRIVATE_KEY|API_KEY)")

#: Assignments that are not credentials despite matching the name pattern:
#: durations, toggles, sizes, and the env-var names of other settings.
_NOT_A_VALUE = re.compile(r"^(true|false|\d+(\.\d+)?|https?://\S+)$", re.IGNORECASE)

#: Explicitly allowed assignments, each with the reason it is not a credential.
_ALLOWED: dict[str, str] = {
    # Refused at startup outside dev (core.config.placeholder_secret_reason),
    # and the value dev is handed anyway when the variable is unset.
    "SECRET_KEY": "dev-only placeholder, refused in every other environment",
}


def _env_example() -> Path:
    for candidate in Path(__file__).resolve().parents:
        env_example = candidate / ".env.example"
        if env_example.is_file():
            return env_example
    pytest.skip(".env.example not found above this file (backend-only checkout)")
    raise AssertionError("unreachable")


def test_the_lookup_finds_the_file() -> None:
    """Fail loudly rather than skipping the check that matters."""
    assert _env_example().is_file()


def test_no_credential_key_ships_an_assigned_value() -> None:
    offenders: list[str] = []
    for number, line in enumerate(_env_example().read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        # A commented key documents without configuring anything.
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        value = value.strip().strip("\"'")
        if not value or not _CREDENTIAL_NAME.search(key):
            continue
        if _NOT_A_VALUE.match(value) or key in _ALLOWED:
            continue
        offenders.append(f"{key} on line {number}")

    assert not offenders, (
        "`.env.example` assigns a credential: "
        + ", ".join(offenders)
        + ". The installation guide tells operators to copy this file to .env, "
        "so whatever is assigned here is what deployments run with. Comment the "
        "line out, or add it to _ALLOWED with the reason it is safe."
    )


# ---------------------------------------------------------------------------
# The reference page describes where the demo password comes from
# ---------------------------------------------------------------------------
#
# It said the default was "(auto-generated)" and that the variable was required
# under `staging` / `prod`. Both were wrong in the direction that matters: the
# default is a fixed value published with the public demo, and seeding refuses
# to run under those environments rather than requiring anything. An operator
# reading it would have concluded that leaving the variable unset produced a
# random password.


def _seed_demo_default() -> str:
    from scripts.seed_demo import _DEV_DEMO_PASSWORD_DEFAULT

    return _DEV_DEMO_PASSWORD_DEFAULT


def test_seeding_refuses_outside_dev_and_demo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The environments the reference page called "required" are refused."""
    from scripts.seed_demo import _resolve_demo_password

    monkeypatch.delenv("DEMO_SUPER_ADMIN_PASSWORD", raising=False)
    for env in ("prod", "staging"):
        monkeypatch.setenv("APP_ENV", env)
        with pytest.raises(RuntimeError):
            _resolve_demo_password()


def test_unset_yields_the_published_default_not_a_random_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two calls agree, which is what makes the value published rather than generated."""
    from scripts.seed_demo import _resolve_demo_password

    monkeypatch.delenv("DEMO_SUPER_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("APP_ENV", "demo")

    assert _resolve_demo_password() == _resolve_demo_password() == _seed_demo_default()


def test_the_reference_page_does_not_call_the_default_generated() -> None:
    """Guard the wording, because the wrong wording is the reassurance."""
    for candidate in Path(__file__).resolve().parents:
        reference = candidate / "docs-site" / "docs" / "reference" / "env-variables.md"
        if reference.is_file():
            break
    else:
        pytest.skip("env-variables.md not found above this file")

    row = next(
        line
        for line in reference.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `DEMO_SUPER_ADMIN_PASSWORD`")
    )
    assert "auto-generated" not in row, (
        "the reference page calls the demo password default auto-generated; it "
        f"is the fixed {_seed_demo_default()!r} that ships with the public demo"
    )
    assert "published" in row, (
        "the reference page must say the default is published, so a reader "
        "does not take an unset variable for a generated password"
    )
